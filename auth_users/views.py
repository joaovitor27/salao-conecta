from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from auth_users.serializers import UserCreateSerializer, UserSerializer

TAGS = ['Autenticação']


@extend_schema(tags=TAGS, description='Cadastro de usuários')
class RegisterUserView(generics.CreateAPIView):
    """
    Rota pública para clientes (ou profissionais) criarem suas contas.
    """
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login_attempts'
    serializer_class = UserCreateSerializer
    permission_classes = [AllowAny]


@extend_schema(tags=TAGS, description='Logout de usuários')
class LogoutView(APIView):
    """
    Logout Stateless: Pega o Refresh Token enviado e o adiciona à Blacklist.
    Assim, ele não pode ser usado para gerar novos Access Tokens.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data.get("refresh_token")
            if not refresh_token:
                return Response({"detail": "Refresh token é obrigatório."}, status=status.HTTP_400_BAD_REQUEST)

            token = RefreshToken(refresh_token)
            token.blacklist()

            return Response({"detail": "Logout realizado com sucesso."}, status=status.HTTP_205_RESET_CONTENT)
        except Exception:
            return Response({"detail": "Token inválido ou expirado."}, status=status.HTTP_400_BAD_REQUEST)


@extend_schema(tags=TAGS, description='Detalhe de usuário logado')
class CurrentUserView(APIView):
    """
    Retorna os dados do usuário logado (baseado no JWT enviado no header)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

@extend_schema(tags=TAGS, description='Login do usuário')
class TokenObtainPairViewMy(TokenObtainPairView):
    ...

@extend_schema(tags=TAGS, description='Refresh Token do usuário')
class TokenRefreshViewMy(TokenRefreshView):
    ...
