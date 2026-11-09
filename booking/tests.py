"""Testes unitários e de integração para os endpoints públicos de agendamento (booking)."""
from datetime import timedelta
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
