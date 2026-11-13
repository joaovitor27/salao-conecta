"""Cálculo dos horários disponíveis para agendamento."""
from dataclasses import dataclass
from datetime import date as date_cls, datetime, time, timedelta
from typing import Sequence

from django.utils import timezone

DEFAULT_OPENS_AT = time(8, 0)
DEFAULT_CLOSES_AT = time(20, 0)
DEFAULT_SLOT_MINUTES = 15

# Chaves aceitas no JSON `Salon.operating_hours`, por dia da semana (Monday = 0)
_WEEKDAY_KEYS = (
    ('monday', 'mon', 'segunda', 'segunda-feira', 'seg'),
    ('tuesday', 'tue', 'terca', 'terça', 'terca-feira', 'ter'),
    ('wednesday', 'wed', 'quarta', 'quarta-feira', 'qua'),
    ('thursday', 'thu', 'quinta', 'quinta-feira', 'qui'),
    ('friday', 'fri', 'sexta', 'sexta-feira', 'sex'),
    ('saturday', 'sat', 'sabado', 'sábado', 'sab'),
    ('sunday', 'sun', 'domingo', 'dom'),
)

_OPEN_KEYS = ('open', 'opens_at', 'start', 'from', 'inicio', 'início', 'abertura')
_CLOSE_KEYS = ('close', 'closes_at', 'end', 'to', 'fim', 'fechamento')
_CLOSED_KEYS = ('closed', 'is_closed', 'fechado')


def _parse_time(value) -> time | None:
    if isinstance(value, time):
        return value
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    for fmt in ('%H:%M', '%H:%M:%S', '%H%M', '%Hh%M', '%Hh'):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


def _extract_range(entry) -> tuple[time, time] | None:
    """Interpreta as formas aceitas de horário de um dia. None = fechado."""
    if entry in (None, False, '', [], {}):
        return None

    if isinstance(entry, str):
        parts = entry.replace('às', '-').replace('as', '-').split('-')
        if len(parts) != 2:
            return None
        opens, closes = _parse_time(parts[0]), _parse_time(parts[1])
        return (opens, closes) if opens and closes else None

    if isinstance(entry, (list, tuple)):
        if len(entry) != 2:
            return None
        opens, closes = _parse_time(entry[0]), _parse_time(entry[1])
        return (opens, closes) if opens and closes else None

    if isinstance(entry, dict):
        for key in _CLOSED_KEYS:
            if entry.get(key) is True:
                return None
        for key in ('enabled', 'active', 'ativo', 'aberto'):
            if key in entry and entry.get(key) is False:
                return None

        opens = closes = None
        for key in _OPEN_KEYS:
            opens = opens or _parse_time(entry.get(key))
        for key in _CLOSE_KEYS:
            closes = closes or _parse_time(entry.get(key))
        return (opens, closes) if opens and closes else None

    return None


def get_working_hours(operating_hours, day: date_cls) -> tuple[time, time] | None:
    """
    Horário de funcionamento do salão no dia informado.
    Retorna (abertura, fechamento) ou None quando o salão está fechado.
    Sem configuração válida, assume o padrão 08:00–20:00.
    """
    if isinstance(operating_hours, dict) and operating_hours:
        normalized = {str(key).strip().lower(): value for key, value in operating_hours.items()}

        for key in _WEEKDAY_KEYS[day.weekday()]:
            if key in normalized:
                return _extract_range(normalized[key])

        # Formato único aplicado a todos os dias: {"open": "09:00", "close": "19:00"}
        parsed = _extract_range(normalized)
        if parsed:
            return parsed

    return DEFAULT_OPENS_AT, DEFAULT_CLOSES_AT


def _period(moment: datetime) -> str:
    if moment.hour < 12:
        return 'morning'
    if moment.hour < 18:
        return 'afternoon'
    return 'evening'


def working_window(day: date_cls, working_hours: tuple[time, time] | None) -> tuple[datetime, datetime] | None:
    """Converte o expediente do dia em datetimes aware (abertura, fechamento)."""
    if not working_hours:
        return None
    tz = timezone.get_current_timezone()
    opens_at, closes_at = working_hours
    opening = timezone.make_aware(datetime.combine(day, opens_at), tz)
    closing = timezone.make_aware(datetime.combine(day, closes_at), tz)
    if closing <= opening:
        closing += timedelta(days=1)
    return opening, closing


def build_slots(
    day: date_cls,
    duration_minutes: int,
    busy_ranges: list[tuple[datetime, datetime]],
    working_hours: tuple[time, time] | None,
    slot_minutes: int = DEFAULT_SLOT_MINUTES,
) -> list[dict]:
    """Gera os horários livres do dia respeitando duração, expediente e agenda."""
    window = working_window(day, working_hours)
    if not window:
        return []

    tz = timezone.get_current_timezone()
    opening, closing = window

    step = timedelta(minutes=slot_minutes)
    duration = timedelta(minutes=duration_minutes)
    now = timezone.now()

    slots: list[dict] = []
    cursor = opening
    while cursor + duration <= closing:
        end = cursor + duration
        if cursor >= now and not any(cursor < busy_end and end > busy_start
                                     for busy_start, busy_end in busy_ranges):
            local_start = timezone.localtime(cursor, tz)
            local_end = timezone.localtime(end, tz)
            slots.append({
                'start': cursor,
                'end': end,
                'label': local_start.strftime('%H:%M'),
                'end_label': local_end.strftime('%H:%M'),
                'period': _period(local_start),
            })
        cursor += step

    return slots


# ──────────────────────────────────────────────────────────────
#  Disponibilidade em cadeia (booking público)
#  Um atendimento é uma sequência de serviços; cada serviço pode ter
#  um profissional definido, vários aptos ("indiferente") ou nenhum.
# ──────────────────────────────────────────────────────────────

PUBLIC_BOOKING_SLOT_MINUTES = 30

BusyRanges = Sequence[tuple[datetime, datetime]]


@dataclass(frozen=True)
class ChainStep:
    """
    Etapa de um atendimento.

    `candidates` contém a agenda ocupada de cada profissional apto a executar a etapa.
    A etapa está livre quando AO MENOS UM candidato está livre no intervalo.
    Sem candidatos, a etapa não tem restrição de agenda (o salão define depois).
    """
    duration_minutes: int
    candidates: tuple[BusyRanges, ...] = ()


def is_free(start: datetime, end: datetime, busy: BusyRanges) -> bool:
    """True quando [start, end) não cruza nenhum intervalo ocupado."""
    return not any(start < busy_end and end > busy_start for busy_start, busy_end in busy)


def chain_segments(start: datetime, steps: Sequence[ChainStep]) -> list[tuple[datetime, datetime]] | None:
    """Intervalos de cada etapa iniciando em `start`, ou None se alguma etapa estiver ocupada."""
    segments: list[tuple[datetime, datetime]] = []
    cursor = start
    for step in steps:
        end = cursor + timedelta(minutes=step.duration_minutes)
        if step.candidates and not any(is_free(cursor, end, busy) for busy in step.candidates):
            return None
        segments.append((cursor, end))
        cursor = end
    return segments


def fits_working_hours(start: datetime, end: datetime, operating_hours: object) -> bool:
    """True quando o intervalo cabe inteiro no expediente do dia em que começa."""
    local_day = timezone.localtime(start).date()
    window = working_window(local_day, get_working_hours(operating_hours, local_day))
    return window is not None and window[0] <= start and end <= window[1]


def build_chain_slots(
    day: date_cls,
    steps: Sequence[ChainStep],
    working_hours: tuple[time, time] | None,
    slot_minutes: int = PUBLIC_BOOKING_SLOT_MINUTES,
    now: datetime | None = None,
) -> list[dict]:
    """
    Horários de início em que TODAS as etapas cabem em sequência,
    respeitando expediente e agenda de cada profissional.
    Cada slot traz `segments`: o intervalo de cada etapa, na mesma ordem de `steps`.
    """
    window = working_window(day, working_hours)
    if not window or not steps or slot_minutes <= 0:
        return []

    tz = timezone.get_current_timezone()
    opening, closing = window
    total = timedelta(minutes=sum(step.duration_minutes for step in steps))
    step_delta = timedelta(minutes=slot_minutes)
    reference = now or timezone.now()

    slots: list[dict] = []
    cursor = opening
    while cursor + total <= closing:
        if cursor >= reference:
            segments = chain_segments(cursor, steps)
            if segments is not None:
                local_start = timezone.localtime(cursor, tz)
                local_end = timezone.localtime(cursor + total, tz)
                slots.append({
                    'start': cursor,
                    'end': cursor + total,
                    'label': local_start.strftime('%H:%M'),
                    'end_label': local_end.strftime('%H:%M'),
                    'period': _period(local_start),
                    'segments': segments,
                })
        cursor += step_delta

    return slots
