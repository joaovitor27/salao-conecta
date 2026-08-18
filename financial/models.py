import uuid

from django.db import models
from django.utils.translation import gettext_lazy as _

from business.models import Salon, Employee
from core.models import TimeStampedModel


class BankAccount(TimeStampedModel):
    """
    Contas do Salão. Pode ser 'Caixa Físico', 'Nubank PJ', 'Mercado Pago'.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='bank_accounts')

    name = models.CharField(max_length=100)  # Ex: "Caixa Gaveta" ou "Banco Inter"
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "bank_accounts"


class FinancialTransaction(TimeStampedModel):
    """
    Registro imutável de entrada e saída.
    """

    class TransactionType(models.TextChoices):
        IN = 'in', _('Entrada')
        OUT = 'out', _('Saída')

    class Category(models.TextChoices):
        SERVICE = 'service', _('Pagamento de Serviço')
        COMMISSION = 'commission', _('Pagamento de Comissão')
        SALARY = 'salary', _('Salário Fixo')
        UTILITY = 'utility', _('Despesa Fixa (Água, Luz, Aluguel)')
        SUPPLIER = 'supplier', _('Fornecedores (Produtos)')
        OTHERS = 'others', _('Outros')

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    salon = models.ForeignKey(Salon, on_delete=models.CASCADE, related_name='transactions')
    account = models.ForeignKey(BankAccount, on_delete=models.PROTECT, related_name='transactions')
    type = models.CharField(max_length=10, choices=TransactionType)
    category = models.CharField(max_length=20, choices=Category)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    description = models.CharField(max_length=255)
    appointment = models.ForeignKey('business.Appointment', on_delete=models.SET_NULL, null=True, blank=True)
    employee = models.ForeignKey(Employee, on_delete=models.SET_NULL, null=True, blank=True)

    date_occurred = models.DateField(db_index=True)

    class Meta:
        db_table = "financial_transactions"
        ordering = ['-date_occurred', '-created_at']
