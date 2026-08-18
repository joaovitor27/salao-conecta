from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView
from auth_users.views import RegisterUserView, LogoutView, CurrentUserView, TokenObtainPairViewMy, TokenRefreshViewMy

urlpatterns = [
    path('auth/register', RegisterUserView.as_view(), name='auth_register'),
    path('auth/login', TokenObtainPairViewMy.as_view(), name='auth_login'),
    path('auth/refresh', TokenRefreshViewMy.as_view(), name='auth_refresh'),
    path('auth/logout', LogoutView.as_view(), name='auth_logout'),
    path('auth/me', CurrentUserView.as_view(), name='auth_me'),
]
