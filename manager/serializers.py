from djoser.serializers import UserCreateSerializer as DjoserUserCreateSerializer, \
    UserSerializer as DjoserUserSerializer
from .models import User


class UserCreateSerializer(DjoserUserCreateSerializer):
    class Meta(DjoserUserCreateSerializer.Meta):
        model = User
        fields = ('id', 'username', 'email', 'phone_number', 'user_type', 'password')


class UserSerializer(DjoserUserSerializer):
    class Meta(DjoserUserSerializer.Meta):
        model = User
        fields = ('id', 'username', 'email', 'phone_number', 'user_type')
