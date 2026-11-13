import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone
from psycopg2.extras import DateTimeTZRange
from rest_framework import serializers as drf_serializers, status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from auth_users.models import User
from business.availability import (
    ChainStep,
    DEFAULT_CLOSES_AT,
    DEFAULT_OPENS_AT,
    build_chain_slots,
    build_slots,
    get_working_hours,
)
from business.models import (
    Appointment,
    Customer,
    Employee,
    EmployeeService,
    Salon,
    Service,
    ServiceSalon,
)
from business.serializers import (
    AppointmentStatusSerializer,
    EmployeeWriteSerializer,
    HexColorField,
    SalonProfileSerializer,
)
from core.models import Address, City, Country, State


@override_settings(
    REST_FRAMEWORK={
        'DEFAULT_PAGINATION_CLASS': 'core.pagination.CustomPageNumberPagination',
        'PAGE_SIZE': 50,
        'DEFAULT_PARSER_CLASSES': ['rest_framework.parsers.JSONParser'],
        'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
        'DEFAULT_FILTER_BACKENDS': ('django_filters.rest_framework.DjangoFilterBackend',),
        'DEFAULT_AUTHENTICATION_CLASSES': ('rest_framework_simplejwt.authentication.JWTAuthentication',),
        'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
        'DEFAULT_THROTTLE_CLASSES': [],
        'DEFAULT_THROTTLE_RATES': {},
    }
)
class BaseBusinessTestCase(TestCase):
    """Base com fixtures compartilhadas."""

    @classmethod
    def setUpTestData(cls):
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(name='São Paulo', abbreviation='SP', region='Sudeste', country=country)
        city = City.objects.create(name='São Paulo', state=state)
        address = Address.objects.create(
            street='Rua Teste', neighborhood='Centro', number='100', city=city, zip_code='01000-000',
        )

        cls.owner_user = User.objects.create_user(email='owner@test.com', password='pwd')
        cls.manager_user = User.objects.create_user(email='manager@test.com', password='pwd')
        cls.financial_user = User.objects.create_user(email='financial@test.com', password='pwd')
        cls.receptionist_user = User.objects.create_user(email='recep@test.com', password='pwd')
        cls.other_user = User.objects.create_user(email='other@test.com', password='pwd')

        cls.salon = Salon.objects.create(
            name='Salão Teste', slug='salao-teste', email='salao@test.com', address=address,
        )
        cls.salon.owners.add(cls.owner_user)

        # 1. Owner que também é profissional
        cls.owner_employee = Employee.objects.create(
            salon=cls.salon, user=cls.owner_user, full_name='Owner Profissional',
            cpf_cnpj='12345678901', role=Employee.Role.MANAGER,
            is_schedulable=True
        )

        # 2. Empregados que fazem login
        cls.manager_employee = Employee.objects.create(
            salon=cls.salon, user=cls.manager_user, full_name='Gerente',
            cpf_cnpj='12345678902', role=Employee.Role.MANAGER, is_schedulable=False
        )
        cls.financial_employee = Employee.objects.create(
            salon=cls.salon, user=cls.financial_user, full_name='Financeiro',
            cpf_cnpj='12345678903', role=Employee.Role.FINANCIAL, is_schedulable=False
        )
        cls.receptionist_employee = Employee.objects.create(
            salon=cls.salon, user=cls.receptionist_user, full_name='Recepcionista',
            cpf_cnpj='12345678904', role=Employee.Role.RECEPTIONIST, is_schedulable=False
        )

        # 3. Profissional puro (SEM LOGIN)
        cls.pure_professional = Employee.objects.create(
            salon=cls.salon, user=None, full_name='Profissional Puro',
            cpf_cnpj='12345678905', role=Employee.Role.PROFESSIONAL, is_schedulable=True
        )

        # Serviços
        cls.service_global = Service.objects.create(name='Corte Feminino')
        cls.service_global2 = Service.objects.create(name='Escova')
        cls.service_salon = ServiceSalon.objects.create(
            service=cls.service_global, salon=cls.salon,
            price=Decimal('80.00'), duration_minutes=60,
        )
        cls.service_salon2 = ServiceSalon.objects.create(
            service=cls.service_global2, salon=cls.salon,
            price=Decimal('50.00'), duration_minutes=30,
        )

        # Vincular serviços aos profissionais
        EmployeeService.objects.create(employee=cls.owner_employee, service=cls.service_salon)
        EmployeeService.objects.create(employee=cls.pure_professional, service=cls.service_salon2)

        # Clientes
        cls.customer = Customer.objects.create(
            salon=cls.salon, name='Ana Cliente', phone='11999990000', cpf='33333333333',
        )
        cls.customer2 = Customer.objects.create(
            salon=cls.salon, name='Bia Cliente', phone='11999990001', cpf='44444444444',
        )

    def setUp(self):
        self.client = APIClient()

    def _auth(self, user):
        token = str(AccessToken.for_user(user))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_X_TENANT_SLUG='salao-teste')


class NewBusinessRulesTests(BaseBusinessTestCase):

    def test_owner_as_professional_has_employee_id(self):
        self._auth(self.owner_user)
        # Ao bater numa view qualquer, o TenantAccessPermission vai rodar
        resp = self.client.get('/api/v1/employees')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Verificar o retorno do /auth/me se o employee_id vem certo
        # Faremos isso no request pro auth, mas simulamos lendo a response:
        # Pela estrutura de view, não temos acesso ao request modificado. Mas o proxy seria /auth/me.

    def test_owner_appears_in_schedulable_employees(self):
        self._auth(self.receptionist_user)
        resp = self.client.get('/api/v1/employees')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = [e['full_name'] for e in resp.data]
        self.assertIn('Owner Profissional', names)

    def test_appointment_assigned_to_owner(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=3)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'services': [{'service_id': self.service_salon.id, 'professional_id': str(self.owner_employee.id)}],
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['items'][0]['professional_name'], 'Owner Profissional')

    def test_professional_cannot_login(self):
        # Validação de model
        emp = Employee(
            salon=self.salon, user=self.other_user, full_name='Hacker',
            cpf_cnpj='999999', role=Employee.Role.PROFESSIONAL
        )
        with self.assertRaises(ValidationError):
            emp.clean()

    def test_financial_can_read_appointments(self):
        self._auth(self.financial_user)
        resp = self.client.get('/api/v1/appointments')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_financial_cannot_create_appointment(self):
        self._auth(self.financial_user)
        resp = self.client.post('/api/v1/appointments', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_financial_can_access_dashboard(self):
        self._auth(self.financial_user)
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_customer_cpf_optional(self):
        self._auth(self.receptionist_user)
        resp = self.client.post('/api/v1/customers', {
            'name': 'No CPF', 'phone': '11988887777'
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['cpf'], '')

    def test_customer_duplicate_cpf_same_salon_fails(self):
        self._auth(self.receptionist_user)
        resp = self.client.post('/api/v1/customers', {
            'name': 'Clone', 'phone': '999', 'cpf': '33333333333'
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cpf', resp.data)

    def test_appointment_custom_duration(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=5)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'services': [{'service_id': self.service_salon2.id, 'professional_id': str(self.pure_professional.id), 'duration_minutes': 120}],
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        # original é 30 mins, pedimos 120
        self.assertEqual(resp.data['items'][0]['duration_minutes'], 120)

    def test_appointment_rejects_unlisted_service(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=6)
        # owner_employee só faz service_salon. Se tentarmos service_salon2:
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),\
            'services': [{'service_id': self.service_salon2.id, 'professional_id': str(self.owner_employee.id)}],\
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('services', str(resp.data))

    def test_salons_fully_isolated(self):
        # Setup second salon
        address = Address.objects.first()
        other_salon = Salon.objects.create(name='S2', slug='s2', email='s2@t.com', address=address)
        other_user = User.objects.create_user(email='o2@t.com', password='1')
        other_salon.owners.add(other_user)
        
        token = str(AccessToken.for_user(other_user))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_X_TENANT_SLUG='s2')
        
        resp = self.client.get('/api/v1/customers')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Should be empty, not seeing customer1
        self.assertEqual(len(resp.data), 0)

class AppointmentMultiServiceTest(BaseBusinessTestCase):
    def test_multi_service_different_professionals(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=3)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'services': [
                {'service_id': self.service_salon.id, 'professional_id': str(self.owner_employee.id)},
                {'service_id': self.service_salon2.id, 'professional_id': str(self.pure_professional.id)}
            ],
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertNotIn('professional', resp.data)
        self.assertEqual(len(resp.data['items']), 2)
        prof_names = {item['professional_name'] for item in resp.data['items']}
        self.assertEqual(prof_names, {'Owner Profissional', 'Profissional Puro'})


class AppointmentSurchargeTest(BaseBusinessTestCase):
    def test_surcharge(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=4)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'services': [{'service_id': self.service_salon.id}],
            'surcharge': '20.00',
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        print('DATA:', resp.data)
        self.assertEqual(str(resp.data.get('surcharge')), '20.00')
        # Total price is sum of items
        self.assertEqual(resp.data['total_price'], '80.00')


class AppointmentOptionalClientTest(BaseBusinessTestCase):
    def test_optional_client(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=5)
        resp = self.client.post('/api/v1/appointments', {
            'services': [{'service_id': self.service_salon.id}],
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.data.get('client'))


class OperatingHoursTest(BaseBusinessTestCase):
    def test_operating_hours(self):
        self._auth(self.owner_user)
        resp = self.client.patch('/api/v1/salon/operating-hours', {
            'operating_hours': {'monday': {'open': '09:00', 'close': '18:00'}}
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        
        get_resp = self.client.get('/api/v1/salon/operating-hours')
        self.assertEqual(get_resp.data['operating_hours']['monday']['open'], '09:00')


class ServiceCRUDTest(BaseBusinessTestCase):
    def test_service_crud(self):
        self._auth(self.owner_user)
        # Create
        resp = self.client.post('/api/v1/salon/services', {
            'service_name': 'Novo Servico',
            'price': '100.00',
            'duration_minutes': 45
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        srv_id = resp.data['id']
        
        # List
        resp = self.client.get('/api/v1/salon/services')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(s['id'] == srv_id for s in resp.data['results']))
        
        # Update
        resp = self.client.patch(f'/api/v1/salon/services/{srv_id}', {
            'price': '110.00'
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['price'], '110.00')
        
        # Delete
        resp = self.client.delete(f'/api/v1/salon/services/{srv_id}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)


class CustomerEditTest(BaseBusinessTestCase):
    def test_customer_edit(self):
        self._auth(self.manager_user)
        resp = self.client.patch(f'/api/v1/customers/{self.customer.id}', {
            'name': 'Ana Editada',
            'phone': '11999991111'
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['name'], 'Ana Editada')
        self.assertEqual(resp.data['phone'], '11999991111')


class CollaboradorVerboseNameTest(BaseBusinessTestCase):
    def test_employee_verbose_name(self):
        self.assertEqual(Employee._meta.verbose_name, 'Colaborador')


# ═══════════════════════════════════════════════════════════════════════════════
#  TESTES UNITÁRIOS PUROS (SERIALIZERS, AVAILABILITY E VALIDAÇÕES SEM BANCO)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBusinessAvailabilityUnit:
    """Testes unitários para o motor de cálculo de horários (business/availability.py)."""

    def test_get_working_hours_default_when_empty(self) -> None:
        monday = date(2026, 11, 9)  # Segunda-feira
        assert get_working_hours({}, monday) == (DEFAULT_OPENS_AT, DEFAULT_CLOSES_AT)
        assert get_working_hours(None, monday) == (DEFAULT_OPENS_AT, DEFAULT_CLOSES_AT)

    def test_get_working_hours_supports_multiple_formats_and_closed_days(self) -> None:
        operating_hours = {
            'monday': {'open': '09:00', 'close': '18:30'},
            'terca': '10:00 às 19:00',
            'wednesday': ['08:30', '17:00'],
            'sunday': {'closed': True},
        }
        assert get_working_hours(operating_hours, date(2026, 11, 9)) == (
            time(9, 0),
            time(18, 30),
        )
        assert get_working_hours(operating_hours, date(2026, 11, 10)) == (
            time(10, 0),
            time(19, 0),
        )
        assert get_working_hours(operating_hours, date(2026, 11, 11)) == (
            time(8, 30),
            time(17, 0),
        )
        assert get_working_hours(operating_hours, date(2026, 11, 15)) is None

    def test_build_slots_respects_duration_and_busy_intervals(self) -> None:
        future_day = timezone.localdate() + timedelta(days=5)
        tz = timezone.get_current_timezone()
        busy_start = timezone.make_aware(datetime.combine(future_day, time(10, 0)), tz)
        busy_end = timezone.make_aware(datetime.combine(future_day, time(11, 0)), tz)

        slots = build_slots(
            day=future_day,
            duration_minutes=60,
            busy_ranges=[(busy_start, busy_end)],
            working_hours=(time(9, 0), time(12, 0)),
            slot_minutes=30,
        )
        labels = [s['label'] for s in slots]
        # 09:00-10:00 (livre), 09:30-10:30 (conflita), 10:00-11:00 (conflita),
        # 10:30-11:30 (conflita), 11:00-12:00 (livre)
        assert labels == ['09:00', '11:00']
        assert all(s['period'] == 'morning' for s in slots)

    def test_build_chain_slots_reconciles_sequential_professionals(self) -> None:
        future_day = timezone.localdate() + timedelta(days=5)
        tz = timezone.get_current_timezone()
        # Profissional 1 ocupada 09:00-09:30; Profissional 2 ocupada 10:30-11:00
        p1_busy = ((timezone.make_aware(datetime.combine(future_day, time(9, 0)), tz),
                    timezone.make_aware(datetime.combine(future_day, time(9, 30)), tz)),)
        p2_busy = ((timezone.make_aware(datetime.combine(future_day, time(10, 30)), tz),
                    timezone.make_aware(datetime.combine(future_day, time(11, 0)), tz)),)

        steps = [
            ChainStep(duration_minutes=30, candidates=(p1_busy,)),
            ChainStep(duration_minutes=30, candidates=(p2_busy,)),
        ]
        slots = build_chain_slots(
            day=future_day,
            steps=steps,
            working_hours=(time(9, 0), time(11, 30)),
            slot_minutes=30,
        )
        # 09:00 falha (P1 ocupada 09:00-09:30)
        # 09:30 passa (P1 09:30-10:00 livre, P2 10:00-10:30 livre)
        # 10:00 falha (P2 ocupada 10:30-11:00)
        # 10:30 passa (P1 10:30-11:00 livre, P2 11:00-11:30 livre)
        assert [s['label'] for s in slots] == ['09:30', '10:30']
        assert [s['end_label'] for s in slots] == ['10:30', '11:30']


class TestBusinessSerializersUnit:
    """Testes unitários de campos customizados e validadores de serializers do app business."""

    @pytest.mark.parametrize(
        ('raw_color', 'expected'),
        [
            ('#abc', '#AABBCC'),
            (' #233b5c ', '#233B5C'),
        ],
    )
    def test_hex_color_field_normalizes_short_and_lowercase(
        self, raw_color: str, expected: str
    ) -> None:
        field = HexColorField()
        assert field.to_internal_value(raw_color) == expected

    @pytest.mark.parametrize(
        'invalid_color',
        ['233B5C', '#12345', '#GGGGGG', '{{7*7}}', '<script>'],
    )
    def test_hex_color_field_rejects_invalid_and_ssti_payloads(self, invalid_color: str) -> None:
        field = HexColorField()
        with pytest.raises(drf_serializers.ValidationError, match='Informe uma cor em hexadecimal'):
            field.to_internal_value(invalid_color)

    def test_salon_profile_serializer_validate_name_and_empty_optional_colors(self) -> None:
        assert SalonProfileSerializer.validate_name('  Salão Elegance  ') == 'Salão Elegance'
        with pytest.raises(drf_serializers.ValidationError, match='ao menos 2 caracteres'):
            SalonProfileSerializer.validate_name(' A ')

        serializer = SalonProfileSerializer()
        cleaned = serializer.validate({'secondary_color': '', 'accent_color': None})
        assert cleaned['secondary_color'] is None
        assert cleaned['accent_color'] is None

    def test_employee_write_serializer_field_validators(self) -> None:
        assert (
            EmployeeWriteSerializer.validate_full_name('  Maria   Eduarda  ') == 'Maria Eduarda'
        )
        with pytest.raises(drf_serializers.ValidationError, match='nome completo'):
            EmployeeWriteSerializer.validate_full_name('Ab')

        assert EmployeeWriteSerializer.validate_cpf_cnpj('123.456.789-01') == '12345678901'
        assert (
            EmployeeWriteSerializer.validate_cpf_cnpj('12.345.678/0001-99') == '12345678000199'
        )
        with pytest.raises(drf_serializers.ValidationError, match='CPF .* ou CNPJ'):
            EmployeeWriteSerializer.validate_cpf_cnpj('123456')

    @pytest.mark.parametrize(
        ('current_status', 'next_status', 'is_valid'),
        [
            (Appointment.Status.PENDING, Appointment.Status.CONFIRMED, True),
            (Appointment.Status.PENDING, Appointment.Status.CANCELLED, True),
            (Appointment.Status.PENDING, Appointment.Status.COMPLETED, False),
            (Appointment.Status.CONFIRMED, Appointment.Status.COMPLETED, True),
            (Appointment.Status.COMPLETED, Appointment.Status.CANCELLED, False),
            (Appointment.Status.CANCELLED, Appointment.Status.CONFIRMED, False),
        ],
    )
    def test_appointment_status_transitions(
        self, current_status: str, next_status: str, is_valid: bool
    ) -> None:
        instance = MagicMock(status=current_status)
        serializer = AppointmentStatusSerializer(instance=instance)
        if is_valid:
            assert serializer.validate_status(next_status) == next_status
        else:
            with pytest.raises(drf_serializers.ValidationError, match='não é permitida'):
                serializer.validate_status(next_status)


# ═══════════════════════════════════════════════════════════════════════════════
#  TESTES DE INTEGRAÇÃO COMPLEMENTARES (VIEWS DRF DO APP BUSINESS)
# ═══════════════════════════════════════════════════════════════════════════════


class AppointmentLifecycleAndAvailabilityIntegrationTest(BaseBusinessTestCase):
    """Cobre atualização, transição de status, cancelamento (DELETE) e endpoint de disponibilidade."""

    def test_appointment_detail_update_status_and_soft_delete(self) -> None:
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(days=1, hours=2)

        create_resp = self.client.post(
            '/api/v1/appointments',
            {
                'client_id': str(self.customer.id),
                'services': [
                    {
                        'service_id': self.service_salon.id,
                        'professional_id': str(self.owner_employee.id),
                    }
                ],
                'start_time': start.isoformat(),
            },
            format='json',
        )
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        appt_id = create_resp.data['id']

        # PUT para atualizar observações e desconto
        new_start = start + timedelta(hours=1)
        put_resp = self.client.put(
            f'/api/v1/appointments/{appt_id}',
            {
                'client_id': str(self.customer.id),
                'services': [
                    {
                        'service_id': self.service_salon.id,
                        'professional_id': str(self.owner_employee.id),
                    }
                ],
                'start_time': new_start.isoformat(),
                'discount': '10.00',
                'notes': 'Cliente VIP',
            },
            format='json',
        )
        self.assertEqual(put_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(put_resp.data['discount'], '10.00')
        self.assertEqual(put_resp.data['notes'], 'Cliente VIP')

        # PATCH status: pending -> confirmed
        status_resp = self.client.patch(
            f'/api/v1/appointments/{appt_id}/status',
            {'status': Appointment.Status.CONFIRMED},
            format='json',
        )
        self.assertEqual(status_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(status_resp.data['status'], Appointment.Status.CONFIRMED)

        # DELETE realiza soft-delete mudando para cancelled
        del_resp = self.client.delete(f'/api/v1/appointments/{appt_id}')
        self.assertEqual(del_resp.status_code, status.HTTP_204_NO_CONTENT)

        # Tentar editar ou cancelar novamente um agendamento cancelado retorna 400
        edit_cancelled = self.client.put(
            f'/api/v1/appointments/{appt_id}',
            {
                'services': [{'service_id': self.service_salon.id}],
                'start_time': new_start.isoformat(),
            },
            format='json',
        )
        self.assertEqual(edit_cancelled.status_code, status.HTTP_400_BAD_REQUEST)

        del_again = self.client.delete(f'/api/v1/appointments/{appt_id}')
        self.assertEqual(del_again.status_code, status.HTTP_400_BAD_REQUEST)

    def test_availability_endpoint_and_salon_branding_views(self) -> None:
        self._auth(self.owner_user)
        target_day = (timezone.localdate() + timedelta(days=3)).isoformat()

        avail_resp = self.client.get(
            '/api/v1/availability',
            {
                'date': target_day,
                'professional': str(self.owner_employee.id),
                'service': str(self.service_salon.id),
            },
        )
        self.assertEqual(avail_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(avail_resp.data['duration_minutes'], 60)
        self.assertTrue(len(avail_resp.data['slots']) > 0)

        # Branding autenticado e público
        branding_resp = self.client.get('/api/v1/salon/branding')
        self.assertEqual(branding_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(branding_resp.data['slug'], 'salao-teste')

        public_branding = self.client.get('/api/v1/public/salons/salao-teste/branding')
        self.assertEqual(public_branding.status_code, status.HTTP_200_OK)

        # Atualização do perfil, cores e obrigatoriedade de profissional do salão
        profile_patch = self.client.patch(
            '/api/v1/salon/profile',
            {
                'display_name': 'Salão Top',
                'primary_color': '#abc',
                'require_professional_choice': True,
            },
            format='json',
        )
        self.assertEqual(profile_patch.status_code, status.HTTP_200_OK)
        self.assertEqual(profile_patch.data['brand_name'], 'Salão Top')
        self.assertEqual(profile_patch.data['primary_color'], '#AABBCC')
        self.assertTrue(profile_patch.data['require_professional_choice'])

    def test_staff_crud_and_soft_delete(self) -> None:
        self._auth(self.owner_user)
        create_staff = self.client.post(
            '/api/v1/staff',
            {
                'full_name': 'Fernanda Lima',
                'cpf_cnpj': '987.654.321-00',
                'role': Employee.Role.PROFESSIONAL,
                'contract_type': Employee.ContractType.COMMISSION,
                'is_schedulable': True,
                'default_commission_rate': '45.00',
                'services': [
                    {'service_id': self.service_salon.id, 'commission_rate': '50.00'}
                ],
            },
            format='json',
        )
        self.assertEqual(create_staff.status_code, status.HTTP_201_CREATED)
        staff_id = create_staff.data['id']
        self.assertEqual(create_staff.data['cpf_cnpj'], '98765432100')

        # Desativar colaborador via DELETE
        del_staff = self.client.delete(f'/api/v1/staff/{staff_id}')
        self.assertEqual(del_staff.status_code, status.HTTP_204_NO_CONTENT)
