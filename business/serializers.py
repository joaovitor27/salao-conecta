import logging
from datetime import timedelta


from django.utils import timezone
from psycopg2.extras import DateTimeTZRange
from rest_framework import serializers

from business.models import Appointment, ServiceSalon, Employee, Customer, Salon

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
#  Serializers de leitura (aninhados, somente GET)
# ──────────────────────────────────────────────────────────────

class EmployeeSummarySerializer(serializers.ModelSerializer):
    """Resumo do profissional (para exibição em listas)."""

    class Meta:
        model = Employee
        fields = ('id', 'full_name', 'role')


class ServiceSalonSummarySerializer(serializers.ModelSerializer):
    """Resumo do serviço do salão com o nome global do serviço."""
    service_name = serializers.CharField(source='service.name', read_only=True)

    class Meta:
        model = ServiceSalon
        fields = ('id', 'service_name', 'price', 'duration_minutes')


class CustomerSummarySerializer(serializers.ModelSerializer):
    """Resumo do cliente para exibição no agendamento."""

    class Meta:
        model = Customer
        fields = ('id', 'name', 'phone')


# ──────────────────────────────────────────────────────────────
#  Serializer de LEITURA do Appointment
# ──────────────────────────────────────────────────────────────

class AppointmentReadSerializer(serializers.ModelSerializer):
    """Serializer completo de leitura para Agendamentos."""
    professional = EmployeeSummarySerializer(read_only=True)
    service = ServiceSalonSummarySerializer(read_only=True)
    client = CustomerSummarySerializer(read_only=True)
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Appointment
        fields = (
            'id',
            'client',
            'professional',
            'service',
            'start_time',
            'end_time',
            'status',
            'status_display',
            'discount',
            'notes',
            'created_at',
            'updated_at',
        )

    @staticmethod
    def get_start_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.lower:
            return obj.time_range.lower.isoformat()
        return None

    @staticmethod
    def get_end_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.upper:
            return obj.time_range.upper.isoformat()
        return None


# ──────────────────────────────────────────────────────────────
#  Serializer de CRIAÇÃO do Appointment
# ──────────────────────────────────────────────────────────────

class AppointmentCreateSerializer(serializers.Serializer):
    """
    Serializer de escrita: recebe campos planos e monta o DateTimeRangeField
    automaticamente com base na duração do serviço selecionado.
    """
    client_id = serializers.UUIDField()
    professional_id = serializers.UUIDField(required=False, allow_null=True)
    service_id = serializers.IntegerField(help_text="ID do ServiceSalon")
    start_time = serializers.DateTimeField(
        help_text="Data e hora de início do agendamento (ISO 8601)"
    )
    discount = serializers.DecimalField(max_digits=10, decimal_places=2, default=0.00, required=False)
    notes = serializers.CharField(required=False, allow_blank=True, default='')

    # ── Validações de campo ──────────────────────────────────

    def validate_start_time(self, value):
        """Impede agendamentos no passado (margem de 5 min)."""
        now = timezone.now()
        if value < now - timedelta(minutes=5):
            raise serializers.ValidationError(
                "Não é possível agendar no passado."
            )
        return value

    # ── Validação cruzada ────────────────────────────────────

    def validate(self, attrs):
        salon: Salon = self.context['request'].salon

        # ── Cliente pertence ao salão? ───────────────────────
        try:
            client = Customer.objects.get(
                id=attrs['client_id'], salon=salon, is_active=True
            )
        except Customer.DoesNotExist:
            raise serializers.ValidationError(
                {"client_id": "Cliente não encontrado neste salão."}
            )

        # ── Serviço ativo no salão? ──────────────────────────
        try:
            service_salon = ServiceSalon.objects.select_related('service').get(
                id=attrs['service_id'], salon=salon, is_active=True
            )
        except ServiceSalon.DoesNotExist:
            raise serializers.ValidationError(
                {"service_id": "Serviço não encontrado ou inativo neste salão."}
            )

        # ── Profissional válido? ─────────────────────────────
        professional = None
        if attrs.get('professional_id'):
            try:
                professional = Employee.objects.get(
                    id=attrs['professional_id'],
                    salon=salon,
                    is_active=True,
                    is_schedulable=True,
                )
            except Employee.DoesNotExist:
                raise serializers.ValidationError(
                    {"professional_id": "Profissional não encontrado, inativo ou não-agendável neste salão."}
                )

        # ── Montar time_range ────────────────────────────────
        start = attrs['start_time']
        end = start + timedelta(minutes=service_salon.duration_minutes)
        time_range = DateTimeTZRange(lower=start, upper=end)

        # ── Conflito de horário (mesmo profissional)? ────────
        if professional:
            conflict = Appointment.objects.filter(
                professional=professional,
                time_range__overlap=time_range,
            ).exclude(
                status=Appointment.Status.CANCELLED,
            )
            if self.instance:
                conflict = conflict.exclude(pk=self.instance.pk)
            if conflict.exists():
                raise serializers.ValidationError(
                    {"start_time": "O profissional já possui um atendimento nesse horário."}
                )

        attrs['_client'] = client
        attrs['_service_salon'] = service_salon
        attrs['_professional'] = professional
        attrs['_time_range'] = time_range
        return attrs

    def create(self, validated_data):
        salon = self.context['request'].salon
        appointment = Appointment.objects.create(
            salon=salon,
            client=validated_data['_client'],
            professional=validated_data['_professional'],
            service=validated_data['_service_salon'],
            time_range=validated_data['_time_range'],
            discount=validated_data.get('discount', 0.00),
            notes=validated_data.get('notes', ''),
            status=Appointment.Status.PENDING,
        )
        logger.info(
            "Agendamento %s criado | salão=%s profissional=%s",
            appointment.pk, salon.slug,
            validated_data['_professional'].full_name if validated_data['_professional'] else 'N/A',
        )
        return appointment

    def update(self, instance, validated_data):
        instance.client = validated_data['_client']
        instance.professional = validated_data['_professional']
        instance.service = validated_data['_service_salon']
        instance.time_range = validated_data['_time_range']
        instance.discount = validated_data.get('discount', instance.discount)
        instance.notes = validated_data.get('notes', instance.notes)
        instance.save()
        logger.info("Agendamento %s atualizado | salão=%s", instance.pk, instance.salon.slug)
        return instance


# ──────────────────────────────────────────────────────────────
#  Serializer para ATUALIZAÇÃO de STATUS
# ──────────────────────────────────────────────────────────────

class AppointmentStatusSerializer(serializers.Serializer):
    """Permite apenas a transição de status do agendamento."""

    ALLOWED_TRANSITIONS = {
        Appointment.Status.PENDING: [
            Appointment.Status.CONFIRMED,
            Appointment.Status.CANCELLED,
        ],
        Appointment.Status.CONFIRMED: [
            Appointment.Status.PENDING,
            Appointment.Status.COMPLETED,
            Appointment.Status.CANCELLED,
        ],
        Appointment.Status.COMPLETED: [],
        Appointment.Status.CANCELLED: [],
    }

    status = serializers.ChoiceField(choices=Appointment.Status.choices)

    def validate_status(self, value):
        current = self.instance.status
        allowed = self.ALLOWED_TRANSITIONS.get(current, [])
        if value not in allowed:
            raise serializers.ValidationError(
                f"Transição de '{current}' para '{value}' não é permitida. "
                f"Transições válidas: {[s for s in allowed]}"
            )
        return value

    def update(self, instance, validated_data):
        instance.status = validated_data['status']
        instance.save(update_fields=['status', 'updated_at'])
        logger.info(
            "Status do agendamento %s atualizado para '%s'",
            instance.pk, instance.status,
        )
        return instance


# ──────────────────────────────────────────────────────────────
#  Serializer do DASHBOARD
# ──────────────────────────────────────────────────────────────

class DashboardAppointmentSerializer(serializers.ModelSerializer):
    """Serializer enxuto para os itens da lista do dashboard."""
    professional_name = serializers.CharField(
        source='professional.full_name', default='Sem profissional', read_only=True
    )
    service_name = serializers.CharField(
        source='service.service.name', read_only=True
    )
    service_price = serializers.DecimalField(
        source='service.price', max_digits=10, decimal_places=2, read_only=True
    )
    client_name = serializers.CharField(source='client.name', read_only=True)
    start_time = serializers.SerializerMethodField()
    end_time = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = Appointment
        fields = (
            'id',
            'client_name',
            'professional_name',
            'service_name',
            'service_price',
            'discount',
            'start_time',
            'end_time',
            'status',
            'status_display',
        )

    @staticmethod
    def get_start_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.lower:
            return obj.time_range.lower.isoformat()
        return None

    @staticmethod
    def get_end_time(obj: Appointment) -> str | None:
        if obj.time_range and obj.time_range.upper:
            return obj.time_range.upper.isoformat()
        return None


class DashboardSummarySerializer(serializers.Serializer):
    """Serializer para o resumo do dashboard."""
    total_appointments = serializers.IntegerField()
    completed_appointments = serializers.IntegerField()
    pending_appointments = serializers.IntegerField()
    confirmed_appointments = serializers.IntegerField()
    cancelled_appointments = serializers.IntegerField()
    estimated_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    completed_revenue = serializers.DecimalField(max_digits=12, decimal_places=2)
    appointments = DashboardAppointmentSerializer(many=True)
