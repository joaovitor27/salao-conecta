"""Testes unitários e de integração para os endpoints públicos de agendamento (booking)."""
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

import pytest
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from booking.serializers import BookingCreateSerializer
from business.models import (
    Customer,
    Employee,
    EmployeeService,
    Salon,
    Service,
    ServiceSalon,
)
from core.models import Address, City, Country, State


class TestBookingCreateSerializerUnit:
    """Testes unitários de validação de campos do BookingCreateSerializer (sem banco)."""

    def test_validate_client_phone_extracts_digits(self) -> None:
        serializer = BookingCreateSerializer()
        assert serializer.validate_client_phone('(11) 98888-7777') == '11988887777'
        assert serializer.validate_client_phone('+55 11 98888-7777') == '5511988887777'

    @pytest.mark.parametrize(
        'invalid_phone',
        ['123456789', '119999999999999', 'telefone-invalido', '{{7*7}}'],
    )
    def test_validate_client_phone_rejects_invalid_and_ssti(self, invalid_phone: str) -> None:
        serializer = BookingCreateSerializer()
        with pytest.raises(ValidationError, match='Informe um número de telefone válido'):
            serializer.validate_client_phone(invalid_phone)

    def test_validate_client_name_normalizes_whitespace_and_rejects_short(self) -> None:
        serializer = BookingCreateSerializer()
        assert serializer.validate_client_name('  Maria   Clara  ') == 'Maria Clara'

        with pytest.raises(ValidationError, match='Informe seu nome'):
            serializer.validate_client_name('   A   ')

    def test_validate_start_time_rejects_past_datetime(self) -> None:
        serializer = BookingCreateSerializer()
        past = timezone.now() - timedelta(minutes=10)
        with pytest.raises(ValidationError, match='O horário selecionado já passou'):
            serializer.validate_start_time(past)

        future = timezone.now() + timedelta(hours=2)
        assert serializer.validate_start_time(future) == future


@pytest.mark.django_db
class TestPublicBookingEndpointsIntegration:
    """Testes de integração para as views públicas do app booking."""

    @pytest.fixture(autouse=True)
    def disable_throttling(self, settings: Any) -> None:
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            'DEFAULT_THROTTLE_CLASSES': [],
            'DEFAULT_THROTTLE_RATES': {},
        }

    @pytest.fixture
    def booking_env(self) -> dict[str, Any]:
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(
            name='São Paulo', abbreviation='SP', region='Sudeste', country=country
        )
        city = City.objects.create(name='São Paulo', state=state)
        address = Address.objects.create(
            street='Rua Bela Cintra',
            neighborhood='Jardins',
            number='250',
            city=city,
            zip_code='01415-000',
        )

        salon = Salon.objects.create(
            name='Studio Belle',
            display_name='Belle',
            tagline='Realce sua beleza',
            slug='studio-belle',
            email='contato@belle.com',
            phone_number='(11) 97777-6666',
            address=address,
            operating_hours={
                'monday': {'open': '08:00', 'close': '20:00'},
                'tuesday': {'open': '08:00', 'close': '20:00'},
                'wednesday': {'open': '08:00', 'close': '20:00'},
                'thursday': {'open': '08:00', 'close': '20:00'},
                'friday': {'open': '08:00', 'close': '20:00'},
                'saturday': {'open': '09:00', 'close': '18:00'},
                'sunday': {'closed': True},
            },
        )

        srv_cut = Service.objects.create(name='Corte Bordado', description='Remove pontas duplas')
        srv_color = Service.objects.create(name='Coloração', description='Tintura completa')

        ss_cut = ServiceSalon.objects.create(
            salon=salon, service=srv_cut, price=Decimal('120.00'), duration_minutes=60
        )
        ss_color = ServiceSalon.objects.create(
            salon=salon, service=srv_color, price=Decimal('200.00'), duration_minutes=90
        )

        prof_juliana = Employee.objects.create(
            salon=salon,
            full_name='Juliana Martins',
            cpf_cnpj='11122233344',
            role=Employee.Role.PROFESSIONAL,
            is_schedulable=True,
        )
        prof_camila = Employee.objects.create(
            salon=salon,
            full_name='Camila Rocha',
            cpf_cnpj='55566677788',
            role=Employee.Role.PROFESSIONAL,
            is_schedulable=True,
        )

        EmployeeService.objects.create(employee=prof_juliana, service=ss_cut)
        EmployeeService.objects.create(employee=prof_juliana, service=ss_color)
        EmployeeService.objects.create(employee=prof_camila, service=ss_cut)

        return {
            'salon': salon,
            'ss_cut': ss_cut,
            'ss_color': ss_color,
            'prof_juliana': prof_juliana,
            'prof_camila': prof_camila,
        }

    def test_public_salon_view_returns_branding_and_404_when_inactive(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        resp = client.get('/api/v1/booking/studio-belle/salon')
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['brand_name'] == 'Belle'
        assert 'Rua Bela Cintra, 250, Jardins, São Paulo - SP' == resp.data['address_display']

        resp_404 = client.get('/api/v1/booking/salao-fantasma/salon')
        assert resp_404.status_code == status.HTTP_404_NOT_FOUND

    def test_public_services_and_professionals_filtering(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        srv_resp = client.get('/api/v1/booking/studio-belle/services')
        assert srv_resp.status_code == status.HTTP_200_OK
        assert len(srv_resp.data) == 2

        # Filtra profissionais que fazem AMBOS os serviços (apenas Juliana)
        cut_id = booking_env['ss_cut'].id
        color_id = booking_env['ss_color'].id
        prof_resp = client.get(
            f'/api/v1/booking/studio-belle/professionals?service={cut_id},{color_id}'
        )
        assert prof_resp.status_code == status.HTTP_200_OK
        assert len(prof_resp.data) == 1
        assert prof_resp.data[0]['first_name'] == 'Juliana'
        assert 'cpf_cnpj' not in prof_resp.data[0]

        # Service ID inválido (ex: tentativa de injeção/SSTI) -> 400
        bad_prof_resp = client.get('/api/v1/booking/studio-belle/professionals?service={{7*7}}')
        assert bad_prof_resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_public_availability_view_validates_params_and_returns_slots(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        future_monday = timezone.localdate() + timedelta(days=(7 - timezone.localdate().weekday()))

        resp = client.get(
            '/api/v1/booking/studio-belle/availability',
            {
                'date': future_monday.isoformat(),
                'professional': str(booking_env['prof_juliana'].id),
                'service': str(booking_env['ss_cut'].id),
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['duration_minutes'] == 60
        assert resp.data['is_closed'] is False
        assert len(resp.data['slots']) > 0

        # Data em formato inválido -> 400
        bad_date = client.get('/api/v1/booking/studio-belle/availability?date=31-12-2026')
        assert bad_date.status_code == status.HTTP_400_BAD_REQUEST

        # Duração inválida -> 400
        bad_dur = client.get('/api/v1/booking/studio-belle/availability?duration=abc')
        assert bad_dur.status_code == status.HTTP_400_BAD_REQUEST

    def test_public_booking_create_happy_path_conflict_and_whatsapp_url(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        start_dt = (timezone.now() + timedelta(days=2)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )

        payload = {
            'client_name': 'Patrícia Abravanel',
            'client_phone': '(11) 99111-2233',
            'start_time': start_dt.isoformat(),
            'services': [
                {
                    'service_id': booking_env['ss_cut'].id,
                    'professional_id': str(booking_env['prof_juliana'].id),
                }
            ],
            'notes': 'Prefiro água gelada',
        }

        resp = client.post('/api/v1/booking/studio-belle/appointments', payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['client_name'] == 'Patrícia Abravanel'
        assert resp.data['salon_name'] == 'Belle'
        assert resp.data['whatsapp_url'].startswith('https://wa.me/5511977776666?text=')
        assert Customer.objects.filter(salon=booking_env['salon'], phone='11991112233').exists()

        # Tentar agendar no mesmo horário com a mesma profissional deve gerar conflito (400)
        conflict_resp = client.post(
            '/api/v1/booking/studio-belle/appointments', payload, format='json'
        )
        assert conflict_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'start_time' in conflict_resp.data

        # Tentar agendar serviço que a profissional não realiza (Camila não faz Coloração)
        unlisted_payload = {
            **payload,
            'start_time': (start_dt + timedelta(hours=3)).isoformat(),
            'services': [
                {
                    'service_id': booking_env['ss_color'].id,
                    'professional_id': str(booking_env['prof_camila'].id),
                }
            ],
        }
        unlisted_resp = client.post(
            '/api/v1/booking/studio-belle/appointments', unlisted_payload, format='json'
        )
        assert unlisted_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'services' in unlisted_resp.data

    def test_professional_choice_rules_auto_single_and_required_setting(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        salon: Salon = booking_env['salon']
        start_dt = (timezone.now() + timedelta(days=3)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )

        # 1. Quando só 1 profissional faz o serviço (Coloração -> apenas Juliana),
        #    mesmo sem professional_id no payload, Juliana é predefinida automaticamente.
        resp_single = client.post(
            '/api/v1/booking/studio-belle/appointments',
            {
                'client_name': 'Helena Rizzo',
                'client_phone': '(11) 99222-3344',
                'start_time': start_dt.isoformat(),
                'services': [{'service_id': booking_env['ss_color'].id}],
            },
            format='json',
        )
        assert resp_single.status_code == status.HTTP_201_CREATED
        assert resp_single.data['services'][0]['professional_name'] == 'Juliana'

        # 2. Quando o salão NÃO obriga (default False) e há 2 profissionais (Corte),
        #    aceita sem professional_id ("Indiferente" -> None).
        resp_indifferent = client.post(
            '/api/v1/booking/studio-belle/appointments',
            {
                'client_name': 'Helena Rizzo',
                'client_phone': '(11) 99222-3344',
                'start_time': (start_dt + timedelta(hours=3)).isoformat(),
                'services': [{'service_id': booking_env['ss_cut'].id}],
            },
            format='json',
        )
        assert resp_indifferent.status_code == status.HTTP_201_CREATED
        assert resp_indifferent.data['services'][0]['professional_name'] is None

        # 3. Quando o salão OBRIGA a escolha (require_professional_choice=True)
        #    e há mais de 1 profissional, omitir professional_id retorna 400.
        salon.require_professional_choice = True
        salon.save(update_fields=['require_professional_choice'])

        salon_resp = client.get('/api/v1/booking/studio-belle/salon')
        assert salon_resp.data['require_professional_choice'] is True

        resp_missing = client.post(
            '/api/v1/booking/studio-belle/appointments',
            {
                'client_name': 'Helena Rizzo',
                'client_phone': '(11) 99222-3344',
                'start_time': (start_dt + timedelta(hours=5)).isoformat(),
                'services': [{'service_id': booking_env['ss_cut'].id}],
            },
            format='json',
        )
        assert resp_missing.status_code == status.HTTP_400_BAD_REQUEST
        assert 'services' in resp_missing.data

        # Mas se o serviço tiver apenas 1 profissional (Coloração), continua auto-predefinido!
        resp_auto_when_required = client.post(
            '/api/v1/booking/studio-belle/appointments',
            {
                'client_name': 'Helena Rizzo',
                'client_phone': '(11) 99222-3344',
                'start_time': (start_dt + timedelta(hours=5)).isoformat(),
                'services': [{'service_id': booking_env['ss_color'].id}],
            },
            format='json',
        )
        assert resp_auto_when_required.status_code == status.HTTP_201_CREATED
        assert resp_auto_when_required.data['services'][0]['professional_name'] == 'Juliana'

    def test_availability_30min_step_and_multi_professional_chain_reconciliation(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        future_tuesday = timezone.localdate() + timedelta(
            days=((1 - timezone.localdate().weekday()) % 7 or 7)
        )
        cut_id = booking_env['ss_cut'].id      # 60 min
        color_id = booking_env['ss_color'].id  # 90 min
        juliana_id = str(booking_env['prof_juliana'].id)
        camila_id = str(booking_env['prof_camila'].id)

        # Camila ocupada das 09:00 às 10:00 na terça
        tz = timezone.get_current_timezone()
        camila_busy_start = datetime.combine(future_tuesday, time(9, 0), tzinfo=tz)
        client.post(
            '/api/v1/booking/studio-belle/appointments',
            {
                'client_name': 'Cliente Agenda',
                'client_phone': '(11) 99000-1122',
                'start_time': camila_busy_start.isoformat(),
                'services': [{'service_id': cut_id, 'professional_id': camila_id}],
            },
            format='json',
        )

        # Cadeia: Corte com Camila (60m) + Coloração com Juliana (90m) = 150m
        resp = client.get(
            '/api/v1/booking/studio-belle/availability',
            {
                'date': future_tuesday.isoformat(),
                'items': f'{cut_id}:{camila_id},{color_id}:{juliana_id}',
            },
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data['slot_minutes'] == 30
        assert resp.data['duration_minutes'] == 150

        labels = [slot['label'] for slot in resp.data['slots']]
        # Intervalo de 30 em 30 min (não existem horários :15 ou :45)
        assert all(lbl.endswith(':00') or lbl.endswith(':30') for lbl in labels)
        # 08:30 (Camila 08:30–09:30) e 09:00 / 09:30 conflitam com Camila ocupada 09:00–10:00
        assert '08:00' in labels
        assert '08:30' not in labels
        assert '09:00' not in labels
        assert '09:30' not in labels
        assert '10:00' in labels

        slot_10 = next(s for s in resp.data['slots'] if s['label'] == '10:00')
        assert slot_10['end_label'] == '12:30'
        assert len(slot_10['segments']) == 2
        assert slot_10['segments'][0]['start_label'] == '10:00'
        assert slot_10['segments'][0]['end_label'] == '11:00'
        assert slot_10['segments'][0]['professional_name'] == 'Camila'
        assert slot_10['segments'][1]['start_label'] == '11:00'
        assert slot_10['segments'][1]['end_label'] == '12:30'
        assert slot_10['segments'][1]['professional_name'] == 'Juliana'

        # Payload inválido em items (ex: SSTI) -> 400
        bad_items = client.get(
            '/api/v1/booking/studio-belle/availability',
            {'date': future_tuesday.isoformat(), 'items': '{{7*7}}:abc'},
        )
        assert bad_items.status_code == status.HTTP_400_BAD_REQUEST

    def test_multi_date_sessions_booking_for_same_service_on_different_days(
        self, booking_env: dict[str, Any]
    ) -> None:
        client = APIClient()
        base = timezone.localtime(timezone.now())
        day_1 = (base + timedelta(days=4)).replace(
            hour=14, minute=0, second=0, microsecond=0
        )
        day_2 = (base + timedelta(days=6)).replace(
            hour=16, minute=30, second=0, microsecond=0
        )

        payload = {
            'client_name': 'Bruna Marquezine',
            'client_phone': '(11) 99444-5566',
            'sessions': [
                {
                    'start_time': day_1.isoformat(),
                    'services': [{'service_id': booking_env['ss_color'].id}],
                },
                {
                    'start_time': day_2.isoformat(),
                    'services': [{'service_id': booking_env['ss_color'].id}],
                },
            ],
        }

        resp = client.post('/api/v1/booking/studio-belle/appointments', payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert len(resp.data['sessions']) == 2
        assert Decimal(str(resp.data['total_price'])) == Decimal('400.00')
        assert resp.data['sessions'][0]['start_time'] == '14:00'
        assert resp.data['sessions'][0]['end_time'] == '15:30'
        assert resp.data['sessions'][1]['start_time'] == '16:30'
        assert resp.data['sessions'][1]['end_time'] == '18:00'

