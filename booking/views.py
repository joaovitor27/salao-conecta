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

from business.availability import (
    ChainStep,
    PUBLIC_BOOKING_SLOT_MINUTES,
    build_chain_slots,
    build_slots,
    get_working_hours,
)
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


def _busy_ranges_for_professionals(
    professionals: list[Employee],
    day_start: datetime,
) -> dict[str, list[tuple[datetime, datetime]]]:
    """Retorna os intervalos ocupados no dia para cada profissional informado."""
    if not professionals:
        return {}
    prof_ids = {p.id for p in professionals}
    result: dict[str, list[tuple[datetime, datetime]]] = {str(pid): [] for pid in prof_ids}

    day_window = DateTimeTZRange(lower=day_start, upper=day_start + timedelta(days=1))
    booked = (
        Appointment.objects.filter(
            items__professional_id__in=prof_ids,
            time_range__overlap=day_window,
        )
        .exclude(status=Appointment.Status.CANCELLED)
        .prefetch_related('items')
        .distinct()
    )

    for appt in booked:
        if not appt.time_range or not appt.time_range.lower or not appt.time_range.upper:
            continue
        items = list(appt.items.all())
        if not items:
            continue
        cursor = appt.time_range.lower
        for item in items:
            item_end = cursor + timedelta(minutes=item.duration_minutes)
            if item.professional_id in prof_ids:
                result[str(item.professional_id)].append((cursor, item_end))
            cursor = item_end

    return result


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
    GET → Horários livres para agendamento em uma data (de 30 em 30 min por padrão).
    Aceita ?items=service_id:professional_id,service_id: para conciliar a agenda
    de múltiplos serviços (com profissionais diferentes ou 'indiferente').
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
            OpenApiParameter(
                'items', str,
                description='Pares service_id:professional_id separados por vírgula para conciliar agendas.',
            ),
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

        # ── Slot interval (padrão: 30 min no booking público) ─
        try:
            slot_minutes = int(params.get('slot_minutes') or PUBLIC_BOOKING_SLOT_MINUTES)
        except ValueError:
            slot_minutes = PUBLIC_BOOKING_SLOT_MINUTES
        slot_minutes = max(5, min(slot_minutes, 60))

        working_hours = get_working_hours(salon.operating_hours, day)
        day_reference = timezone.make_aware(
            datetime.combine(day, time.min), timezone.get_current_timezone()
        )

        # ── Modo em cadeia (?items=1:uuid,2:) ────────────────
        raw_items = (params.get('items') or '').strip()
        if raw_items:
            return self._chain_availability(
                salon=salon,
                day=day,
                day_reference=day_reference,
                working_hours=working_hours,
                slot_minutes=slot_minutes,
                raw_items=raw_items,
            )

        # ── Modo simples (compatível com chamadas existentes) ─
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
            busy_map = _busy_ranges_for_professionals([professional], day_reference)
            busy = busy_map.get(str(professional.id), [])

        slots = build_slots(day, duration, busy, working_hours, slot_minutes)

        return Response({
            'date': day.isoformat(),
            'professional_id': str(professional.id) if professional else None,
            'duration_minutes': duration,
            'slot_minutes': slot_minutes,
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

    def _chain_availability(
        self,
        salon: Salon,
        day,
        day_reference: datetime,
        working_hours,
        slot_minutes: int,
        raw_items: str,
    ) -> Response:
        parsed_pairs: list[tuple[int, str | None]] = []
        for token in raw_items.split(','):
            token = token.strip()
            if not token:
                continue
            parts = token.split(':', 1)
            try:
                sid = int(parts[0].strip())
            except ValueError:
                return Response(
                    {'detail': 'IDs de serviço inválidos.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            pid = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
            parsed_pairs.append((sid, pid))

        if not parsed_pairs:
            return Response(
                {'detail': 'Informe ao menos um item em items.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        service_map = {
            s.id: s
            for s in ServiceSalon.objects.filter(
                id__in=[sid for sid, _ in parsed_pairs], salon=salon, is_active=True
            ).select_related('service')
        }

        all_schedulable = list(
            Employee.objects.filter(salon=salon, is_active=True, is_schedulable=True)
            .prefetch_related('employee_services')
            .order_by('full_name')
        )
        prof_by_id = {str(p.id): p for p in all_schedulable}
        capable_by_service: dict[int, list[Employee]] = {}
        for emp in all_schedulable:
            for es in emp.employee_services.all():
                capable_by_service.setdefault(es.service_id, []).append(emp)

        busy_map = _busy_ranges_for_professionals(all_schedulable, day_reference)

        steps_meta: list[dict] = []
        chain_steps: list[ChainStep] = []

        for sid, pid in parsed_pairs:
            service_salon = service_map.get(sid)
            if service_salon is None:
                return Response(
                    {'detail': f'Serviço ID {sid} não encontrado.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            capable = capable_by_service.get(sid, [])
            chosen_prof: Employee | None = None

            if pid:
                chosen_prof = prof_by_id.get(pid)
                if chosen_prof is None or chosen_prof not in capable:
                    return Response(
                        {'detail': 'Profissional inválido para o serviço selecionado.'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                candidates = (tuple(busy_map.get(str(chosen_prof.id), [])),)
            elif len(capable) == 1:
                chosen_prof = capable[0]
                candidates = (tuple(busy_map.get(str(chosen_prof.id), [])),)
            elif capable:
                candidates = tuple(tuple(busy_map.get(str(c.id), [])) for c in capable)
            else:
                candidates = ()

            chain_steps.append(
                ChainStep(duration_minutes=service_salon.duration_minutes, candidates=candidates)
            )
            steps_meta.append({
                'service_id': service_salon.id,
                'service_name': service_salon.service.name,
                'duration_minutes': service_salon.duration_minutes,
                'professional_id': str(chosen_prof.id) if chosen_prof else None,
                'professional_name': chosen_prof.full_name.split()[0] if chosen_prof else None,
            })

        total_duration = sum(step.duration_minutes for step in chain_steps)
        slots = build_chain_slots(day, chain_steps, working_hours, slot_minutes)
        tz = timezone.get_current_timezone()

        return Response({
            'date': day.isoformat(),
            'professional_id': (
                steps_meta[0]['professional_id'] if len(steps_meta) == 1 else None
            ),
            'duration_minutes': total_duration,
            'slot_minutes': slot_minutes,
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
                    'segments': [
                        {
                            **meta,
                            'start_label': timezone.localtime(seg[0], tz).strftime('%H:%M'),
                            'end_label': timezone.localtime(seg[1], tz).strftime('%H:%M'),
                        }
                        for meta, seg in zip(steps_meta, slot['segments'])
                    ],
                }
                for slot in slots
            ],
        })


# ═══════════════════════════════════════════════════════════════
#  POST: Criar agendamento
# ═══════════════════════════════════════════════════════════════

class PublicBookingCreateView(APIView):
    """
    POST → Cria um ou mais agendamentos públicos (self-service).
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
        appointments = serializer.save()

        confirmation = BookingConfirmationSerializer.build_from_appointments(appointments)
        return Response(confirmation, status=status.HTTP_201_CREATED)

