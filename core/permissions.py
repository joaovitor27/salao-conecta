from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.exceptions import PermissionDenied, ParseError
from django.shortcuts import get_object_or_404

from business.models import Salon, Employee


class TenantRole:
    OWNER = 'owner'
    MANAGER = 'manager'
    FINANCIAL = 'financial'
    RECEPTIONIST = 'receptionist'
    # professional e support NÃO fazem login, mas mantemos para referência
    PROFESSIONAL = 'professional'
    SUPPORT = 'support'


class TenantAccessPermission(BasePermission):
    """
    Descobre o Salão e o Papel do usuário.
    - Owner: sempre tem acesso; se tiver Employee vinculado, popula employee_id.
    - Manager/Financial/Receptionist: acesso via Employee com user vinculado.
    - Professional/Support: bloqueados (não possuem login).
    """

    def has_permission(self, request: Request, view) -> bool:
        if not request.user or not request.user.is_authenticated:
            return False

        tenant_slug: str | None = request.headers.get('X-Tenant-Slug')
        if not tenant_slug:
            raise ParseError("O cabeçalho X-Tenant-Slug é obrigatório.")

        salon = get_object_or_404(Salon, slug=tenant_slug, is_active=True)

        # INJEÇÃO DE DEPENDÊNCIA (Disponível em qualquer lugar do Request)
        request.salon = salon
        request.tenant_role = None
        request.employee_id = None

        # Buscar Employee vinculado (se existir)
        employee = Employee.objects.filter(
            salon=salon, user=request.user, is_active=True
        ).first()

        # 1. É Sócio/Dono?
        is_owner = salon.owners.filter(id=request.user.id).exists()
        if is_owner:
            request.tenant_role = TenantRole.OWNER
            # Owner que também é profissional → popular employee_id
            request.employee_id = employee.id if employee else None
            return True

        # 2. É Colaborador vinculado?
        if employee:
            if employee.role in Employee.ROLES_WITHOUT_LOGIN:
                raise PermissionDenied("Seu perfil não possui acesso ao sistema.")

            request.tenant_role = employee.role
            request.employee_id = employee.id
            return True

        raise PermissionDenied("Você não tem vínculo com este salão.")


class CanManageFinancials(BasePermission):
    """Acesso a dados financeiros: Owner, Manager e Financial."""

    def has_permission(self, request: Request, view) -> bool:
        return getattr(request, 'tenant_role', None) in [
            TenantRole.OWNER, TenantRole.MANAGER, TenantRole.FINANCIAL
        ]


class CanManageSalonProfile(BasePermission):
    """
    Perfil e identidade visual do salão.
    - Todos os papéis com login podem LER (o tema é aplicado para qualquer usuário).
    - Somente Owner e Manager podem ALTERAR.
    """

    def has_permission(self, request: Request, view) -> bool:
        role = getattr(request, 'tenant_role', None)
        if role is None:
            return False

        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True

        return role in [TenantRole.OWNER, TenantRole.MANAGER]


class CanManageEmployees(BasePermission):
    """
    Cadastro de funcionários.
    - Todos os papéis com login podem LER (necessário para agenda e relatórios).
    - Somente Owner e Manager podem CRIAR, EDITAR ou DESATIVAR.
    """

    def has_permission(self, request: Request, view) -> bool:
        role = getattr(request, 'tenant_role', None)
        if role is None:
            return False

        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True

        return role in [TenantRole.OWNER, TenantRole.MANAGER]


class CanManageCustomers(BasePermission):
    """
    Cadastro de clientes.
    - Todos os papéis com login podem LER.
    - Owner, Manager e Receptionist podem CRIAR e EDITAR.
    """

    def has_permission(self, request: Request, view) -> bool:
        role = getattr(request, 'tenant_role', None)
        if role is None:
            return False

        if request.method in ['GET', 'HEAD', 'OPTIONS']:
            return True

        return role in [TenantRole.OWNER, TenantRole.MANAGER, TenantRole.RECEPTIONIST]


class CanManageAppointments(BasePermission):
    """
    Regra de quem pode mexer na Agenda.
    - Owner, Manager, Receptionist: acesso total.
    - Financial: somente leitura (GET, HEAD, OPTIONS).
    """

    def has_permission(self, request: Request, view) -> bool:
        role = getattr(request, 'tenant_role', None)

        # Gerência e Recepção têm acesso total à agenda
        if role in [TenantRole.OWNER, TenantRole.MANAGER, TenantRole.RECEPTIONIST]:
            return True

        # Financeiro só pode ler a agenda (para relatórios)
        if role == TenantRole.FINANCIAL:
            return request.method in ['GET', 'HEAD', 'OPTIONS']

        return False
