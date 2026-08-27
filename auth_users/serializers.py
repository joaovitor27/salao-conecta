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
        
        # Salões como dono
        owned_salons = Salon.objects.filter(owners=obj, is_active=True)
        # Salões como funcionário (com login)
        employee_salons = Salon.objects.filter(
            employees__user=obj, employees__is_active=True
        ).exclude(id__in=owned_salons).distinct()

        result = []
        for salon in owned_salons:
            emp = Employee.objects.filter(salon=salon, user=obj, is_active=True).first()
            result.append({
                "slug": salon.slug,
                "name": salon.name,
                "role": "owner",
                "employee_id": str(emp.id) if emp else None,
            })

        for salon in employee_salons:
            emp = Employee.objects.filter(salon=salon, user=obj, is_active=True).first()
            if emp and emp.role not in Employee.ROLES_WITHOUT_LOGIN:
                result.append({
                    "slug": salon.slug,
                    "name": salon.name,
                    "role": emp.role,
                    "employee_id": str(emp.id),
                })

        return result
