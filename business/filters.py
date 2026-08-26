from django_filters import rest_framework as filters

from business.models import Appointment


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
