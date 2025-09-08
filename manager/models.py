from django.db import models
from django.contrib.auth.models import AbstractUser, Group, Permission


# --- Base Models ---
class TimeStampedModel(models.Model):
    """Abstract model with creation and modification date fields."""
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Data de Criação")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Última Atualização")

    class Meta:
        abstract = True


class Country(TimeStampedModel):
    """Model to represent a country."""
    name = models.CharField(max_length=100, unique=True, verbose_name="Nome do País")
    code = models.CharField(max_length=3, unique=True, verbose_name="Código do País")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "País"
        verbose_name_plural = "Países"
        db_table = "country"


class State(TimeStampedModel):
    """Model to represent a state."""
    name = models.CharField(max_length=100, unique=True, verbose_name="Nome do Estado")
    abbreviation = models.CharField(max_length=2, unique=True, verbose_name="Abreviação")
    region = models.CharField(max_length=100, verbose_name="Região")
    country = models.ForeignKey(Country, on_delete=models.CASCADE, related_name='states', verbose_name="País")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Estado"
        verbose_name_plural = "Estados"
        db_table = "state"


class City(TimeStampedModel):
    """Model to represent a city."""
    name = models.CharField(max_length=100, verbose_name="Nome da Cidade")
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name='cities', verbose_name="Estado")

    class Meta:
        unique_together = ('name', 'state')
        verbose_name = "Cidade"
        verbose_name_plural = "Cidades"
        db_table = "city"

    def __str__(self):
        return f"{self.name} - {self.state.abbreviation}"


class Address(TimeStampedModel):
    """Model to represent an address."""
    street = models.CharField(max_length=255, verbose_name="Rua", db_index=True)
    neighborhood = models.CharField(max_length=255, verbose_name="Bairro", db_index=True)
    number = models.CharField(max_length=20, verbose_name="Número", default="S/N",
                              help_text="Use 'S/N' se não houver número.", db_index=True)
    complement = models.CharField(max_length=255, blank=True, null=True, verbose_name="Complemento")
    reference = models.CharField(max_length=255, blank=True, null=True, verbose_name="Referência")
    latitude = models.FloatField(blank=True, null=True, verbose_name="Latitude")
    longitude = models.FloatField(blank=True, null=True, verbose_name="Longitude")
    city = models.ForeignKey(City, on_delete=models.PROTECT, related_name='addresses', verbose_name="Cidade")
    zip_code = models.CharField(max_length=20, verbose_name="CEP", db_index=True, help_text="Formato: 00000-000",
                                blank=True, null=True, )

    def __str__(self):
        return f"{self.street}, {self.number} - {self.city}"

    class Meta:
        verbose_name = "Endereço"
        verbose_name_plural = "Endereços"
        db_table = "address"


class UserType(models.TextChoices):
    CLIENT = "client", "Cliente"
    PROFESSIONAL = "professional", "Profissional"
    SALON_OWNER = "salon_owner", "Dono de Salão"


# --- Custom User Model ---
class User(AbstractUser, TimeStampedModel):
    """Custom user model with additional fields."""
    user_type = models.CharField(max_length=50, choices=UserType.choices, default="client")
    phone_number = models.CharField(max_length=20, blank=True, null=True, verbose_name="Número de Telefone")
    bio = models.TextField(blank=True, null=True, verbose_name="Biografia")
    profile_picture = models.ImageField(upload_to='profile_pics/', blank=True, null=True, verbose_name="Foto de Perfil")
    email = models.EmailField(unique=True, verbose_name="Email", db_index=True)
    groups = models.ManyToManyField(
        Group,
        verbose_name='groups',
        blank=True,
        help_text='The groups this user belongs to. A user will get all permissions granted to each of their groups.',
        related_name="gestao_user_groups",  # NOME EXCLUSIVO
        related_query_name="gestao_user",
    )
    user_permissions = models.ManyToManyField(
        Permission,
        verbose_name='user permissions',
        blank=True,
        help_text='Specific permissions for this user.',
        related_name="gestao_user_permissions",  # NOME EXCLUSIVO
        related_query_name="gestao_user",
    )


# --- Business Models ---
class Salon(TimeStampedModel):
    """Model to represent a beauty salon."""
    name = models.CharField(max_length=100, unique=True, verbose_name="Nome do Salão", db_index=True)
    description = models.TextField(blank=True, verbose_name="Descrição")
    address = models.ForeignKey(Address, on_delete=models.PROTECT, verbose_name="Endereço do Salão",
                                related_name="salon_address")
    email = models.EmailField(unique=True, help_text="Email para contato e login.", verbose_name="Email de Contato")
    phone_number = models.CharField(max_length=20, blank=True, null=True, verbose_name="Telefone de Contato",
                                    db_index=True)
    website = models.URLField(blank=True, null=True, verbose_name="Website")
    slug = models.SlugField(max_length=100, unique=True, help_text="URL amigável para o salão.", verbose_name="Slug")
    logo = models.ImageField(upload_to='salon_logos/', blank=True, null=True, verbose_name="Logo do Salão")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    operating_hours = models.JSONField(default=dict, verbose_name="Horário de Funcionamento")
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='owned_salons', help_text="Dono do salão.")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Salão"
        verbose_name_plural = "Salões"
        ordering = ['name']
        db_table = "salon"


class Professional(TimeStampedModel):
    """Model to represent a professional working at a salon."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='professional_info',
                                help_text="Informações de login do profissional.")
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='professionals', verbose_name="Salão")
    full_name = models.CharField(max_length=200, verbose_name="Nome Completo")
    specialties = models.ManyToManyField('Service', related_name='professionals', verbose_name="Especialidades")
    profile_picture = models.ImageField(upload_to='professional_pics/', blank=True, null=True,
                                        verbose_name="Foto do Profissional")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")

    def __str__(self):
        return self.full_name

    class Meta:
        verbose_name = "Profissional"
        verbose_name_plural = "Profissionais"
        ordering = ['full_name']
        db_table = "professional"


class Service(TimeStampedModel):
    """Model for a service offered by a salon."""
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='services', verbose_name="Salão")
    name = models.CharField(max_length=100, verbose_name="Nome do Serviço")
    description = models.TextField(blank=True, verbose_name="Descrição do Serviço")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Preço")
    duration_minutes = models.IntegerField(verbose_name="Duração em minutos")

    def __str__(self):
        return f"{self.name} ({self.salon.name})"

    class Meta:
        verbose_name = "Serviço"
        verbose_name_plural = "Serviços"
        ordering = ['name']
        unique_together = ('salon', 'name')
        db_table = "service"


class AppointmentStatus(models.TextChoices):
    PENDING = "pending", "Pendente"
    CONFIRMED = "confirmed", "Confirmado"
    COMPLETED = "completed", "Concluído"
    CANCELLED = "cancelled", "Cancelado"


class Appointment(TimeStampedModel):
    """Model for a client appointment."""
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='appointments', verbose_name="Salão")
    client = models.ForeignKey(User, on_delete=models.CASCADE, related_name='my_appointments', verbose_name="Cliente")
    professional = models.ForeignKey(Professional, on_delete=models.CASCADE, related_name='received_appointments',
                                     verbose_name="Profissional", null=True, blank=True)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, related_name='service_appointments',
                                verbose_name="Serviço Agendado")
    date_time = models.DateTimeField(verbose_name="Data e Hora", db_index=True)
    notes = models.TextField(blank=True, null=True, verbose_name="Notas Adicionais")
    status = models.CharField(max_length=50, choices=AppointmentStatus.choices, default='pending',
                              verbose_name="Status", db_index=True)

    def __str__(self):
        return f"Agendamento de {self.client.username} em {self.date_time}"

    class Meta:
        verbose_name = "Agendamento"
        verbose_name_plural = "Agendamentos"
        ordering = ['date_time']
        unique_together = ('professional', 'date_time')
        db_table = "appointment"
