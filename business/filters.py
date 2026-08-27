from django_filters import rest_framework as filters

from business.models import Appointment, Employee


class AppointmentFilter(filters.FilterSet):
    """
    Filtro completo para agendamentos.

    Todos os campos de lista usam BaseInFilter, ou seja, aceitam
    múltiplos valores separados por vírgula (?status=pending,confirmed).
    """
    # ── Filtros por data (range no time_range) ───────────────
    date_from = filters.DateFilter(
        method='filter_date_from',
        label='Data inicial (YYYY-MM-DD)',
    )
    date_to = filters.DateFilter(
        method='filter_date_to',
        label='Data final (YYYY-MM-DD)',
    )

    # ── Filtros inclusivos (IN) ──────────────────────────────
    professional = filters.BaseInFilter(
        field_name='professional__id',
        label='IDs dos profissionais (separados por vírgula)',
    )
    service = filters.BaseInFilter(
        field_name='items__service__id',
        label='IDs dos serviços do salão (separados por vírgula)',
    )
    status = filters.BaseInFilter(
        field_name='status',
        label='Status (separados por vírgula, ex: pending,confirmed)',
    )
    client = filters.BaseInFilter(
        field_name='client__id',
        label='IDs dos clientes (separados por vírgula)',
    )

    class Meta:
        model = Appointment
        fields = []

    # ── Métodos de filtragem por data ────────────────────────

    @staticmethod
    def filter_date_from(queryset, _name, value):
        """Filtra agendamentos cuja data de início >= value."""
        from django.utils import timezone
        start = timezone.make_aware(
            timezone.datetime.combine(value, timezone.datetime.min.time())
        )
        return queryset.filter(time_range__startswith__gte=start)

    @staticmethod
    def filter_date_to(queryset, _name, value):
        """Filtra agendamentos cuja data de início <= final do dia."""
        from django.utils import timezone
        end = timezone.make_aware(
            timezone.datetime.combine(value, timezone.datetime.max.time())
        )
        return queryset.filter(time_range__startswith__lte=end)



class EmployeeFilter(filters.FilterSet):
    """
    Filtro de funcionários.

    `service` aceita vários IDs separados por vírgula e retorna apenas quem
    realiza TODOS os serviços informados (usado no agendamento).
    """
    role = filters.BaseInFilter(
        field_name='role',
        label='Papéis (separados por vírgula)',
    )
    contract_type = filters.BaseInFilter(
        field_name='contract_type',
        label='Tipos de contrato (separados por vírgula)',
    )
    is_active = filters.BooleanFilter(field_name='is_active', label='Somente ativos')
    is_schedulable = filters.BooleanFilter(field_name='is_schedulable', label='Atende na agenda')
    service = filters.BaseInFilter(
        method='filter_service',
        label='IDs dos serviços do salão (separados por vírgula)',
    )

    class Meta:
        model = Employee
        fields = []

    @staticmethod
    def filter_service(queryset, _name, value):
        """Interseção: o funcionário precisa realizar todos os serviços informados."""
        for service_id in value:
            if service_id in (None, ''):
                continue
            queryset = queryset.filter(employee_services__service_id=service_id)
        return queryset.distinct()
