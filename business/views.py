import logging
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from psycopg2.extras import DateTimeTZRange

from django.db import transaction, IntegrityError
from django.db.models import Sum, Q, Count, Max
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import status, generics, filters as drf_filters
from rest_framework.generics import get_object_or_404
from rest_framework.parsers import JSONParser, FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.exceptions import ValidationError

from django_filters.rest_framework import DjangoFilterBackend

from business.availability import build_slots, get_working_hours, DEFAULT_SLOT_MINUTES
from business.filters import AppointmentFilter, EmployeeFilter
from business.serializers import (
    AppointmentReadSerializer,
    AppointmentCreateSerializer,
    AppointmentStatusSerializer,
    AvailabilityResponseSerializer,
    DashboardSummarySerializer,
    DashboardAppointmentSerializer,
    CustomerSummarySerializer,
    CustomerListSerializer,
    EmployeeSummarySerializer,
    EmployeeReadSerializer,
    EmployeeWriteSerializer,
    ServiceSalonSummarySerializer,
    SalonBrandingSerializer,
    SalonProfileSerializer,
)
from business.models import Appointment, Customer, Employee, ServiceSalon, Salon
from core.pagination import CustomPageNumberPagination
from core.permissions import (
    TenantAccessPermission,
    CanManageAppointments,
    CanManageSalonProfile,
    CanManageEmployees,
)

logger = logging.getLogger(__name__)

TAGS_APPOINTMENT = ['Agendamentos']
TAGS_DASHBOARD = ['Dashboard']
TAGS_SALON = ['Salão']
TAGS_EMPLOYEE = ['Funcionários']


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
        return Appointment.objects.filter(salon=salon).select_related(
            'professional', 'client',
        ).prefetch_related('items__service__service').order_by('time_range')

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
        return Appointment.objects.filter(salon=salon).select_related(
            'professional', 'client',
        ).prefetch_related('items__service__service')

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
    Acessível por Owner, Manager, Receptionist (escrita) e Financial (leitura).
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
    """
    GET  → Clientes ativos do salão.
           Sem `page`/`page_size` retorna a lista completa (dropdowns);
           com um deles, retorna paginado (tela de clientes).
    POST → Cria um novo cliente.
    """
    permission_classes = [TenantAccessPermission]
    filter_backends = [drf_filters.SearchFilter, drf_filters.OrderingFilter]
    search_fields = ['name', 'phone', 'cpf', 'email']
    ordering_fields = ['name', 'phone', 'created_at', 'birth_date', 'appointments_count', 'last_visit']
    ordering = ['name']

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return CustomerSummarySerializer
        return CustomerListSerializer

    @property
    def paginator(self):
        """Paginação opt-in para não quebrar os dropdowns que esperam a lista completa."""
        if not hasattr(self, '_paginator'):
            params = self.request.query_params
            if 'page' in params or 'page_size' in params:
                self._paginator = CustomPageNumberPagination()
            else:
                self._paginator = None
        return self._paginator

    def get_queryset(self):
        return Customer.objects.filter(
            salon=self.request.salon, is_active=True
        ).annotate(
            appointments_count=Count(
                'appointments',
                filter=~Q(appointments__status=Appointment.Status.CANCELLED),
                distinct=True,
            ),
            last_visit=Max(
                'appointments__time_range__startswith',
                filter=Q(appointments__status=Appointment.Status.COMPLETED),
            ),
        ).order_by('name')


    @transaction.atomic
    def perform_create(self, serializer):
        try:
            serializer.save(salon=self.request.salon)
        except IntegrityError:
            raise ValidationError({"cpf": ["Já existe um cliente cadastrado com este CPF neste salão."]})


class EmployeeListView(generics.ListAPIView):
    """
    Retorna profissionais agendáveis do salão para dropdowns.
    Aceita `?service=1,2` para trazer apenas quem realiza todos esses serviços.
    """
    permission_classes = [TenantAccessPermission]
    serializer_class = EmployeeSummarySerializer
    pagination_class = None
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter]
    filterset_class = EmployeeFilter
    search_fields = ['full_name']

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Listar profissionais agendáveis',
        parameters=[
            OpenApiParameter('service', str, description='IDs dos serviços (vírgula). Retorna quem faz todos.'),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        return Employee.objects.filter(
            salon=self.request.salon,
            is_active=True,
            is_schedulable=True
        ).prefetch_related(
            'employee_services__service__service'
        ).order_by('full_name')


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



# ═══════════════════════════════════════════════════════════════
#  IDENTIDADE VISUAL / PERFIL DO SALÃO
# ═══════════════════════════════════════════════════════════════

class SalonBrandingView(APIView):
    """
    GET → Identidade visual do salão do usuário logado (nome, logo e cores).
    Usado pelo front para montar o tema dinamicamente.
    """
    permission_classes = [TenantAccessPermission, CanManageSalonProfile]

    @extend_schema(
        tags=TAGS_SALON,
        summary='Identidade visual do salão atual',
        responses={200: SalonBrandingSerializer},
    )
    def get(self, request):
        serializer = SalonBrandingSerializer(request.salon, context={'request': request})
        return Response(serializer.data)


class PublicSalonBrandingView(APIView):
    """
    GET → Identidade visual pública de um salão pelo slug.
    Permite aplicar o tema em telas anteriores ao login (ex: página de login do salão).
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=TAGS_SALON,
        summary='Identidade visual pública do salão',
        responses={200: SalonBrandingSerializer},
    )
    def get(self, request, slug: str):
        salon = get_object_or_404(Salon, slug=slug, is_active=True)
        serializer = SalonBrandingSerializer(salon, context={'request': request})
        return Response(serializer.data)


class SalonProfileView(APIView):
    """
    GET   → Perfil completo do salão (cadastro + identidade visual).
    PATCH → Atualiza nome, slogan, contato, logo e cores. Somente Owner/Manager.

    Aceita JSON ou multipart/form-data (necessário para o upload do logo).
    """
    permission_classes = [TenantAccessPermission, CanManageSalonProfile]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @extend_schema(
        tags=TAGS_SALON,
        summary='Perfil do salão',
        responses={200: SalonProfileSerializer},
    )
    def get(self, request):
        serializer = SalonProfileSerializer(request.salon, context={'request': request})
        return Response(serializer.data)

    @extend_schema(
        tags=TAGS_SALON,
        summary='Atualiza o perfil e a identidade visual do salão',
        request=SalonProfileSerializer,
        responses={200: SalonProfileSerializer},
    )
    def patch(self, request):
        salon = request.salon
        serializer = SalonProfileSerializer(
            salon, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError({'name': 'Já existe um salão com este nome ou email.'})

        logger.info("Perfil do salão %s atualizado por %s", salon.slug, request.user.email)
        return Response(serializer.data)


class SalonLogoView(APIView):
    """DELETE → Remove o logo atual do salão. Somente Owner/Manager."""
    permission_classes = [TenantAccessPermission, CanManageSalonProfile]

    @extend_schema(
        tags=TAGS_SALON,
        summary='Remove o logo do salão',
        responses={200: SalonProfileSerializer},
    )
    def delete(self, request):
        salon = request.salon
        if salon.logo:
            salon.logo.delete(save=False)
            salon.logo = None
            salon.save(update_fields=['logo', 'updated_at'])
        serializer = SalonProfileSerializer(salon, context={'request': request})
        return Response(serializer.data)



# ═══════════════════════════════════════════════════════════════
#  FUNCIONÁRIOS (CRUD)
# ═══════════════════════════════════════════════════════════════

class EmployeeListCreateView(generics.ListCreateAPIView):
    """
    GET  → Lista funcionários do salão (busca, ordenação e paginação).
    POST → Cadastra um funcionário com os serviços que ele realiza.
    """
    permission_classes = [TenantAccessPermission, CanManageEmployees]
    filter_backends = [DjangoFilterBackend, drf_filters.SearchFilter, drf_filters.OrderingFilter]
    filterset_class = EmployeeFilter
    search_fields = ['full_name', 'cpf_cnpj']
    ordering_fields = [
        'full_name', 'role', 'contract_type', 'fixed_salary',
        'default_commission_rate', 'is_active', 'created_at',
    ]
    ordering = ['full_name']

    def get_serializer_class(self):
        if self.request.method == 'POST':
            return EmployeeWriteSerializer
        return EmployeeReadSerializer

    def get_queryset(self):
        return Employee.objects.filter(salon=self.request.salon).select_related('user').prefetch_related(
            'employee_services__service__service'
        )

    @extend_schema(
        tags=TAGS_EMPLOYEE,
        summary='Listar funcionários',
        parameters=[
            OpenApiParameter('search', str, description='Busca por nome ou CPF/CNPJ.'),
            OpenApiParameter('role', str, description='Papéis (separados por vírgula).'),
            OpenApiParameter('contract_type', str, description='Tipos de contrato (vírgula).'),
            OpenApiParameter('is_active', bool, description='Somente ativos/inativos.'),
            OpenApiParameter('is_schedulable', bool, description='Somente quem atende na agenda.'),
            OpenApiParameter('service', str, description='IDs dos serviços (vírgula). Retorna quem faz todos.'),
            OpenApiParameter('ordering', str, description='Campo de ordenação. Ex: -full_name'),
        ],
        responses={200: EmployeeReadSerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS_EMPLOYEE,
        summary='Cadastrar funcionário',
        request=EmployeeWriteSerializer,
        responses={201: EmployeeReadSerializer},
    )
    @transaction.atomic
    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError({'cpf_cnpj': ['Já existe um funcionário com este CPF/CNPJ neste salão.']})
        return Response(
            EmployeeReadSerializer(serializer.instance).data,
            status=status.HTTP_201_CREATED,
        )


class EmployeeDetailView(generics.RetrieveUpdateDestroyAPIView):
    """
    GET    → Detalhe do funcionário.
    PATCH  → Atualiza dados, serviços e acesso ao sistema.
    DELETE → Desativa o funcionário (mantém o histórico de atendimentos).
    """
    permission_classes = [TenantAccessPermission, CanManageEmployees]
    lookup_field = 'pk'

    def get_serializer_class(self):
        if self.request.method in ('PUT', 'PATCH'):
            return EmployeeWriteSerializer
        return EmployeeReadSerializer

    def get_queryset(self):
        return Employee.objects.filter(salon=self.request.salon).select_related('user').prefetch_related(
            'employee_services__service__service'
        )

    @extend_schema(tags=TAGS_EMPLOYEE, summary='Detalhe do funcionário')
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=TAGS_EMPLOYEE,
        summary='Atualizar funcionário',
        request=EmployeeWriteSerializer,
        responses={200: EmployeeReadSerializer},
    )
    @transaction.atomic
    def patch(self, request, *args, **kwargs):
        employee = self.get_object()
        serializer = EmployeeWriteSerializer(
            instance=employee, data=request.data, partial=True, context={'request': request}
        )
        serializer.is_valid(raise_exception=True)
        try:
            serializer.save()
        except IntegrityError:
            raise ValidationError({'cpf_cnpj': ['Já existe um funcionário com este CPF/CNPJ neste salão.']})
        employee.refresh_from_db()
        return Response(EmployeeReadSerializer(employee).data)

    @extend_schema(tags=TAGS_EMPLOYEE, summary='Atualizar funcionário (PUT)')
    def put(self, request, *args, **kwargs):
        return self.patch(request, *args, **kwargs)

    @extend_schema(tags=TAGS_EMPLOYEE, summary='Desativar funcionário')
    def delete(self, request, *args, **kwargs):
        employee = self.get_object()
        if not employee.is_active:
            return Response(
                {'detail': 'Funcionário já está inativo.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        employee.is_active = False
        employee.save(update_fields=['is_active', 'updated_at'])
        logger.info("Funcionário %s desativado | salão=%s", employee.pk, request.salon.slug)
        return Response(status=status.HTTP_204_NO_CONTENT)



# ═══════════════════════════════════════════════════════════════
#  HORÁRIOS DISPONÍVEIS
# ═══════════════════════════════════════════════════════════════

class AvailabilityView(APIView):
    """
    GET → Horários livres para agendamento em uma data.

    Considera o expediente do salão, a duração somada dos serviços escolhidos
    e a agenda do profissional (quando informado).
    """
    permission_classes = [TenantAccessPermission, CanManageAppointments]

    @extend_schema(
        tags=TAGS_APPOINTMENT,
        summary='Horários disponíveis',
        parameters=[
            OpenApiParameter('date', str, description='Data desejada (YYYY-MM-DD). Padrão: hoje.'),
            OpenApiParameter('professional', str, description='ID do profissional.'),
            OpenApiParameter('service', str, description='IDs dos serviços (vírgula) para somar a duração.'),
            OpenApiParameter('duration', int, description='Duração total em minutos (sobrepõe os serviços).'),
            OpenApiParameter('slot_minutes', int, description='Intervalo entre horários. Padrão: 15.'),
            OpenApiParameter('appointment', int, description='ID do agendamento em edição (ignora o próprio horário).'),
        ],
        responses={200: AvailabilityResponseSerializer},
    )
    def get(self, request):
        salon = request.salon
        params = request.query_params

        # ── Data ─────────────────────────────────────────────
        raw_date = params.get('date')
        if raw_date:
            try:
                day = datetime.strptime(raw_date, '%Y-%m-%d').date()
            except ValueError:
                raise ValidationError({'date': 'Data inválida. Use o formato YYYY-MM-DD.'})
        else:
            day = timezone.localdate()

        # ── Duração ──────────────────────────────────────────
        duration = 0
        raw_duration = params.get('duration')
        if raw_duration:
            try:
                duration = int(raw_duration)
            except ValueError:
                raise ValidationError({'duration': 'Duração inválida.'})

        service_ids = [sid for sid in (params.get('service') or '').split(',') if sid.strip()]
        if not duration and service_ids:
            try:
                service_ids = [int(sid) for sid in service_ids]
            except ValueError:
                raise ValidationError({'service': 'IDs de serviço inválidos.'})
            duration = ServiceSalon.objects.filter(
                id__in=service_ids, salon=salon, is_active=True
            ).aggregate(total=Sum('duration_minutes'))['total'] or 0

        if duration <= 0:
            duration = 30

        # ── Intervalo entre horários ─────────────────────────
        try:
            slot_minutes = int(params.get('slot_minutes') or DEFAULT_SLOT_MINUTES)
        except ValueError:
            slot_minutes = DEFAULT_SLOT_MINUTES
        slot_minutes = max(5, min(slot_minutes, 60))

        # ── Profissional e agenda ocupada ────────────────────
        professional = None
        raw_professional = params.get('professional')
        if raw_professional:
            professional = Employee.objects.filter(
                id=raw_professional, salon=salon, is_active=True, is_schedulable=True
            ).first()
            if professional is None:
                raise ValidationError({'professional': 'Profissional não encontrado ou não-agendável.'})

        busy: list[tuple[datetime, datetime]] = []
        if professional:
            booked = Appointment.objects.filter(professional=professional).exclude(
                status=Appointment.Status.CANCELLED
            )
            raw_appointment = params.get('appointment')
            if raw_appointment and str(raw_appointment).isdigit():
                booked = booked.exclude(pk=int(raw_appointment))
            reference = timezone.make_aware(
                datetime.combine(day, time.min), timezone.get_current_timezone()
            )
            booked = booked.filter(
                time_range__overlap=DateTimeTZRange(
                    lower=reference, upper=reference + timedelta(days=1)
                )
            )
            busy = [
                (appointment.time_range.lower, appointment.time_range.upper)
                for appointment in booked
                if appointment.time_range and appointment.time_range.lower and appointment.time_range.upper
            ]

        working_hours = get_working_hours(salon.operating_hours, day)
        slots = build_slots(day, duration, busy, working_hours, slot_minutes)

        return Response({
            'date': day.isoformat(),
            'professional_id': str(professional.id) if professional else None,
            'duration_minutes': duration,
            'opens_at': working_hours[0].strftime('%H:%M') if working_hours else None,
            'closes_at': working_hours[1].strftime('%H:%M') if working_hours else None,
            'is_closed': working_hours is None,
            'slots': [
                {
                    'start': slot['start'].isoformat(),
                    'end': slot['end'].isoformat(),
                    'label': slot['label'],
                    'end_label': slot['end_label'],
                    'period': slot['period'],
                }
                for slot in slots
            ],
        })
