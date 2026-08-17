from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView, TokenObtainPairView

from manager.views import RegisterUserView, LogoutView, CurrentUserView

urlpatterns = [
    # Cadastro e Login
    path('auth/register/', RegisterUserView.as_view(), name='auth_register'),
    path('auth/login/', TokenObtainPairView.as_view(), name='auth_login'),
    path('auth/refresh/', TokenRefreshView.as_view(), name='auth_refresh'),
    path('auth/logout/', LogoutView.as_view(), name='auth_logout'),
    path('auth/me/', CurrentUserView.as_view(), name='auth_me'),
]
