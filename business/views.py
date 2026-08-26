import logging
from datetime import date, datetime, time
from decimal import Decimal

from django.db import transaction, IntegrityError
from django.db.models import Sum, Q, Count
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status, generics, filters as drf_filters
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from business.filters import AppointmentFilter
from business.models import Appointment
from business.serializers import (
    AppointmentReadSerializer,
    AppointmentCreateSerializer,
    AppointmentStatusSerializer,
    DashboardSummarySerializer,
    DashboardAppointmentSerializer,
    CustomerSummarySerializer,
    EmployeeSummarySerializer,
    ServiceSalonSummarySerializer,
)
from business.models import Appointment, Customer, Employee, ServiceSalon
from core.permissions import TenantAccessPermission, CanManageAppointments, IsManagerOrOwner, TenantRole

logger = logging.getLogger(__name__)

TAGS_APPOINTMENT = ['Agendamentos']
TAGS_DASHBOARD = ['Dashboard']


# ═══════════════════════════════════════════════════════════════
#  APPOINTMENT CRUD
# ═══════════════════════════════════════════════════════════════


class AppointmentListCreateView(generics.ListCreateAPIView):
    """
    GET  → Lista agendamentos do salão (com filtros inclusivos).
    POST → Cria um novo agendamento com todas as validações.
    """
    permission_classes = [TenantAccessPermission, CanManageAppointments]
    filterset_class = AppointmentFilter

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return AppointmentCreateSerializer
        return AppointmentReadSerializer

    def get_queryset(self):
        salon = self.request.salon
        qs = Appointment.objects.filter(salon=salon).select_related(
            'professional', 'client',
        ).prefetch_related('items__service__service').order_by('time_range')

        # Profissionais só veem seus próprios agendamentos
        if getattr(self.request, 'tenant_role', None) == TenantRole.PROFESSIONAL:
            qs = qs.filter(professional__user=self.request.user)

        return qs

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Listar agendamentos',
        description='Lista agendamentos do salão com filtros inclusivos de profissional, serviço, status e data.',
        parameters=[
            OpenApiParameter('date_from', str, description='Data inicial (YYYY-MM-DD)'),
            OpenApiParameter('date_to', str, description='Data final (YYYY-MM-DD)'),
            OpenApiParameter('professional', str, description='IDs dos profissionais (separados por vírgula)'),
            OpenApiParameter('service', str, description='IDs dos serviços (separados por vírgula)'),
            OpenApiParameter('status', str, description='Status (separados por vírgula)'),
            OpenApiParameter('client', str, description='IDs dos clientes (separados por vírgula)'),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Criar agendamento',
        request=AppointmentCreateSerializer,
        responses={201: AppointmentReadSerializer},
    )
    def post(self, request, *args, **kwargs):
        return super().post(request, *args, **kwargs)

    def perform_create(self, serializer):
        serializer.save()

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Sobrescrevemos create para retornar o serializer de leitura."""
        write_serializer = self.get_serializer(data=request.data)
        write_serializer.is_valid(raise_exception=True)
        self.perform_create(write_serializer)
        appointment = write_serializer.instance
        read_serializer = AppointmentReadSerializer(appointment)
        return Response(read_serializer.data, status=status.HTTP_201_CREATED)


class AppointmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    → Detalhe de um agendamento.
    PUT    → Atualiza dados do agendamento (horário, profissional, serviço).
    DELETE → Cancela (soft-delete via status) o agendamento.
    """
    permission_classes = [TenantAccessPermission, CanManageAppointments]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return AppointmentCreateSerializer
        return AppointmentReadSerializer

    def get_queryset(self):
        salon = self.request.salon
        qs = Appointment.objects.filter(salon=salon).select_related(
            'professional', 'client',
        ).prefetch_related('items__service__service')
        if getattr(self.request, 'tenant_role', None) == TenantRole.PROFESSIONAL:
            qs = qs.filter(professional__user=self.request.user)
        return qs

    @extend_schema(tags=TAGS_APPOINTMENT, summary='Detalhe do agendamento')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Atualizar agendamento',
        request=AppointmentCreateSerializer,
        responses={200: AppointmentReadSerializer},
    )
    def put(self, request, *args, **kwargs):
        return self._update(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Atualizar agendamento (parcial)',
        request=AppointmentCreateSerializer,
        responses={200: AppointmentReadSerializer},
    )
    def patch(self, request, *args, **kwargs):
        return self._update(request, *args, **kwargs)

    def _update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status in (Appointment.Status.COMPLETED, Appointment.Status.CANCELLED):
            return Response(
                {"detail": "Não é possível editar um agendamento concluído ou cancelado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer = AppointmentCreateSerializer(
            instance=instance, data=request.data, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AppointmentReadSerializer(instance).data)

    @extend_schema(tags=TAGS_APPOINTMENT, summary='Cancelar agendamento')
    def delete(self, request, *args, **kwargs):
        """Soft-delete: apenas muda o status para CANCELLED."""
        instance = self.get_object()
        if instance.status == Appointment.Status.CANCELLED:
            return Response(
                {"detail": "Agendamento já está cancelado."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instance.status == Appointment.Status.COMPLETED:
            return Response(
                {"detail": "Não é possível cancelar um agendamento já concluído."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        instance.status = Appointment.Status.CANCELLED
        instance.save(update_fields=['status', 'updated_at'])
        logger.info("Agendamento %s cancelado via DELETE.", instance.pk)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AppointmentStatusUpdateView(APIView):
    """
    PATCH → Atualiza somente o status do agendamento.
    Profissionais só podem marcar como 'completed' os seus próprios agendamentos.
    """
    permission_classes = [TenantAccessPermission, CanManageAppointments]

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Atualizar status do agendamento',
        request=AppointmentStatusSerializer,
        responses={200: AppointmentReadSerializer},
    )
    def patch(self, request, pk):
        salon = request.salon
        appointment = get_object_or_404(Appointment, pk=pk, salon=salon)

        # Profissional só altera status dos próprios agendamentos
        role = getattr(request, 'tenant_role', None)
        if role == TenantRole.PROFESSIONAL:
            if appointment.professional is None or appointment.professional.user_id != request.user.id:
                return Response(
                    {"detail": "Você só pode atualizar o status dos seus próprios agendamentos."},
                    status=status.HTTP_403_FORBIDDEN,
                )
            # Profissional só pode marcar como concluído
            if request.data.get('status') != Appointment.Status.COMPLETED:
                return Response(
                    {"detail": "Profissionais só podem marcar agendamentos como concluídos."},
                    status=status.HTTP_403_FORBIDDEN,
                )

        serializer = AppointmentStatusSerializer(
            instance=appointment, data=request.data,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(AppointmentReadSerializer(appointment).data)


# ═══════════════════════════════════════════════════════════════
#  AUXILIARY LISTS (FOR FRONTEND DROPDOWNS)
# ═══════════════════════════════════════════════════════════════

class CustomerListCreateView(generics.ListCreateAPIView):
    """Retorna clientes ativos do salão ou cria um novo cliente."""
    permission_classes = [TenantAccessPermission]
    serializer_class = CustomerSummarySerializer
    pagination_class = None
    filter_backends = [drf_filters.SearchFilter]
    search_fields = ['name', 'phone']

    def get_queryset(self):
        return Customer.objects.filter(salon=self.request.salon, is_active=True).order_by('name')
        
    @transaction.atomic
    def perform_create(self, serializer):
        try:
            serializer.save(salon=self.request.salon)
        except IntegrityError:
            raise ValidationError({"phone": ["Já existe um cliente cadastrado com este telefone/CPF neste salão."]})


class EmployeeListView(generics.ListAPIView):
    """Retorna profissionais agendáveis do salão para dropdowns."""
    permission_classes = [TenantAccessPermission]
    serializer_class = EmployeeSummarySerializer
    pagination_class = None

    def get_queryset(self):
        qs = Employee.objects.filter(
            salon=self.request.salon, 
            is_active=True, 
            is_schedulable=True
        ).order_by('full_name')
        
        # Se for profissional logado e não tiver acesso livre, só vê a si mesmo
        if getattr(self.request, 'tenant_role', None) == TenantRole.PROFESSIONAL:
            qs = qs.filter(user=self.request.user)
            
        return qs


class ServiceSalonListView(generics.ListAPIView):
    """Retorna serviços ativos do salão para dropdowns."""
    permission_classes = [TenantAccessPermission]
    serializer_class = ServiceSalonSummarySerializer
    pagination_class = None

    def get_queryset(self):
        return ServiceSalon.objects.filter(
            salon=self.request.salon, 
            is_active=True
        ).select_related('service').order_by('service__name')


# ═══════════════════════════════════════════════════════════════
#  DASHBOARD
# ═══════════════════════════════════════════════════════════════

class DashboardView(APIView):
    """
    GET → Retorna o resumo do dia (ou intervalo) para o salão:
      - total de atendimentos
      - atendimentos concluídos
      - faturamento estimado (todos exceto cancelados)
      - faturamento concluído (somente completed)
      - lista com horário, profissional, serviço e status

    Filtros (query params, todos opcionais, inclusivos):
      - date       : data específica (YYYY-MM-DD) — padrão: hoje
      - date_from  : data inicial
      - date_to    : data final
      - professional: IDs separados por vírgula
      - service    : IDs separados por vírgula
      - status     : status separados por vírgula
    """
    permission_classes = [TenantAccessPermission, CanManageAppointments]

    @extend_schema(
        tags=TAGS_DASHBOARD,
        summary='Dashboard de atendimentos',
        description=(
            'Retorna métricas resumidas (total, concluídos, faturamento) e a lista de '
            'atendimentos do salão. Por padrão retorna o dia atual, todos os profissionais, '
            'serviços e status.'
        ),
        parameters=[
            OpenApiParameter('date', str, description='Data específica (YYYY-MM-DD). Padrão: hoje.'),
            OpenApiParameter('date_from', str, description='Data inicial do intervalo.'),
            OpenApiParameter('date_to', str, description='Data final do intervalo.'),
            OpenApiParameter('professional', str, description='IDs dos profissionais (vírgula).'),
            OpenApiParameter('service', str, description='IDs dos serviços (vírgula).'),
            OpenApiParameter('status', str, description='Status (vírgula). Ex: pending,confirmed'),
        ],
        responses={200: DashboardSummarySerializer},
    )
    def get(self, request):
        salon = request.salon

        # ── Base queryset ────────────────────────────────────
        qs = Appointment.objects.filter(salon=salon).select_related(
            'professional', 'client',
        ).prefetch_related('items__service__service')

        # ── Filtro de data ───────────────────────────────────
        date_str = request.query_params.get('date')
        date_from_str = request.query_params.get('date_from')
        date_to_str = request.query_params.get('date_to')

        if date_from_str or date_to_str:
            if date_from_str:
                try:
                    d = date.fromisoformat(date_from_str)
                except ValueError:
                    return Response({"date_from": "Formato inválido. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
                start_dt = timezone.make_aware(datetime.combine(d, time.min))
                qs = qs.filter(time_range__startswith__gte=start_dt)

            if date_to_str:
                try:
                    d = date.fromisoformat(date_to_str)
                except ValueError:
                    return Response({"date_to": "Formato inválido. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
                end_dt = timezone.make_aware(datetime.combine(d, time.max))
                qs = qs.filter(time_range__startswith__lte=end_dt)
        else:
            try:
                target_date = date.fromisoformat(date_str) if date_str else timezone.localdate()
            except ValueError:
                return Response({"date": "Formato inválido. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)
            day_start = timezone.make_aware(datetime.combine(target_date, time.min))
            day_end = timezone.make_aware(datetime.combine(target_date, time.max))
            qs = qs.filter(
                time_range__startswith__gte=day_start,
                time_range__startswith__lte=day_end,
            )

        # ── Filtros inclusivos ───────────────────────────────
        professional_ids = request.query_params.get('professional')
        if professional_ids:
            ids = [v.strip() for v in professional_ids.split(',') if v.strip()]
            qs = qs.filter(professional__id__in=ids)

        service_ids = request.query_params.get('service')
        if service_ids:
            ids = [v.strip() for v in service_ids.split(',') if v.strip()]
            qs = qs.filter(items__service__id__in=ids)

        status_values = request.query_params.get('status')
        if status_values:
            values = [v.strip() for v in status_values.split(',') if v.strip()]
            qs = qs.filter(status__in=values)

        if getattr(request, 'tenant_role', None) == TenantRole.PROFESSIONAL:
            qs = qs.filter(professional__user=request.user)

        qs = qs.distinct().order_by('time_range')

        from django.db.models import F
        aggregation = qs.aggregate(
            total=Count('id'),
            completed=Count('id', filter=Q(status=Appointment.Status.COMPLETED)),
            pending=Count('id', filter=Q(status=Appointment.Status.PENDING)),
            confirmed=Count('id', filter=Q(status=Appointment.Status.CONFIRMED)),
            cancelled=Count('id', filter=Q(status=Appointment.Status.CANCELLED)),
            estimated_revenue=Sum(
                F('total_price') - F('discount'),
                filter=~Q(status=Appointment.Status.CANCELLED),
            ),
            completed_revenue=Sum(
                F('total_price') - F('discount'),
                filter=Q(status=Appointment.Status.COMPLETED),
            ),
        )

        data = {
            'total_appointments': aggregation['total'],
            'completed_appointments': aggregation['completed'],
            'pending_appointments': aggregation['pending'],
            'confirmed_appointments': aggregation['confirmed'],
            'cancelled_appointments': aggregation['cancelled'],
            'estimated_revenue': str(aggregation['estimated_revenue'] or Decimal('0.00')),
            'completed_revenue': str(aggregation['completed_revenue'] or Decimal('0.00')),
            'appointments': DashboardAppointmentSerializer(qs, many=True).data,
        }

        return Response(data)

