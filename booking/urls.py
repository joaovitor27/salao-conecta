from django.urls import path

from booking.views import (
    PublicSalonView,
    PublicServicesView,
    PublicProfessionalsView,
    PublicAvailabilityView,
    PublicBookingCreateView,
)

urlpatterns = [
    path('booking/<slug:slug>/salon', PublicSalonView.as_view(), name='public-booking-salon'),
    path('booking/<slug:slug>/services', PublicServicesView.as_view(), name='public-booking-services'),
    path('booking/<slug:slug>/professionals', PublicProfessionalsView.as_view(), name='public-booking-professionals'),
    path('booking/<slug:slug>/availability', PublicAvailabilityView.as_view(), name='public-booking-availability'),
    path('booking/<slug:slug>/appointments', PublicBookingCreateView.as_view(), name='public-booking-create'),
]
