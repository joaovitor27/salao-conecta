from django.contrib import admin

from auth_users.models import User
from business.models import ServiceSalon, Service, Employee, Salon
from core.models import Address, City, State, Country


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = (
        'email',
        'first_name',
        'last_name',
        'phone_number',
        'is_staff',
    )
    search_fields = (
        'email',
        'first_name',
        'phone_number',
    )
    list_filter = (
        'is_staff',
        'is_active',
    )


class EmployeeInline(admin.TabularInline):
    model = Employee
    extra = 1
    fields = (
        'full_name',
        'cpf_cnpj',
        'role',
        'is_schedulable',
        'is_active',
    )


class ServiceSalonInline(admin.TabularInline):
    model = ServiceSalon
    extra = 1
    fields = (
        'service',
        'price',
        'duration_minutes',
        'is_active',
    )


@admin.register(Salon)
class SalonAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'slug',
        'email',
        'is_active',
        'created_at',
    )

    search_fields = (
        'name',
        'slug',
        'email',
    )

    list_filter = (
        'is_active',
        'created_at',
    )

    prepopulated_fields = {
        'slug': ('name',),
    }

    filter_horizontal = ('owners',)

    inlines = [
        EmployeeInline,
        ServiceSalonInline,
    ]


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = (
        'street',
        'number',
        'city',
        'zip_code',
    )

    search_fields = (
        'street',
        'neighborhood',
        'zip_code',
        'city__name',
    )

    autocomplete_fields = (
        'city',
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related(
                'city',
                'city__state',
            )
        )


@admin.register(City)
class CityAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'state',
    )

    search_fields = (
        'name',
        'state__name',
        'state__abbreviation',
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('state')
        )


@admin.register(State)
class StateAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'abbreviation',
        'region',
        'country',
    )

    search_fields = (
        'name',
        'abbreviation',
    )

    autocomplete_fields = (
        'country',
    )

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .select_related('country')
        )


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'code',
    )

    search_fields = (
        'name',
        'code',
    )


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'created_at',
    )

    search_fields = (
        'name',
    )