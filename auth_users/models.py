import uuid

from django.contrib.auth.models import PermissionsMixin, AbstractUser
from django.db import models

from auth_users.management.UserManager import UserManager
from core.models import TimeStampedModel


# Create your models here.
# --- Custom User Model ---
class User(AbstractUser, PermissionsMixin, TimeStampedModel):
    """Custom user model with additional fields."""
    objects = UserManager()
    username = None
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    email = models.EmailField(unique=True, verbose_name="Email")
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    phone_number = models.CharField(max_length=20, blank=True, null=True, verbose_name="Número de Telefone")

    class Meta:
        verbose_name = "Usuário"
        verbose_name_plural = "Usuários"
        ordering = ['email']
        db_table = "users"

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name} ({self.email})"