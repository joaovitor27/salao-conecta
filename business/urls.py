from django.urls import path

from business.views import (
    AppointmentListCreateView,
    AppointmentDetailView,
    AppointmentStatusUpdateView,
    DashboardView,
    AvailabilityView,
    CustomerListCreateView,
    EmployeeListView,
    EmployeeListCreateView,
    EmployeeDetailView,
    ServiceSalonListView,
    SalonBrandingView,
    PublicSalonBrandingView,
    SalonProfileView,
    SalonLogoView,
)

urlpatterns = [
    # ── Salão: perfil e identidade visual ────────────────────
    path('salon/branding', SalonBrandingView.as_view(), name='salon-branding'),
    path('salon/profile', SalonProfileView.as_view(), name='salon-profile'),
    path('salon/logo', SalonLogoView.as_view(), name='salon-logo'),
    path('public/salons/<slug:slug>/branding', PublicSalonBrandingView.as_view(), name='public-salon-branding'),

    # ── Dashboard ────────────────────────────────────────────
    path('dashboard', DashboardView.as_view(), name='dashboard'),

    # ── Agendamentos CRUD ────────────────────────────────────
    path('appointments', AppointmentListCreateView.as_view(), name='appointment-list-create'),
    path('appointments/<int:pk>', AppointmentDetailView.as_view(), name='appointment-detail'),
    path('appointments/<int:pk>/status', AppointmentStatusUpdateView.as_view(), name='appointment-status'),
    path('availability', AvailabilityView.as_view(), name='availability'),

    # ── Funcionários CRUD ────────────────────────────────────
    path('staff', EmployeeListCreateView.as_view(), name='employee-list-create'),
    path('staff/<uuid:pk>', EmployeeDetailView.as_view(), name='employee-detail'),

    # ── Auxiliares para Dropdowns ────────────────────────────
    path('customers', CustomerListCreateView.as_view(), name='customer-list-create'),
    path('employees', EmployeeListView.as_view(), name='employee-list'),
    path('services', ServiceSalonListView.as_view(), name='service-list'),
]
