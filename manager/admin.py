from django.contrib import admin

from auth_users.models import User
from business.models import ServiceSalon, Service, Employee, Salon
from core.models import Address


# ==========================================
# 1. ADMIN DE USUÁRIOS (Identidade Global)
# ==========================================
@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'first_name', 'last_name', 'phone_number', 'is_staff')
    search_fields = ('email', 'first_name', 'phone_number')
    list_filter = ('is_staff', 'is_active')


# ==========================================
# 2. ADMIN DE SALÕES (O Tenant)
# ==========================================
class EmployeeInline(admin.TabularInline):
    """Permite ver e adicionar empregados diretamente na tela do Salão."""
    model = Employee
    extra = 1
    fields = ('full_name', 'cpf_cnpj', 'role', 'is_schedulable', 'is_active')


class ServiceSalonInline(admin.TabularInline):
    """Mostra quais serviços o salão oferece."""
    model = ServiceSalon
    extra = 1
    fields = ('service', 'price', 'duration_minutes', 'is_active')


@admin.register(Salon)
class SalonAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'email', 'is_active', 'created_at')
    search_fields = ('name', 'slug', 'email')
    list_filter = ('is_active', 'created_at')

    # O Slug deve ser gerado automaticamente com base no nome
    prepopulated_fields = {'slug': ('name',)}

    # Campo ManyToMany para selecionar os donos
    filter_horizontal = ('owners',)

    inlines = [EmployeeInline, ServiceSalonInline]


# ==========================================
# 3. OUTROS REGISTROS GLOBAIS
# ==========================================
@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ('street', 'number', 'city', 'zip_code')
    search_fields = ('street', 'zip_code')


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    """
    Catálogo Global de Serviços.
    Ex: Você cria 'Corte de Cabelo' aqui, e cada Salão puxa pra si
    definindo o próprio preço no ServiceSalon.
    """
    list_display = ('name', 'created_at')
    search_fields = ('name',)