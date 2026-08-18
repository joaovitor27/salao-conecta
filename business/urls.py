from django.urls import path

from business.views import (
    AppointmentListCreateView,
    AppointmentDetailView,
    AppointmentStatusUpdateView,
    DashboardView,
    CustomerListCreateView,
    EmployeeListView,
    ServiceSalonListView,
)

urlpatterns = [
    # ── Dashboard ────────────────────────────────────────────
    path('dashboard', DashboardView.as_view(), name='dashboard'),

    # ── Agendamentos CRUD ────────────────────────────────────
    path('appointments', AppointmentListCreateView.as_view(), name='appointment-list-create'),
    path('appointments/<int:pk>', AppointmentDetailView.as_view(), name='appointment-detail'),
    path('appointments/<int:pk>/status', AppointmentStatusUpdateView.as_view(), name='appointment-status'),

    # ── Auxiliares para Dropdowns ────────────────────────────
    path('customers', CustomerListCreateView.as_view(), name='customer-list-create'),
    path('employees', EmployeeListView.as_view(), name='employee-list'),
    path('services', ServiceSalonListView.as_view(), name='service-list'),
]
