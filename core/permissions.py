from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.exceptions import PermissionDenied, ParseError
from django.shortcuts import get_object_or_404

from business.models import Salon, Employee


class TenantRole:
    OWNER = 'owner'
    MANAGER = 'manager'
    RECEPTIONIST = 'receptionist'
    PROFESSIONAL = 'professional'
    SUPPORT = 'support'


class TenantAccessPermission(BasePermission):
    """
    Descobre o Salão e o Papel do usuário. Não bloqueia por cargo, apenas por vínculo.
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

        # 1. É Sócio/Dono?
        if salon.owners.filter(id=request.user.id).exists():
            request.tenant_role = TenantRole.OWNER
            return True

        # 2. É Colaborador vinculado?
        employee = Employee.objects.filter(
            salon=salon, user=request.user, is_active=True
        ).first()

        if employee:
            if employee.role == TenantRole.SUPPORT:
                raise PermissionDenied("Seu cargo não possui acesso ao painel do sistema.")

            request.tenant_role = employee.role
            request.employee_id = employee.id
            return True

        raise PermissionDenied("Você não tem vínculo com este salão.")


class IsManagerOrOwner(BasePermission):
    """Acesso exclusivo para dados sensíveis (Financeiro, Configurações do Salão)"""

    def has_permission(self, request: Request, view) -> bool:
        return getattr(request, 'tenant_role', None) in [TenantRole.OWNER, TenantRole.MANAGER]


class CanManageAppointments(BasePermission):
    """Regra de quem pode mexer na Agenda"""

    def has_permission(self, request: Request, view) -> bool:
        role = getattr(request, 'tenant_role', None)

        # Gerência e Recepção têm acesso total à agenda
        if role in [TenantRole.OWNER, TenantRole.MANAGER, TenantRole.RECEPTIONIST]:
            return True

        # Profissionais têm acesso restrito (Só podem VER e ATUALIZAR STATUS para concluído)
        if role == TenantRole.PROFESSIONAL:
            # Permite GET, HEAD, OPTIONS e PATCH
            if request.method in ['GET', 'HEAD', 'OPTIONS', 'PATCH']:
                return True

        return False
