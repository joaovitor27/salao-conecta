import uuid

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeOperators, DateTimeRangeField
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _


from core.models import TimeStampedModel, Address

DEFAULT_PRIMARY_COLOR = '#233B5C'

hex_color_validator = RegexValidator(
    regex=r'^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$',
    message="Informe uma cor em hexadecimal. Ex: #233B5C.",
)


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

    # ── Identidade visual (white-label por salão) ────────────
    display_name = models.CharField(
        max_length=60, blank=True, verbose_name="Nome de Exibição",
        help_text="Nome curto exibido no menu e cabeçalho. Se vazio, usa o nome do salão.",
    )
    tagline = models.CharField(
        max_length=80, blank=True, verbose_name="Slogan",
        help_text="Frase curta exibida abaixo do nome. Ex: A beleza na palma da sua mão.",
    )
    primary_color = models.CharField(
        max_length=7, default=DEFAULT_PRIMARY_COLOR, validators=[hex_color_validator],
        verbose_name="Cor Principal", help_text="Cor base da identidade visual em hexadecimal.",
    )
    secondary_color = models.CharField(
        max_length=7, blank=True, null=True, validators=[hex_color_validator],
        verbose_name="Cor Secundária", help_text="Opcional. Se vazio, é derivada da cor principal.",
    )
    accent_color = models.CharField(
        max_length=7, blank=True, null=True, validators=[hex_color_validator],
        verbose_name="Cor de Destaque", help_text="Opcional. Se vazio, é derivada da cor principal.",
    )

    @property
    def brand_name(self) -> str:
        return self.display_name or self.name

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
        unique_together = ('cpf', 'salon')


class Employee(TimeStampedModel):
    """
    Engloba todos os trabalhadores do salão.
    Profissionais e Apoio NÃO possuem acesso ao sistema (user deve ser null).
    Apenas Owner (via Salon.owners), Manager, Financial e Receptionist fazem login.
    """

    class Role(models.TextChoices):
        MANAGER = 'manager', _('Gerente/Administrador')
        FINANCIAL = 'financial', _('Financeiro')
        RECEPTIONIST = 'receptionist', _('Recepcionista')
        PROFESSIONAL = 'professional', _('Profissional da Beleza')
        SUPPORT = 'support', _('Apoio (Faxina, Manutenção)')

    # Papéis que NÃO podem ter login (User vinculado)
    ROLES_WITHOUT_LOGIN = {Role.PROFESSIONAL, Role.SUPPORT}

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

    def clean(self):
        super().clean()
        if self.role in self.ROLES_WITHOUT_LOGIN and self.user is not None:
            raise ValidationError(
                "Profissionais e Apoio não possuem acesso ao sistema. "
                "Remova o usuário vinculado."
            )

    def save(self, *args, **kwargs):
        self.clean()
        super().save(*args, **kwargs)

    class Meta:
        db_table = "employee"
        unique_together = ('cpf_cnpj', 'salon')


class EmployeeService(TimeStampedModel):
    """Vincula profissional aos serviços que ele realiza, com comissão personalizada."""
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name='employee_services')
    service = models.ForeignKey('ServiceSalon', on_delete=models.CASCADE, related_name='employee_services')
    commission_rate = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        help_text="Comissão específica para este serviço. Se null, usa a comissão padrão do profissional."
    )

    class Meta:
        db_table = "employee_services"
        unique_together = ('employee', 'service')
        verbose_name = "Serviço do Profissional"
        verbose_name_plural = "Serviços dos Profissionais"


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


class AppointmentItem(TimeStampedModel):
    """Itens do Agendamento (Serviços selecionados com preços possivelmente customizados)."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    appointment = models.ForeignKey('Appointment', on_delete=models.CASCADE, related_name='items')
    service = models.ForeignKey(ServiceSalon, on_delete=models.PROTECT, related_name='appointment_items')
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Preço Cobrado")
    duration_minutes = models.IntegerField(verbose_name="Duração (minutos)")

    class Meta:
        db_table = "appointment_items"
        verbose_name = "Item do Agendamento"
        verbose_name_plural = "Itens do Agendamento"


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
    total_price = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Valor Total dos Serviços")
    time_range = DateTimeRangeField()
    discount = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Desconto")
    notes = models.TextField(blank=True, null=True, verbose_name="Notas Adicionais")
    status = models.CharField(max_length=50, choices=Status, default=Status.PENDING,
                              verbose_name="Status", db_index=True)

    def __str__(self):
        return f"Agendamento de {self.client.name} em {self.time_range.start} no {self.salon.name} - {self.status.upper()}"

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
