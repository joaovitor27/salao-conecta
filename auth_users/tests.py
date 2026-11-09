"""Testes unitários e de integração para autenticação, usuários e JWT (auth_users)."""
from typing import Any

import pytest
from rest_framework import status
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from auth_users.models import User
from auth_users.serializers import UserCreateSerializer
from business.models import Employee, Salon
from core.models import Address, City, Country, State


@pytest.mark.django_db
class TestUserModelAndManager:
    """Testes unitários para User e UserManager."""

    def test_create_user_with_valid_email(self) -> None:
        user = User.objects.create_user(
            email='CLIENTE@Salao.com',
            password='StrongPassword123!',
            first_name='Maria',
            last_name='Silva',
            phone_number='11988887777',
        )
        assert user.email == 'CLIENTE@salao.com'
        assert user.check_password('StrongPassword123!') is True
        assert user.is_staff is False
        assert user.is_superuser is False
        assert str(user) == 'Maria Silva (CLIENTE@salao.com)'

    def test_create_user_without_email_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match='O e-mail é obrigatório'):
            User.objects.create_user(email='', password='StrongPassword123!')

    def test_create_superuser_success_and_flags_validation(self) -> None:
        admin = User.objects.create_superuser(email='admin@salao.com', password='AdminPassword123!')
        assert admin.is_staff is True
        assert admin.is_superuser is True
        assert admin.is_active is True

        with pytest.raises(ValueError, match='is_staff=True'):
            User.objects.create_superuser(
                email='badstaff@salao.com', password='AdminPassword123!', is_staff=False
            )

        with pytest.raises(ValueError, match='is_superuser=True'):
            User.objects.create_superuser(
                email='badsuper@salao.com', password='AdminPassword123!', is_superuser=False
            )


class TestUserCreateSerializerValidation:
    """Testes unitários de validação de payload e sanitização no UserCreateSerializer."""

    @pytest.mark.parametrize(
        ('raw_phone', 'expected'),
        [
            ('(11) 98765-4321', '11987654321'),
            ('1133445566', '1133445566'),
        ],
    )
    def test_validate_phone_number_happy_path(self, raw_phone: str, expected: str) -> None:
        assert UserCreateSerializer.validate_phone_number(raw_phone) == expected

    @pytest.mark.parametrize(
        'invalid_phone',
        ['12345', '11999999999999', 'abcdef', ''],
    )
    def test_validate_phone_number_rejects_invalid_length(self, invalid_phone: str) -> None:
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError, match='Formato de telefone inválido'):
            UserCreateSerializer.validate_phone_number(invalid_phone)

    def test_validate_first_name_normalizes_title_case(self) -> None:
        assert UserCreateSerializer.validate_first_name('joão') == 'João'

    @pytest.mark.parametrize(
        'malicious_or_invalid_name',
        [
            '{{7*7}}',
            '<script>alert(1)</script>',
            'Joao123',
            'Nome Composto',
        ],
    )
    def test_validate_first_name_rejects_ssti_xss_and_non_alpha(
        self, malicious_or_invalid_name: str
    ) -> None:
        from rest_framework.exceptions import ValidationError

        with pytest.raises(ValidationError, match='O nome deve conter apenas letras'):
            UserCreateSerializer.validate_first_name(malicious_or_invalid_name)


@pytest.mark.django_db
class TestAuthViewsIntegration:
    """Testes de integração para registro, login, /auth/me e logout."""

    @pytest.fixture(autouse=True)
    def disable_throttling(self, settings: Any) -> None:
        settings.REST_FRAMEWORK = {
            **settings.REST_FRAMEWORK,
            'DEFAULT_THROTTLE_CLASSES': [],
            'DEFAULT_THROTTLE_RATES': {'login_attempts': '100/minute'},
        }

    @pytest.fixture
    def api_client(self) -> APIClient:
        return APIClient()

    def test_register_user_happy_path_and_invalid_payload(self, api_client: APIClient) -> None:
        payload = {
            'first_name': ' Fernanda',
            'last_name': 'Costa',
            'email': 'fernanda@teste.com',
            'phone_number': '(21) 99876-5432',
            'password': 'SafePassword987!',
        }
        resp = api_client.post('/api/v1/auth/register', payload, format='json')
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data['first_name'] == 'Fernanda'
        assert resp.data['phone_number'] == '21998765432'
        assert 'password' not in resp.data

        # Rejeita payload SSTI no first_name
        bad_resp = api_client.post(
            '/api/v1/auth/register',
            {**payload, 'email': 'outro@teste.com', 'first_name': '{{config}}'},
            format='json',
        )
        assert bad_resp.status_code == status.HTTP_400_BAD_REQUEST
        assert 'first_name' in bad_resp.data

    def test_login_and_current_user_me_with_salons(self, api_client: APIClient) -> None:
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(
            name='São Paulo', abbreviation='SP', region='Sudeste', country=country
        )
        city = City.objects.create(name='São Paulo', state=state)
        address = Address.objects.create(
            street='Rua A', neighborhood='Bairro B', number='10', city=city
        )

        user = User.objects.create_user(
            email='dono@salao.com',
            password='LoginPass123!',
            first_name='Carlos',
            last_name='Dono',
        )
        salon_owned = Salon.objects.create(
            name='Salão Dono', slug='salao-dono', email='dono@s.com', address=address
        )
        salon_owned.owners.add(user)

        salon_staff = Salon.objects.create(
            name='Salão Staff', slug='salao-staff', email='staff@s.com', address=address
        )
        emp = Employee.objects.create(
            salon=salon_staff,
            user=user,
            full_name='Carlos Gerente',
            cpf_cnpj='99988877766',
            role=Employee.Role.MANAGER,
            is_schedulable=False,
        )

        login_resp = api_client.post(
            '/api/v1/auth/login',
            {'email': 'dono@salao.com', 'password': 'LoginPass123!'},
            format='json',
        )
        assert login_resp.status_code == status.HTTP_200_OK
        assert 'access' in login_resp.data
        assert 'refresh' in login_resp.data

        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {login_resp.data['access']}")
        me_resp = api_client.get('/api/v1/auth/me')
        assert me_resp.status_code == status.HTTP_200_OK
        salons_by_slug: dict[str, Any] = {s['slug']: s for s in me_resp.data['salons']}
        assert salons_by_slug['salao-dono']['role'] == 'owner'
        assert salons_by_slug['salao-staff']['role'] == Employee.Role.MANAGER
        assert salons_by_slug['salao-staff']['employee_id'] == str(emp.id)

    def test_logout_blacklists_refresh_token_and_validates_errors(
        self, api_client: APIClient
    ) -> None:
        user = User.objects.create_user(email='logout@salao.com', password='LogoutPass123!')
        refresh = RefreshToken.for_user(user)
        access = str(AccessToken.for_user(user))
        api_client.credentials(HTTP_AUTHORIZATION=f'Bearer {access}')

        # Sem refresh_token -> 400
        missing_resp = api_client.post('/api/v1/auth/logout', {}, format='json')
        assert missing_resp.status_code == status.HTTP_400_BAD_REQUEST

        # Com token inválido -> 400
        invalid_resp = api_client.post(
            '/api/v1/auth/logout', {'refresh_token': 'token-invalido'}, format='json'
        )
        assert invalid_resp.status_code == status.HTTP_400_BAD_REQUEST

        # Com refresh_token válido -> 205
        ok_resp = api_client.post(
            '/api/v1/auth/logout', {'refresh_token': str(refresh)}, format='json'
        )
        assert ok_resp.status_code == status.HTTP_205_RESET_CONTENT
