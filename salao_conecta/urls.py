"""
URL configuration for salao_conecta project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, reverse_lazy
from django.views.generic import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView, TokenVerifyView

urlpatterns = [
    path('admin-jvm/', admin.site.urls),
    path('api/v1/', include('manager.urls'), name='manager'),
    path('api/v1/', include('financial.urls'), name='financial'),
    path('api/v1/', include('business.urls'), name='business'),
    path('api/v1/', include('auth_users.urls'), name='auth_users'),
    path('', RedirectView.as_view(url=reverse_lazy('swagger-ui')), name='home'),
    path('api/', RedirectView.as_view(url=reverse_lazy('swagger-ui')), name='home'),
    path('docs/', RedirectView.as_view(url=reverse_lazy('swagger-ui')), name='home'),
    path('api/schema', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

if settings.DEBUG:
    # Servir uploads (logo do salão, imagens de serviços) em desenvolvimento
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
