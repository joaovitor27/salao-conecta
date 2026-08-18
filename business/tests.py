from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from psycopg2.extras import DateTimeTZRange
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken

from auth_users.models import User
from business.models import Salon, Employee, Customer, Service, ServiceSalon, Appointment
from core.models import Country, State, City, Address


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
    """Base com fixtures compartilhadas para todos os testes do app business."""

    @classmethod
    def setUpTestData(cls):
        # ── Geo ──────────────────────────────────────────────
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(name='São Paulo', abbreviation='SP', region='Sudeste', country=country)
        city = City.objects.create(name='São Paulo', state=state)
        address = Address.objects.create(
            street='Rua Teste', neighborhood='Centro', number='100', city=city, zip_code='01000-000',
        )

        # ── Usuários ─────────────────────────────────────────
        cls.owner_user = User.objects.create_user(email='owner@test.com', password='Test@12345', first_name='Owner')
        cls.prof_user = User.objects.create_user(email='prof@test.com', password='Test@12345', first_name='Prof')
        cls.other_user = User.objects.create_user(email='other@test.com', password='Test@12345', first_name='Other')

        # ── Salão ────────────────────────────────────────────
        cls.salon = Salon.objects.create(
            name='Salão Teste', slug='salao-teste', email='salao@test.com', address=address,
        )
        cls.salon.owners.add(cls.owner_user)

        # ── Funcionários ─────────────────────────────────────
        cls.employee = Employee.objects.create(
            salon=cls.salon, user=cls.prof_user, full_name='Maria Profissional',
            cpf_cnpj='11111111111', role=Employee.Role.PROFESSIONAL,
            is_schedulable=True, default_commission_rate=Decimal('50.00'),
        )
        cls.employee2 = Employee.objects.create(
            salon=cls.salon, full_name='João Profissional',
            cpf_cnpj='22222222222', role=Employee.Role.PROFESSIONAL, is_schedulable=True,
        )

        # ── Serviços ─────────────────────────────────────────
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

        # ── Clientes ─────────────────────────────────────────
        cls.customer = Customer.objects.create(
            salon=cls.salon, name='Ana Cliente', phone='11999990000', cpf='33333333333',
        )
        cls.customer2 = Customer.objects.create(
            salon=cls.salon, name='Bia Cliente', phone='11999990001', cpf='44444444444',
        )

    def setUp(self):
        self.client = APIClient()

    # ── Helpers ───────────────────────────────────────────────

    def _auth(self, user):
        token = str(AccessToken.for_user(user))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}', HTTP_X_TENANT_SLUG='salao-teste')

    def _auth_owner(self):
        self._auth(self.owner_user)

    def _auth_prof(self):
        self._auth(self.prof_user)

    def _make_appointment(self, **overrides):
        now = timezone.now()
        start = overrides.pop('start', now + timedelta(hours=2))
        end = start + timedelta(minutes=self.service_salon.duration_minutes)
        defaults = dict(
            salon=self.salon, client=self.customer, professional=self.employee,
            service=self.service_salon, time_range=DateTimeTZRange(start, end),
            status=Appointment.Status.PENDING,
        )
        defaults.update(overrides)
        return Appointment.objects.create(**defaults)


# ═══════════════════════════════════════════════════════════════
#  TESTES DE AGENDAMENTO
# ═══════════════════════════════════════════════════════════════

class AppointmentCreateTests(BaseBusinessTestCase):

    def test_create_appointment_success(self):
        self._auth_owner()
        start = timezone.now() + timedelta(hours=3)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'professional_id': str(self.employee.id),
            'service_id': str(self.service_salon.id),
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['client']['name'], 'Ana Cliente')
        self.assertEqual(resp.data['professional']['full_name'], 'Maria Profissional')
        self.assertEqual(resp.data['status'], 'pending')

    def test_create_appointment_in_past_fails(self):
        self._auth_owner()
        past = timezone.now() - timedelta(hours=1)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'professional_id': str(self.employee.id),
            'service_id': str(self.service_salon.id),
            'start_time': past.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_appointment_conflict_fails(self):
        self._auth_owner()
        start = timezone.now() + timedelta(hours=5)
        self._make_appointment(start=start)
        # Mesmo profissional, mesmo horário
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer2.id),
            'professional_id': str(self.employee.id),
            'service_id': str(self.service_salon.id),
            'start_time': (start + timedelta(minutes=10)).isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('start_time', str(resp.data))

    def test_create_with_wrong_salon_client_fails(self):
        """Cliente de outro salão não pode ser agendado."""
        self._auth_owner()
        other_address = Address.objects.create(
            street='Outra Rua', neighborhood='Outro', number='1',
            city=City.objects.first(), zip_code='00000-000',
        )
        other_salon = Salon.objects.create(
            name='Outro Salão', slug='outro-salao', email='outro@test.com', address=other_address,
        )
        alien_customer = Customer.objects.create(
            salon=other_salon, name='Alien', phone='11900000000', cpf='99999999999',
        )
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(alien_customer.id),
            'professional_id': str(self.employee.id),
            'service_id': str(self.service_salon.id),
            'start_time': (timezone.now() + timedelta(hours=3)).isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_without_tenant_header_fails(self):
        token = str(AccessToken.for_user(self.owner_user))
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {token}')
        resp = self.client.post('/api/v1/appointments', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_unauthenticated_fails(self):
        resp = self.client.post('/api/v1/appointments', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_create_without_professional(self):
        """Agendamento sem profissional definido deve funcionar."""
        self._auth_owner()
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'service_id': str(self.service_salon.id),
            'start_time': (timezone.now() + timedelta(hours=4)).isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(resp.data['professional'])


class AppointmentListTests(BaseBusinessTestCase):

    def test_list_appointments(self):
        self._auth_owner()
        self._make_appointment()
        resp = self.client.get('/api/v1/appointments')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['count'], 1)

    def test_list_filter_by_status(self):
        self._auth_owner()
        self._make_appointment(status=Appointment.Status.CONFIRMED, start=timezone.now() + timedelta(hours=10))
        self._make_appointment(status=Appointment.Status.PENDING, start=timezone.now() + timedelta(hours=11))
        resp = self.client.get('/api/v1/appointments?status=confirmed')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for item in resp.data['results']:
            self.assertEqual(item['status'], 'confirmed')

    def test_list_filter_multiple_status(self):
        self._auth_owner()
        self._make_appointment(status=Appointment.Status.CONFIRMED, start=timezone.now() + timedelta(hours=12))
        self._make_appointment(status=Appointment.Status.PENDING, start=timezone.now() + timedelta(hours=13))
        resp = self.client.get('/api/v1/appointments?status=confirmed,pending')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        statuses = {item['status'] for item in resp.data['results']}
        self.assertTrue(statuses.issubset({'confirmed', 'pending'}))

    def test_professional_sees_only_own(self):
        self._make_appointment(professional=self.employee, start=timezone.now() + timedelta(hours=14))
        self._make_appointment(professional=self.employee2, start=timezone.now() + timedelta(hours=15))
        self._auth_prof()
        resp = self.client.get('/api/v1/appointments')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for item in resp.data['results']:
            self.assertEqual(item['professional']['full_name'], 'Maria Profissional')


class AppointmentDetailTests(BaseBusinessTestCase):

    def test_retrieve_appointment(self):
        self._auth_owner()
        appt = self._make_appointment()
        resp = self.client.get(f'/api/v1/appointments/{appt.pk}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['id'], appt.pk)

    def test_delete_cancels_appointment(self):
        self._auth_owner()
        appt = self._make_appointment()
        resp = self.client.delete(f'/api/v1/appointments/{appt.pk}')
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        appt.refresh_from_db()
        self.assertEqual(appt.status, Appointment.Status.CANCELLED)

    def test_delete_completed_fails(self):
        self._auth_owner()
        appt = self._make_appointment(status=Appointment.Status.COMPLETED)
        resp = self.client.delete(f'/api/v1/appointments/{appt.pk}')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_update_completed_appointment_fails(self):
        self._auth_owner()
        appt = self._make_appointment(status=Appointment.Status.COMPLETED)
        resp = self.client.put(f'/api/v1/appointments/{appt.pk}', {
            'client_id': str(self.customer.id),
            'professional_id': str(self.employee.id),
            'service_id': str(self.service_salon.id),
            'start_time': (timezone.now() + timedelta(hours=20)).isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class AppointmentStatusTests(BaseBusinessTestCase):

    def test_update_status_pending_to_confirmed(self):
        self._auth_owner()
        appt = self._make_appointment()
        resp = self.client.patch(f'/api/v1/appointments/{appt.pk}/status', {'status': 'confirmed'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['status'], 'confirmed')

    def test_invalid_transition_fails(self):
        self._auth_owner()
        appt = self._make_appointment(status=Appointment.Status.PENDING)
        resp = self.client.patch(f'/api/v1/appointments/{appt.pk}/status', {'status': 'completed'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_professional_can_complete_own(self):
        self._auth_prof()
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        resp = self.client.patch(f'/api/v1/appointments/{appt.pk}/status', {'status': 'completed'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_professional_cannot_cancel(self):
        self._auth_prof()
        appt = self._make_appointment(status=Appointment.Status.CONFIRMED)
        resp = self.client.patch(f'/api/v1/appointments/{appt.pk}/status', {'status': 'cancelled'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


# ═══════════════════════════════════════════════════════════════
#  TESTES DO DASHBOARD
# ═══════════════════════════════════════════════════════════════

class DashboardTests(BaseBusinessTestCase):

    def _create_today_appointments(self):
        """Cria agendamentos no dia de hoje para testar o dashboard."""
        now = timezone.now()
        base = now.replace(hour=9, minute=0, second=0, microsecond=0)
        if base < now:
            base = now + timedelta(minutes=10)

        a1 = self._make_appointment(
            start=base, status=Appointment.Status.COMPLETED,
            service=self.service_salon,  # R$ 80
        )
        a2 = self._make_appointment(
            start=base + timedelta(hours=2), status=Appointment.Status.CONFIRMED,
            service=self.service_salon2, professional=self.employee2,  # R$ 50
            client=self.customer2,
        )
        a3 = self._make_appointment(
            start=base + timedelta(hours=4), status=Appointment.Status.CANCELLED,
            service=self.service_salon,  # R$ 80 (cancelado → não conta)
        )
        return a1, a2, a3

    def test_dashboard_returns_today_by_default(self):
        self._auth_owner()
        self._create_today_appointments()
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.data
        self.assertEqual(data['total_appointments'], 3)
        self.assertEqual(data['completed_appointments'], 1)
        self.assertEqual(data['cancelled_appointments'], 1)
        # Faturamento estimado = R$ 80 (completed) + R$ 50 (confirmed) = R$ 130
        self.assertEqual(Decimal(str(data['estimated_revenue'])), Decimal('130.00'))
        # Faturamento concluído = R$ 80
        self.assertEqual(Decimal(str(data['completed_revenue'])), Decimal('80.00'))
        self.assertEqual(len(data['appointments']), 3)

    def test_dashboard_filter_by_professional(self):
        self._auth_owner()
        self._create_today_appointments()
        resp = self.client.get(f'/api/v1/dashboard?professional={self.employee.id}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for appt in resp.data['appointments']:
            self.assertEqual(appt['professional_name'], 'Maria Profissional')

    def test_dashboard_filter_by_service(self):
        self._auth_owner()
        self._create_today_appointments()
        resp = self.client.get(f'/api/v1/dashboard?service={self.service_salon2.id}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for appt in resp.data['appointments']:
            self.assertEqual(appt['service_name'], 'Escova')

    def test_dashboard_filter_by_status(self):
        self._auth_owner()
        self._create_today_appointments()
        resp = self.client.get('/api/v1/dashboard?status=completed,confirmed')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['cancelled_appointments'], 0)

    def test_dashboard_filter_by_date(self):
        self._auth_owner()
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        resp = self.client.get(f'/api/v1/dashboard?date={tomorrow}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_appointments'], 0)

    def test_dashboard_filter_by_date_range(self):
        self._auth_owner()
        self._create_today_appointments()
        today = timezone.localdate().isoformat()
        resp = self.client.get(f'/api/v1/dashboard?date_from={today}&date_to={today}')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['total_appointments'], 1)

    def test_dashboard_invalid_date_returns_400(self):
        self._auth_owner()
        resp = self.client.get('/api/v1/dashboard?date=invalid')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_dashboard_professional_sees_only_own(self):
        self._auth_prof()
        self._create_today_appointments()
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        for appt in resp.data['appointments']:
            self.assertEqual(appt['professional_name'], 'Maria Profissional')

    def test_dashboard_unauthenticated_fails(self):
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_dashboard_unlinked_user_fails(self):
        self._auth(self.other_user)
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_dashboard_empty_day(self):
        self._auth_owner()
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['total_appointments'], 0)
        self.assertEqual(Decimal(str(resp.data['estimated_revenue'])), Decimal('0.00'))
        self.assertEqual(resp.data['appointments'], [])

    def test_dashboard_multiple_professionals_filter(self):
        self._auth_owner()
        self._create_today_appointments()
        resp = self.client.get(
            f'/api/v1/dashboard?professional={self.employee.id},{self.employee2.id}'
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data['total_appointments'], 2)
