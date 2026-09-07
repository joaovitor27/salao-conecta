"""
Serializers públicos para o fluxo de agendamento self-service.

Regras de segurança:
- Nenhum endpoint retorna CPF, e-mail, comissões, salários ou dados financeiros.
- Profissionais são exibidos apenas pelo primeiro nome.
- Somente dados estritamente necessários para o booking são expostos.
"""
import re
import logging
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from psycopg2.extras import DateTimeTZRange
from rest_framework import serializers

from business.models import (
    Salon, ServiceSalon, Employee, EmployeeService,
    Customer, Appointment, AppointmentItem,
)

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  READ-ONLY: Dados públicos do salão
# ──────────────────────────────────────────────────────────────

class PublicSalonSerializer(serializers.ModelSerializer):
    """Branding + informações públicas mínimas para a página de booking."""
    brand_name = serializers.CharField(read_only=True)
    logo_url = serializers.SerializerMethodField()
    address_display = serializers.SerializerMethodField()

    class Meta:
        model = Salon
        fields = (
            'slug',
            'brand_name',
            'tagline',
            'logo_url',
            'primary_color',
            'secondary_color',
            'accent_color',
            'phone_number',
            'address_display',
        )
        read_only_fields = fields

    def get_logo_url(self, obj: Salon) -> str | None:
        if not obj.logo:
            return None
        request = self.context.get('request')
        url = obj.logo.url
        return request.build_absolute_uri(url) if request else url

    @staticmethod
    def get_address_display(obj: Salon) -> str:
        addr = obj.address
        if not addr:
            return ''
        parts = [addr.street]
        if addr.number and addr.number != 'S/N':
            parts.append(addr.number)
        parts.append(addr.neighborhood)
        if addr.city:
            parts.append(f'{addr.city.name} - {addr.city.state.abbreviation}')
        return ', '.join(parts)


# ──────────────────────────────────────────────────────────────
#  READ-ONLY: Serviços ativos do salão
# ──────────────────────────────────────────────────────────────

class PublicServiceSerializer(serializers.ModelSerializer):
    """Serviço com apenas os dados necessários para seleção no booking."""
    service_name = serializers.CharField(source='service.name', read_only=True)
    description = serializers.CharField(source='service.description', read_only=True, default='')
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ServiceSalon
        fields = ('id', 'service_name', 'description', 'price', 'duration_minutes', 'image_url')
        read_only_fields = fields

    def get_image_url(self, obj: ServiceSalon) -> str | None:
        if not obj.image:
            return None
        request = self.context.get('request')
        url = obj.image.url
        return request.build_absolute_uri(url) if request else url


# ──────────────────────────────────────────────────────────────
#  READ-ONLY: Profissionais agendáveis (apenas primeiro nome)
# ──────────────────────────────────────────────────────────────

class PublicProfessionalServiceSerializer(serializers.Serializer):
    """Serviço que o profissional realiza (apenas ID e nome)."""
    service_id = serializers.IntegerField(source='service.id')
    service_name = serializers.CharField(source='service.service.name')


class PublicProfessionalSerializer(serializers.ModelSerializer):
    """Profissional exibido apenas com primeiro nome e serviços vinculados."""
    first_name = serializers.SerializerMethodField()
    services = PublicProfessionalServiceSerializer(
        source='employee_services', many=True, read_only=True
    )

    class Meta:
        model = Employee
        fields = ('id', 'first_name', 'services')
        read_only_fields = fields

    @staticmethod
    def get_first_name(obj: Employee) -> str:
        return obj.full_name.split()[0] if obj.full_name else ''


# ──────────────────────────────────────────────────────────────
#  WRITE: Criação de agendamento público
# ──────────────────────────────────────────────────────────────

class BookingItemSerializer(serializers.Serializer):
    """Um serviço selecionado no booking."""
    service_id = serializers.IntegerField()
    professional_id = serializers.UUIDField(required=False, allow_null=True)


class BookingCreateSerializer(serializers.Serializer):
    """
    Criação de agendamento pelo cliente (self-service).
    Identificação anônima: apenas nome + telefone.
    """
    client_name = serializers.CharField(max_length=200, min_length=2)
    client_phone = serializers.CharField(max_length=20, min_length=8)
    start_time = serializers.DateTimeField()
    services = BookingItemSerializer(many=True, allow_empty=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    def validate_client_phone(self, value: str) -> str:
        digits = re.sub(r'\D', '', value)
        if len(digits) < 10 or len(digits) > 13:
            raise serializers.ValidationError(
                'Informe um número de telefone válido com DDD. Ex: (11) 99999-9999'
            )
        return digits

    def validate_client_name(self, value: str) -> str:
        value = ' '.join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError('Informe seu nome.')
        return value

    def validate_start_time(self, value):
        if value < timezone.now():
            raise serializers.ValidationError('O horário selecionado já passou.')
        return value

    def validate(self, attrs):
        salon: Salon = self.context['salon']
        services_data = attrs['services']

        validated_items = []
        total_price = Decimal('0.00')
        cursor = attrs['start_time']

        prof_time_ranges: dict = {}

        for item_data in services_data:
            # ── Validar serviço ───────────────────────────────
            try:
                service_salon = ServiceSalon.objects.select_related('service').get(
                    id=item_data['service_id'], salon=salon, is_active=True
                )
            except ServiceSalon.DoesNotExist:
                raise serializers.ValidationError(
                    {'services': f"Serviço ID {item_data['service_id']} não encontrado ou indisponível."}
                )

            # ── Validar profissional (opcional) ───────────────
            professional = None
            prof_id = item_data.get('professional_id')
            if prof_id:
                try:
                    professional = Employee.objects.get(
                        id=prof_id, salon=salon, is_active=True, is_schedulable=True
                    )
                except Employee.DoesNotExist:
                    raise serializers.ValidationError(
                        {'services': 'Profissional não encontrado ou indisponível.'}
                    )

                # Verificar se o profissional realiza o serviço
                if not EmployeeService.objects.filter(
                    employee=professional, service=service_salon
                ).exists():
                    raise serializers.ValidationError(
                        {'services': f"O profissional não realiza o serviço '{service_salon.service.name}'."}
                    )

            price = service_salon.price
            duration = service_salon.duration_minutes
            item_start = cursor
            item_end = item_start + timedelta(minutes=duration)
            cursor = item_end

            item_time_range = DateTimeTZRange(lower=item_start, upper=item_end)

            # ── Verificar conflito de agenda ──────────────────
            if professional:
                conflict = Appointment.objects.filter(
                    items__professional=professional,
                    time_range__overlap=item_time_range,
                ).exclude(
                    status=Appointment.Status.CANCELLED,
                ).distinct()

                if conflict.exists():
                    raise serializers.ValidationError(
                        {'start_time': 'O horário selecionado não está mais disponível. Por favor, escolha outro horário.'}
                    )

                # Conflito intra-agendamento
                if professional.id not in prof_time_ranges:
                    prof_time_ranges[professional.id] = []
                for prange in prof_time_ranges[professional.id]:
                    if max(prange[0], item_start) < min(prange[1], item_end):
                        raise serializers.ValidationError(
                            {'start_time': 'Conflito de horários no agendamento.'}
                        )
                prof_time_ranges[professional.id].append((item_start, item_end))

            total_price += price
            validated_items.append({
                'service_salon': service_salon,
                'price': price,
                'duration_minutes': duration,
                'professional': professional,
            })

        time_range = DateTimeTZRange(lower=attrs['start_time'], upper=cursor)

        attrs['_validated_items'] = validated_items
        attrs['_total_price'] = total_price
        attrs['_time_range'] = time_range
        return attrs

    def create(self, validated_data):
        salon: Salon = self.context['salon']
        phone = validated_data['client_phone']
        name = validated_data['client_name']

        # Buscar ou criar cliente por telefone
        customer, created = Customer.objects.get_or_create(
            salon=salon,
            phone=phone,
            defaults={
                'name': name,
                'cpf': '',
                'is_active': True,
            },
        )
        # Se já existe, atualizar o nome caso tenha mudado
        if not created and customer.name != name:
            customer.name = name
            customer.save(update_fields=['name', 'updated_at'])

        appointment = Appointment.objects.create(
            salon=salon,
            client=customer,
            time_range=validated_data['_time_range'],
            total_price=validated_data['_total_price'],
            notes=validated_data.get('notes', ''),
            status=Appointment.Status.PENDING,
        )

        items_to_create = [
            AppointmentItem(
                appointment=appointment,
                service=item['service_salon'],
                price=item['price'],
                duration_minutes=item['duration_minutes'],
                professional=item['professional'],
            )
            for item in validated_data['_validated_items']
        ]
        AppointmentItem.objects.bulk_create(items_to_create)

        logger.info(
            "Booking público criado: appointment=%s | salão=%s | telefone=%s",
            appointment.pk, salon.slug, phone,
        )
        return appointment


# ──────────────────────────────────────────────────────────────
#  READ: Resumo de confirmação do agendamento
# ──────────────────────────────────────────────────────────────

class BookingConfirmationItemSerializer(serializers.Serializer):
    """Serviço no resumo da confirmação."""
    service_name = serializers.CharField()
    professional_name = serializers.CharField(allow_null=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    duration_minutes = serializers.IntegerField()


class BookingConfirmationSerializer(serializers.Serializer):
    """Resumo completo do agendamento para exibição e WhatsApp."""
    appointment_id = serializers.IntegerField()
    salon_name = serializers.CharField()
    salon_phone = serializers.CharField(allow_null=True)
    client_name = serializers.CharField()
    date = serializers.CharField()
    start_time = serializers.CharField()
    end_time = serializers.CharField()
    services = BookingConfirmationItemSerializer(many=True)
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    whatsapp_url = serializers.CharField()

    @staticmethod
    def build_from_appointment(appointment: Appointment) -> dict:
        """Monta o dict de confirmação a partir de um Appointment criado."""
        salon = appointment.salon
        items = appointment.items.select_related('service__service', 'professional').all()

        tz = timezone.get_current_timezone()
        start = timezone.localtime(appointment.time_range.lower, tz)
        end = timezone.localtime(appointment.time_range.upper, tz)

        services_summary = []
        services_text_parts = []
        for item in items:
            sname = item.service.service.name
            pname = item.professional.full_name.split()[0] if item.professional else None
            services_summary.append({
                'service_name': sname,
                'professional_name': pname,
                'price': item.price,
                'duration_minutes': item.duration_minutes,
            })
            part = f'• {sname}'
            if pname:
                part += f' (com {pname})'
            services_text_parts.append(part)

        # Montar mensagem para WhatsApp
        client_name = appointment.client.name if appointment.client else 'Cliente'
        date_str = start.strftime('%d/%m/%Y')
        time_str = f"{start.strftime('%H:%M')} às {end.strftime('%H:%M')}"
        services_text = '\n'.join(services_text_parts)

        whatsapp_message = (
            f"Olá! Fiz um agendamento pelo site. 😊\n\n"
            f"📋 *Resumo do Agendamento*\n"
            f"👤 Nome: {client_name}\n"
            f"📅 Data: {date_str}\n"
            f"🕐 Horário: {time_str}\n"
            f"💇 Serviços:\n{services_text}\n"
            f"💰 Total: R$ {appointment.total_price:.2f}\n\n"
            f"Aguardo confirmação! 🙏"
        )

        # Montar URL do WhatsApp
        salon_phone_digits = re.sub(r'\D', '', salon.phone_number or '')
        if salon_phone_digits and not salon_phone_digits.startswith('55'):
            salon_phone_digits = '55' + salon_phone_digits
        
        import urllib.parse
        whatsapp_url = (
            f"https://wa.me/{salon_phone_digits}?text={urllib.parse.quote(whatsapp_message)}"
            if salon_phone_digits else ''
        )

        return {
            'appointment_id': appointment.pk,
            'salon_name': salon.brand_name,
            'salon_phone': salon.phone_number,
            'client_name': client_name,
            'date': date_str,
            'start_time': start.strftime('%H:%M'),
            'end_time': end.strftime('%H:%M'),
            'services': services_summary,
            'total_price': appointment.total_price,
            'whatsapp_url': whatsapp_url,
        }
