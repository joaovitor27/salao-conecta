"""Testes unitários e de integração para os models e permissões do app core."""
from typing import Any
from unittest.mock import MagicMock

import pytest
from django.db import IntegrityError, transaction
from django.http import Http404
from rest_framework.exceptions import ParseError, PermissionDenied
from rest_framework.test import APIRequestFactory

from auth_users.models import User
from business.models import Employee, Salon
from core.models import Address, City, Country, State
from core.permissions import (
    CanManageAppointments,
    CanManageCustomers,
    CanManageEmployees,
    CanManageFinancials,
    CanManageSalonProfile,
    TenantAccessPermission,
    TenantRole,
)


@pytest.mark.django_db
class TestCoreModels:
    """Testes unitários para Country, State, City e Address."""

    def test_create_location_hierarchy_and_str_representations(self) -> None:
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(
            name='São Paulo', abbreviation='SP', region='Sudeste', country=country
        )
        city = City.objects.create(name='Campinas', state=state)
        address = Address.objects.create(
            street='Av. Francisco Glicério',
            neighborhood='Centro',
            number='1000',
            city=city,
            zip_code='13012-100',
        )

        assert str(country) == 'Brasil'
        assert str(state) == 'São Paulo'
        assert str(city) == 'Campinas - SP'
        assert str(address) == 'Av. Francisco Glicério, 1000 - Campinas - SP'
        assert address.created_at is not None
        assert address.updated_at is not None

    def test_address_default_number_sn(self) -> None:
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(
            name='Rio de Janeiro', abbreviation='RJ', region='Sudeste', country=country
        )
        city = City.objects.create(name='Niterói', state=state)
        address = Address.objects.create(
            street='Rua da Praia',
            neighborhood='Icaraí',
            city=city,
        )
        assert address.number == 'S/N'

    def test_unique_constraints_on_country_state_and_city(self) -> None:
        country = Country.objects.create(name='Brasil', code='BRA')
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                Country.objects.create(name='Brasil', code='BR2')

        state = State.objects.create(
            name='Minas Gerais', abbreviation='MG', region='Sudeste', country=country
        )
        City.objects.create(name='Belo Horizonte', state=state)
        with pytest.raises(IntegrityError):
            with transaction.atomic():
                City.objects.create(name='Belo Horizonte', state=state)


@pytest.mark.django_db
class TestTenantAccessPermission:
    """Testes de permissão multi-tenant (TenantAccessPermission)."""

    @pytest.fixture
    def setup_salon(self) -> dict[str, Any]:
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(
            name='São Paulo', abbreviation='SP', region='Sudeste', country=country
        )
        city = City.objects.create(name='São Paulo', state=state)
        address = Address.objects.create(
            street='Rua Augusta', neighborhood='Consolação', number='500', city=city
        )
        owner = User.objects.create_user(email='owner@core.com', password='Password123!')
        manager = User.objects.create_user(email='manager@core.com', password='Password123!')
        outsider = User.objects.create_user(email='outsider@core.com', password='Password123!')

        salon = Salon.objects.create(
            name='Salão Core', slug='salao-core', email='contato@core.com', address=address
        )
        salon.owners.add(owner)

        emp_manager = Employee.objects.create(
            salon=salon,
            user=manager,
            full_name='Gerente Core',
            cpf_cnpj='11122233344',
            role=Employee.Role.MANAGER,
            is_schedulable=False,
        )

        return {
            'salon': salon,
            'owner': owner,
            'manager': manager,
            'emp_manager': emp_manager,
            'outsider': outsider,
        }

    def test_unauthenticated_user_denied(self) -> None:
        factory = APIRequestFactory()
        request = factory.get('/api/v1/appointments')
        request.user = MagicMock(is_authenticated=False)

        perm = TenantAccessPermission()
        assert perm.has_permission(request, view=None) is False

    def test_missing_tenant_header_raises_parse_error(self, setup_salon: dict[str, Any]) -> None:
        factory = APIRequestFactory()
        request = factory.get('/api/v1/appointments')
        request.user = setup_salon['owner']

        perm = TenantAccessPermission()
        with pytest.raises(ParseError, match='X-Tenant-Slug é obrigatório'):
            perm.has_permission(request, view=None)

    def test_nonexistent_or_inactive_salon_raises_404(self, setup_salon: dict[str, Any]) -> None:
        factory = APIRequestFactory()
        request = factory.get('/api/v1/appointments', HTTP_X_TENANT_SLUG='salao-inexistente')
        request.user = setup_salon['owner']

        perm = TenantAccessPermission()
        with pytest.raises(Http404):
            perm.has_permission(request, view=None)

    def test_owner_access_populates_salon_and_role(self, setup_salon: dict[str, Any]) -> None:
        factory = APIRequestFactory()
        request = factory.get('/api/v1/appointments', HTTP_X_TENANT_SLUG='salao-core')
        request.user = setup_salon['owner']

        perm = TenantAccessPermission()
        assert perm.has_permission(request, view=None) is True
        assert request.salon == setup_salon['salon']
        assert request.tenant_role == TenantRole.OWNER
        assert request.employee_id is None

    def test_employee_access_populates_role_and_employee_id(
        self, setup_salon: dict[str, Any]
    ) -> None:
        factory = APIRequestFactory()
        request = factory.get('/api/v1/appointments', HTTP_X_TENANT_SLUG='salao-core')
        request.user = setup_salon['manager']

        perm = TenantAccessPermission()
        assert perm.has_permission(request, view=None) is True
        assert request.tenant_role == TenantRole.MANAGER
        assert request.employee_id == setup_salon['emp_manager'].id

    def test_outsider_user_raises_permission_denied(self, setup_salon: dict[str, Any]) -> None:
        factory = APIRequestFactory()
        request = factory.get('/api/v1/appointments', HTTP_X_TENANT_SLUG='salao-core')
        request.user = setup_salon['outsider']

        perm = TenantAccessPermission()
        with pytest.raises(PermissionDenied, match='Você não tem vínculo com este salão'):
            perm.has_permission(request, view=None)


class TestRoleGranularPermissions:
    """Testes unitários sem banco para as classes de permissão por papel (Role)."""

    @pytest.mark.parametrize(
        ('role', 'expected'),
        [
            (TenantRole.OWNER, True),
            (TenantRole.MANAGER, True),
            (TenantRole.FINANCIAL, True),
            (TenantRole.RECEPTIONIST, False),
            (None, False),
        ],
    )
    def test_can_manage_financials(self, role: str | None, expected: bool) -> None:
        request = MagicMock(tenant_role=role)
        assert CanManageFinancials().has_permission(request, view=None) is expected

    @pytest.mark.parametrize(
        ('role', 'method', 'expected'),
        [
            (TenantRole.OWNER, 'PATCH', True),
            (TenantRole.MANAGER, 'PATCH', True),
            (TenantRole.RECEPTIONIST, 'GET', True),
            (TenantRole.RECEPTIONIST, 'PATCH', False),
            (TenantRole.FINANCIAL, 'GET', True),
            (TenantRole.FINANCIAL, 'DELETE', False),
            (None, 'GET', False),
        ],
    )
    def test_can_manage_salon_profile_and_employees(
        self, role: str | None, method: str, expected: bool
    ) -> None:
        request = MagicMock(tenant_role=role, method=method)
        assert CanManageSalonProfile().has_permission(request, view=None) is expected
        assert CanManageEmployees().has_permission(request, view=None) is expected

    @pytest.mark.parametrize(
        ('role', 'method', 'expected'),
        [
            (TenantRole.OWNER, 'POST', True),
            (TenantRole.MANAGER, 'POST', True),
            (TenantRole.RECEPTIONIST, 'POST', True),
            (TenantRole.FINANCIAL, 'GET', True),
            (TenantRole.FINANCIAL, 'POST', False),
            (None, 'GET', False),
        ],
    )
    def test_can_manage_customers_and_appointments(
        self, role: str | None, method: str, expected: bool
    ) -> None:
        request = MagicMock(tenant_role=role, method=method)
        assert CanManageCustomers().has_permission(request, view=None) is expected
        assert CanManageAppointments().has_permission(request, view=None) is expected
