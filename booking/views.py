"""
Views públicas para agendamento self-service.

Todas as views são AllowAny (sem autenticação).
O salão é identificado pelo slug na URL, NÃO pelo header X-Tenant-Slug.

Endpoints GET: somente leitura, retornam apenas dados necessários.
Endpoint POST: cria agendamento com validações de conflito.
"""
import logging
from datetime import datetime, time, timedelta

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from drf_spectacular.utils import extend_schema, OpenApiParameter
from psycopg2.extras import DateTimeTZRange
from rest_framework import status
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from business.availability import build_slots, get_working_hours, DEFAULT_SLOT_MINUTES
from business.models import Salon, ServiceSalon, Employee, Appointment
from booking.serializers import (
    PublicSalonSerializer,
    PublicServiceSerializer,
    PublicProfessionalSerializer,
    BookingCreateSerializer,
    BookingConfirmationSerializer,
)

logger = logging.getLogger(__name__)
TAGS = ['Booking Público']


def _get_active_salon(slug: str) -> Salon:
    """Busca o salão ativo pelo slug ou retorna 404."""
    return get_object_or_404(Salon, slug=slug, is_active=True)


# ═══════════════════════════════════════════════════════════════
#  GET: Informações do salão (branding + endereço)
# ═══════════════════════════════════════════════════════════════

class PublicSalonView(APIView):
    """
    GET → Dados públicos do salão: branding, endereço e telefone.
    Usado para montar o tema da página de booking.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=TAGS,
        summary='Dados públicos do salão',
        responses={200: PublicSalonSerializer},
    )
    def get(self, request, slug: str):
        salon = _get_active_salon(slug)
        serializer = PublicSalonSerializer(salon, context={'request': request})
        return Response(serializer.data)


# ═══════════════════════════════════════════════════════════════
#  GET: Serviços ativos do salão
# ═══════════════════════════════════════════════════════════════

class PublicServicesView(APIView):
    """
    GET → Lista de serviços ativos do salão.
    Retorna: id, nome, descrição, preço, duração e imagem.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=TAGS,
        summary='Serviços disponíveis para agendamento',
        responses={200: PublicServiceSerializer(many=True)},
    )
    def get(self, request, slug: str):
        salon = _get_active_salon(slug)
        services = ServiceSalon.objects.filter(
            salon=salon, is_active=True
        ).select_related('service').order_by('service__name')
        serializer = PublicServiceSerializer(services, many=True, context={'request': request})
        return Response(serializer.data)


# ═══════════════════════════════════════════════════════════════
#  GET: Profissionais agendáveis
# ═══════════════════════════════════════════════════════════════

class PublicProfessionalsView(APIView):
    """
    GET → Profissionais agendáveis do salão (apenas primeiro nome).
    Aceita ?service=1,2 para filtrar quem realiza os serviços informados.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=TAGS,
        summary='Profissionais disponíveis para agendamento',
        parameters=[
            OpenApiParameter(
                'service', str,
                description='IDs dos serviços (separados por vírgula). Retorna quem realiza todos.',
            ),
        ],
        responses={200: PublicProfessionalSerializer(many=True)},
    )
    def get(self, request, slug: str):
        salon = _get_active_salon(slug)
        qs = Employee.objects.filter(
            salon=salon, is_active=True, is_schedulable=True
        ).prefetch_related('employee_services__service__service')

        # Filtrar por serviço(s) selecionado(s)
        service_param = request.query_params.get('service', '')
        service_ids = [sid.strip() for sid in service_param.split(',') if sid.strip()]
        if service_ids:
            try:
                service_ids = [int(sid) for sid in service_ids]
            except ValueError:
                return Response(
                    {'detail': 'IDs de serviço inválidos.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            for sid in service_ids:
                qs = qs.filter(employee_services__service_id=sid)

        qs = qs.distinct().order_by('full_name')
        serializer = PublicProfessionalSerializer(qs, many=True)
        return Response(serializer.data)


# ═══════════════════════════════════════════════════════════════
#  GET: Horários disponíveis
# ═══════════════════════════════════════════════════════════════

class PublicAvailabilityView(APIView):
    """
    GET → Horários livres para agendamento em uma data.
    Reutiliza a lógica de `build_slots` existente.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=TAGS,
        summary='Horários disponíveis para agendamento',
        parameters=[
            OpenApiParameter('date', str, description='Data desejada (YYYY-MM-DD). Padrão: hoje.'),
            OpenApiParameter('professional', str, description='ID do profissional (UUID).'),
            OpenApiParameter('service', str, description='IDs dos serviços (vírgula) para somar a duração.'),
            OpenApiParameter('duration', int, description='Duração total em minutos (sobrepõe os serviços).'),
        ],
    )
    def get(self, request, slug: str):
        salon = _get_active_salon(slug)
        params = request.query_params

        # ── Data ─────────────────────────────────────────────
        raw_date = params.get('date')
        if raw_date:
            try:
                day = datetime.strptime(raw_date, '%Y-%m-%d').date()
            except ValueError:
                return Response(
                    {'detail': 'Data inválida. Use o formato YYYY-MM-DD.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            day = timezone.localdate()

        # ── Duração ──────────────────────────────────────────
        duration = 0
        raw_duration = params.get('duration')
        if raw_duration:
            try:
                duration = int(raw_duration)
            except ValueError:
                return Response(
                    {'detail': 'Duração inválida.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        service_ids = [sid for sid in (params.get('service') or '').split(',') if sid.strip()]
        if not duration and service_ids:
            try:
                service_ids_int = [int(sid) for sid in service_ids]
            except ValueError:
                return Response(
                    {'detail': 'IDs de serviço inválidos.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            duration = ServiceSalon.objects.filter(
                id__in=service_ids_int, salon=salon, is_active=True
            ).aggregate(total=Sum('duration_minutes'))['total'] or 0

        if duration <= 0:
            duration = 30

        # ── Slot interval ────────────────────────────────────
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
                return Response(
                    {'detail': 'Profissional não encontrado.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        busy: list[tuple[datetime, datetime]] = []
        if professional:
            reference = timezone.make_aware(
                datetime.combine(day, time.min), timezone.get_current_timezone()
            )
            booked = Appointment.objects.filter(
                items__professional=professional
            ).exclude(
                status=Appointment.Status.CANCELLED
            ).filter(
                time_range__overlap=DateTimeTZRange(
                    lower=reference, upper=reference + timedelta(days=1)
                )
            ).distinct()
            busy = [
                (appt.time_range.lower, appt.time_range.upper)
                for appt in booked
                if appt.time_range and appt.time_range.lower and appt.time_range.upper
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


# ═══════════════════════════════════════════════════════════════
#  POST: Criar agendamento
# ═══════════════════════════════════════════════════════════════

class PublicBookingCreateView(APIView):
    """
    POST → Cria um agendamento público (self-service).
    Identifica a cliente por nome + telefone (sem login).
    Retorna resumo completo com URL do WhatsApp.
    """
    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=TAGS,
        summary='Criar agendamento público',
        request=BookingCreateSerializer,
        responses={201: BookingConfirmationSerializer},
    )
    @transaction.atomic
    def post(self, request, slug: str):
        salon = _get_active_salon(slug)

        serializer = BookingCreateSerializer(
            data=request.data,
            context={'request': request, 'salon': salon},
        )
        serializer.is_valid(raise_exception=True)
        appointment = serializer.save()

        confirmation = BookingConfirmationSerializer.build_from_appointment(appointment)
        return Response(confirmation, status=status.HTTP_201_CREATED)
