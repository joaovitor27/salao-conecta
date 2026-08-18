import uuid

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators, DateTimeRangeField
from django.db import models
from django.utils.translation import gettext_lazy as _


from core.models import TimeStampedModel, Address


class Salon(TimeStampedModel):
    """Model to represent a beauty salon."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    slug = models.SlugField(unique=True, db_index=True, help_text="URL amigável para o salão.", verbose_name="Slug")
    name = models.CharField(max_length=100, unique=True, verbose_name="Nome do Salão", db_index=True)
    description = models.TextField(blank=True, verbose_name="Descrição")
    address = models.ForeignKey(Address, on_delete=models.PROTECT, verbose_name="Endereço do Salão",
                                related_name="salon_address")
    email = models.EmailField(unique=True, help_text="Email para contato e login.", verbose_name="Email de Contato")
    phone_number = models.CharField(max_length=20, blank=True, null=True, verbose_name="Telefone de Contato",
                                    db_index=True)
    website = models.URLField(blank=True, null=True, verbose_name="Website")
    logo = models.ImageField(upload_to='salon_logo/', blank=True, null=True, verbose_name="Logo do Salão")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")
    operating_hours = models.JSONField(default=dict, verbose_name="Horário de Funcionamento", blank=True, null=True)
    owners = models.ManyToManyField('auth_users.User', related_name='owned_salons')

    def __str__(self):
        return self.slug

    class Meta:
        verbose_name = "Salão"
        verbose_name_plural = "Salões"
        ordering = ['name']
        db_table = "salon"


class Customer(TimeStampedModel):
    """O Chapéu do Cliente: Garante que o Salão só veja os dados do SEU cliente"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='customers')
    user = models.ForeignKey('auth_users.User', on_delete=models.SET_NULL, null=True, blank=True)
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, db_index=True)
    cpf = models.CharField(max_length=20, db_index=True)
    email = models.EmailField(blank=True, null=True)
    birth_date = models.DateField(blank=True, null=True)
    is_active = models.BooleanField(default=True, verbose_name="Ativo")

    class Meta:
        db_table = "customers"
        unique_together = ('phone', 'salon')


class Employee(TimeStampedModel):
    """
    substitui a antiga tabela 'Professional'.
    Engloba todos os trabalhadores do salão (com ou sem acesso ao sistema).
    """

    class Role(models.TextChoices):
        MANAGER = 'manager', _('Gerente/Administrador')
        RECEPTIONIST = 'receptionist', _('Recepcionista')
        PROFESSIONAL = 'professional', _('Profissional da Beleza')
        SUPPORT = 'support', _('Apoio (Faxina, Manutenção)')

    class ContractType(models.TextChoices):
        FIXED = 'fixed', _('Fixo (CLT/Mensalista)')
        COMMISSION = 'commission', _('Comissionado (Autônomo/Parceiro)')

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='employees')
    user = models.ForeignKey('auth_users.User', on_delete=models.SET_NULL, null=True, blank=True)

    full_name = models.CharField(max_length=200)
    cpf_cnpj = models.CharField(max_length=20, db_index=True)

    role = models.CharField(max_length=20, choices=Role, default=Role.PROFESSIONAL)
    contract_type = models.CharField(max_length=20, choices=ContractType, default=ContractType.COMMISSION)

    is_schedulable = models.BooleanField(default=True, help_text="Aparece na agenda para clientes?")

    fixed_salary = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)
    default_commission_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0.00,
                                                  help_text="Ex: 50.00 para 50%")

    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "employee"
        unique_together = ('cpf_cnpj', 'salon')


class ServiceSalon(TimeStampedModel):
    """Model to represent the relationship between a service and a salon."""
    service = models.ForeignKey('Service', on_delete=models.PROTECT, related_name='salons', blank=True, null=True)
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='services')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Preço")
    image = models.ImageField(upload_to='service_images/', blank=True, null=True, verbose_name='Imagem')
    duration_minutes = models.IntegerField(verbose_name="Duração em minutos")
    is_active = models.BooleanField(default=True, verbose_name="Ativo")

    def __str__(self):
        return f"{self.service.name} - {self.salon.name}"

    class Meta:
        verbose_name = 'Serviço do Salão'
        verbose_name_plural = 'Serviços do Salão'
        db_table = 'services_salon'


class Service(TimeStampedModel):
    """Model for a service offered by a salon."""
    name = models.CharField(max_length=100, verbose_name="Nome do Serviço", unique=True, db_index=True)
    description = models.TextField(blank=True, null=True, verbose_name="Descrição do Serviço")

    def __str__(self):
        return f"{self.name} ({self.name})"

    class Meta:
        verbose_name = "Serviço"
        verbose_name_plural = "Serviços"
        ordering = ['name']
        unique_together = ('name',)
        db_table = "services"


class Appointment(TimeStampedModel):
    """Model for a client appointment."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pendente"
        CONFIRMED = "confirmed", "Confirmado"
        COMPLETED = "completed", "Concluído"
        CANCELLED = "cancelled", "Cancelado"

    salon = models.ForeignKey(Salon, on_delete=models.PROTECT, related_name='appointments', verbose_name="Salão")
    client = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='appointments')
    professional = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name='received_appointments',
                                     verbose_name="Profissional", null=True, blank=True)
    service = models.ForeignKey(ServiceSalon, on_delete=models.PROTECT, related_name='service_appointments',
                                verbose_name="Serviço Agendado")
    time_range = DateTimeRangeField()
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Desconto")
    notes = models.TextField(blank=True, null=True, verbose_name="Notas Adicionais")
    status = models.CharField(max_length=50, choices=Status, default=Status.PENDING,
                              verbose_name="Status", db_index=True)

    def __str__(self):
        return f"Agendamento de {self.client.name} em {self.time_range.start} para {self.service.service.name} no {self.salon.name} - {self.status.upper()}"

    class Meta:
        verbose_name = "Agendamento"
        verbose_name_plural = "Agendamentos"
        constraints = [
            ExclusionConstraint(
                name='prevent_double_booking',
                expressions=[
                    ('professional', RangeOperators.EQUAL),
                    ('time_range', RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(status='cancelled')
            )
        ]
        db_table = "appointments"
