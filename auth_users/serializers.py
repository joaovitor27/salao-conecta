import re
from rest_framework.exceptions import ValidationError

from rest_framework import serializers
from .models import User
from django.contrib.auth.password_validation import validate_password


class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(
        write_only=True,
        required=True,
        validators=[validate_password],
        style={'input_type': 'password'}
    )

    class Meta:
        model = User
        fields = ('id', 'first_name', 'last_name', 'email', 'phone_number', 'password')

    @staticmethod
    def validate_phone_number(value: str) -> str:
        """Limpa o telefone, removendo tudo que não for número."""
        cleaned_phone = re.sub(r'\D', '', value)
        if len(cleaned_phone) < 10 or len(cleaned_phone) > 11:
            raise ValidationError("Formato de telefone inválido.")

        return cleaned_phone

    @staticmethod
    def validate_first_name(value: str) -> str:
        """Sanitização básica contra scripts e caracteres inválidos."""
        if not value.isalpha():
            raise ValidationError("O nome deve conter apenas letras.")
        return value.strip().title()


class UserSerializer(serializers.ModelSerializer):
    salons = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ('id', 'username', 'first_name', 'last_name', 'email', 'phone_number', 'salons')
        
    def get_salons(self, obj):
        from business.models import Salon, Employee
        from django.db.models import Q
        
        # O usuário pode estar vinculado como dono ou funcionário ativo
        salons = Salon.objects.filter(
            Q(owners=obj) | Q(employees__user=obj, employees__is_active=True),
            is_active=True
        ).distinct()
        
        result = []
        for salon in salons:
            is_owner = salon.owners.filter(id=obj.id).exists()
            if is_owner:
                role = 'owner'
                employee_id = None
            else:
                emp = Employee.objects.filter(salon=salon, user=obj, is_active=True).first()
                role = emp.role if emp else None
                employee_id = str(emp.id) if emp else None
            
            if role:
                result.append({
                    "slug": salon.slug, 
                    "name": salon.name,
                    "role": role,
                    "employee_id": employee_id
                })
        return result
