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
            'require_professional_choice',
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


class BookingSessionSerializer(serializers.Serializer):
    """Uma sessão de agendamento (data/hora + serviços daquela data)."""
    start_time = serializers.DateTimeField()
    services = BookingItemSerializer(many=True, allow_empty=False)

    def validate_start_time(self, value):
        if value < timezone.now():
            raise serializers.ValidationError('O horário selecionado já passou.')
        return value


def _schedulable_professionals_for(salon: Salon, service_salon: ServiceSalon) -> list[Employee]:
    """Profissionais ativos e agendáveis que realizam o serviço informado."""
    return list(
        Employee.objects.filter(
            salon=salon,
            is_active=True,
            is_schedulable=True,
            employee_services__service=service_salon,
        ).distinct().order_by('full_name')
    )


def _professional_busy_in(
    professional: Employee,
    start,
    end,
    extra_ranges: list[tuple] | None = None,
) -> bool:
    """
    Verifica se o profissional já tem atendimento sobreposto ao segmento [start, end).
    Calcula o intervalo exato de cada item existente para conciliar múltiplos profissionais
    em sequência no mesmo agendamento.
    """
    window = DateTimeTZRange(lower=start, upper=end)
    appts = (
        Appointment.objects.filter(
            items__professional=professional,
            time_range__overlap=window,
        )
        .exclude(status=Appointment.Status.CANCELLED)
        .prefetch_related('items')
        .distinct()
    )

    for appt in appts:
        if not appt.time_range or not appt.time_range.lower:
            continue
        items = list(appt.items.all())
        cursor = appt.time_range.lower
        for item in items:
            item_end = cursor + timedelta(minutes=item.duration_minutes)
            if item.professional_id == professional.id and max(cursor, start) < min(item_end, end):
                return True
            cursor = item_end

    if extra_ranges:
        for r_start, r_end in extra_ranges:
            if max(r_start, start) < min(r_end, end):
                return True

    return False


class BookingCreateSerializer(serializers.Serializer):
    """
    Criação de agendamento pelo cliente (self-service).
    Identificação anônima: apenas nome + telefone.
    Aceita um único atendimento (`start_time` + `services`) ou várias datas (`sessions`).
    """
    client_name = serializers.CharField(max_length=200, min_length=2)
    client_phone = serializers.CharField(max_length=20, min_length=8)
    start_time = serializers.DateTimeField(required=False)
    services = BookingItemSerializer(many=True, allow_empty=False, required=False)
    sessions = BookingSessionSerializer(many=True, allow_empty=False, required=False)
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

    def _validate_session(
        self,
        salon: Salon,
        start_time,
        services_data: list[dict],
        prof_time_ranges: dict,
    ) -> dict:
        validated_items = []
        total_price = Decimal('0.00')
        cursor = start_time

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

            capable = _schedulable_professionals_for(salon, service_salon)
            prof_id = item_data.get('professional_id')
            professional: Employee | None = None

            if prof_id:
                try:
                    professional = Employee.objects.get(
                        id=prof_id, salon=salon, is_active=True, is_schedulable=True
                    )
                except Employee.DoesNotExist:
                    raise serializers.ValidationError(
                        {'services': 'Profissional não encontrado ou indisponível.'}
                    )

                if not EmployeeService.objects.filter(
                    employee=professional, service=service_salon
                ).exists():
                    raise serializers.ValidationError(
                        {'services': f"O profissional não realiza o serviço '{service_salon.service.name}'."}
                    )
            elif len(capable) == 1:
                # Apenas 1 profissional realiza o serviço: predefinido automaticamente
                professional = capable[0]
            elif salon.require_professional_choice and len(capable) > 1:
                raise serializers.ValidationError(
                    {'services': f"Selecione o profissional para o serviço '{service_salon.service.name}'."}
                )

            price = service_salon.price
            duration = service_salon.duration_minutes
            item_start = cursor
            item_end = item_start + timedelta(minutes=duration)
            cursor = item_end

            # ── Verificar conflito de agenda ──────────────────
            if professional:
                prior = prof_time_ranges.get(professional.id, [])
                if _professional_busy_in(professional, item_start, item_end, prior):
                    raise serializers.ValidationError(
                        {'start_time': 'O horário selecionado não está mais disponível. Por favor, escolha outro horário.'}
                    )
                prof_time_ranges.setdefault(professional.id, []).append((item_start, item_end))

            total_price += price
            validated_items.append({
                'service_salon': service_salon,
                'price': price,
                'duration_minutes': duration,
                'professional': professional,
            })

        return {
            'items': validated_items,
            'total_price': total_price,
            'time_range': DateTimeTZRange(lower=start_time, upper=cursor),
        }

    def validate(self, attrs):
        salon: Salon = self.context['salon']
        raw_sessions = attrs.get('sessions')

        if raw_sessions:
            sessions_input = [
                {'start_time': s['start_time'], 'services': s['services']}
                for s in raw_sessions
            ]
        else:
            if 'start_time' not in attrs:
                raise serializers.ValidationError({'start_time': 'Este campo é obrigatório.'})
            if not attrs.get('services'):
                raise serializers.ValidationError({'services': 'Selecione ao menos um serviço.'})
            sessions_input = [
                {'start_time': attrs['start_time'], 'services': attrs['services']}
            ]

        prof_time_ranges: dict = {}
        validated_sessions = [
            self._validate_session(salon, s['start_time'], s['services'], prof_time_ranges)
            for s in sessions_input
        ]

        attrs['_validated_sessions'] = validated_sessions
        return attrs

    def create(self, validated_data):
        salon: Salon = self.context['salon']
        phone = validated_data['client_phone']
        name = validated_data['client_name']

        customer, created = Customer.objects.get_or_create(
            salon=salon,
            phone=phone,
            defaults={
                'name': name,
                'cpf': '',
                'is_active': True,
            },
        )
        if not created and customer.name != name:
            customer.name = name
            customer.save(update_fields=['name', 'updated_at'])

        appointments: list[Appointment] = []
        for session in validated_data['_validated_sessions']:
            appointment = Appointment.objects.create(
                salon=salon,
                client=customer,
                time_range=session['time_range'],
                total_price=session['total_price'],
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
                for item in session['items']
            ]
            AppointmentItem.objects.bulk_create(items_to_create)
            appointments.append(appointment)

            logger.info(
                "Booking público criado: appointment=%s | salão=%s | telefone=%s",
                appointment.pk, salon.slug, phone,
            )

        return appointments


# ──────────────────────────────────────────────────────────────
#  READ: Resumo de confirmação do agendamento
# ──────────────────────────────────────────────────────────────

class BookingConfirmationItemSerializer(serializers.Serializer):
    """Serviço no resumo da confirmação."""
    service_name = serializers.CharField()
    professional_name = serializers.CharField(allow_null=True)
    price = serializers.DecimalField(max_digits=10, decimal_places=2)
    duration_minutes = serializers.IntegerField()


class BookingConfirmationSessionSerializer(serializers.Serializer):
    """Sessão confirmada quando há agendamentos em mais de uma data."""
    appointment_id = serializers.IntegerField()
    date = serializers.CharField()
    start_time = serializers.CharField()
    end_time = serializers.CharField()
    services = BookingConfirmationItemSerializer(many=True)
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)


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
    sessions = BookingConfirmationSessionSerializer(many=True, required=False)
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2)
    whatsapp_url = serializers.CharField()

    @staticmethod
    def build_from_appointments(appointments: list[Appointment]) -> dict:
        """Monta o dict de confirmação a partir de 1 ou mais Appointments criados."""
        import urllib.parse

        first = appointments[0]
        salon = first.salon
        tz = timezone.get_current_timezone()
        client_name = first.client.name if first.client else 'Cliente'

        sessions_summary = []
        all_services = []
        grand_total = Decimal('0.00')
        date_blocks_text = []

        for appt in appointments:
            items = appt.items.select_related('service__service', 'professional').all()
            start = timezone.localtime(appt.time_range.lower, tz)
            end = timezone.localtime(appt.time_range.upper, tz)
            date_str = start.strftime('%d/%m/%Y')
            start_str = start.strftime('%H:%M')
            end_str = end.strftime('%H:%M')

            session_services = []
            lines = []
            for item in items:
                sname = item.service.service.name
                pname = item.professional.full_name.split()[0] if item.professional else None
                entry = {
                    'service_name': sname,
                    'professional_name': pname,
                    'price': item.price,
                    'duration_minutes': item.duration_minutes,
                }
                session_services.append(entry)
                all_services.append(entry)
                part = f'• {sname}'
                if pname:
                    part += f' (com {pname})'
                lines.append(part)

            sessions_summary.append({
                'appointment_id': appt.pk,
                'date': date_str,
                'start_time': start_str,
                'end_time': end_str,
                'services': session_services,
                'total_price': appt.total_price,
            })
            grand_total += appt.total_price
            date_blocks_text.append(
                f"📅 Data: {date_str} ({start_str} às {end_str})\n" + '\n'.join(lines)
            )

        first_session = sessions_summary[0]
        if len(sessions_summary) == 1:
            services_text = '\n'.join(
                f"• {s['service_name']}" + (f" (com {s['professional_name']})" if s['professional_name'] else '')
                for s in first_session['services']
            )
            whatsapp_message = (
                f"Olá! Fiz um agendamento pelo site. 😊\n\n"
                f"📋 *Resumo do Agendamento*\n"
                f"👤 Nome: {client_name}\n"
                f"📅 Data: {first_session['date']}\n"
                f"🕐 Horário: {first_session['start_time']} às {first_session['end_time']}\n"
                f"💇 Serviços:\n{services_text}\n"
                f"💰 Total: R$ {grand_total:.2f}\n\n"
                f"Aguardo confirmação! 🙏"
            )
        else:
            blocks = '\n\n'.join(date_blocks_text)
            whatsapp_message = (
                f"Olá! Fiz um agendamento pelo site. 😊\n\n"
                f"📋 *Resumo dos Agendamentos*\n"
                f"👤 Nome: {client_name}\n\n"
                f"{blocks}\n\n"
                f"💰 Total: R$ {grand_total:.2f}\n\n"
                f"Aguardo confirmação! 🙏"
            )

        salon_phone_digits = re.sub(r'\D', '', salon.phone_number or '')
        if salon_phone_digits and not salon_phone_digits.startswith('55'):
            salon_phone_digits = '55' + salon_phone_digits

        whatsapp_url = (
            f"https://wa.me/{salon_phone_digits}?text={urllib.parse.quote(whatsapp_message)}"
            if salon_phone_digits else ''
        )

        return {
            'appointment_id': first.pk,
            'salon_name': salon.brand_name,
            'salon_phone': salon.phone_number,
            'client_name': client_name,
            'date': first_session['date'],
            'start_time': first_session['start_time'],
            'end_time': first_session['end_time'],
            'services': all_services,
            'sessions': sessions_summary,
            'total_price': grand_total,
            'whatsapp_url': whatsapp_url,
        }

    @classmethod
    def build_from_appointment(cls, appointment: Appointment) -> dict:
        return cls.build_from_appointments([appointment])

