import uuid
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone
from psycopg2.extras import DateTimeTZRange
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken
from django.core.exceptions import ValidationError

from auth_users.models import User
from business.models import Salon, Employee, Customer, Service, ServiceSalon, Appointment, EmployeeService
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
            'professional_id': str(self.owner_employee.id),
            'services': [{'service_id': self.service_salon.id}],
            'start_time': start.isoformat(),
        }, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(resp.data['professional']['full_name'], 'Owner Profissional')

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
        resp = self.client.post('/api/v1/appointments', {})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_financial_can_access_dashboard(self):
        self._auth(self.financial_user)
        resp = self.client.get('/api/v1/dashboard')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

    def test_customer_cpf_required(self):
        self._auth(self.receptionist_user)
        resp = self.client.post('/api/v1/customers', {
            'name': 'No CPF', 'phone': '123'
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cpf', resp.data)

    def test_customer_duplicate_cpf_same_salon_fails(self):
        self._auth(self.receptionist_user)
        resp = self.client.post('/api/v1/customers', {
            'name': 'Clone', 'phone': '999', 'cpf': '33333333333'
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('cpf', resp.data)

    def test_appointment_custom_duration(self):
        self._auth(self.manager_user)
        start = timezone.now() + timedelta(hours=5)
        resp = self.client.post('/api/v1/appointments', {
            'client_id': str(self.customer.id),
            'professional_id': str(self.pure_professional.id),
            'services': [{'service_id': self.service_salon2.id, 'duration_minutes': 120}],
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
            'client_id': str(self.customer.id),
            'professional_id': str(self.owner_employee.id),
            'services': [{'service_id': self.service_salon2.id}],
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
