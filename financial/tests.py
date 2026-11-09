"""Testes unitários para os models financeiros (BankAccount e FinancialTransaction)."""
from datetime import date
from decimal import Decimal

import pytest

from business.models import Employee, Salon
from core.models import Address, City, Country, State
from financial.models import BankAccount, FinancialTransaction


@pytest.mark.django_db
class TestFinancialModels:
    """Testes unitários para criação e relacionamento de contas bancárias e transações."""

    def test_create_bank_account_and_financial_transactions(self) -> None:
        country = Country.objects.create(name='Brasil', code='BRA')
        state = State.objects.create(
            name='São Paulo', abbreviation='SP', region='Sudeste', country=country
        )
        city = City.objects.create(name='São Paulo', state=state)
        address = Address.objects.create(
            street='Rua Oscar Freire', neighborhood='Jardins', number='100', city=city
        )
        salon = Salon.objects.create(
            name='Salão Financeiro',
            slug='salao-financeiro',
            email='fin@salao.com',
            address=address,
        )
        employee = Employee.objects.create(
            salon=salon,
            full_name='Beatriz Profissional',
            cpf_cnpj='12312312399',
            role=Employee.Role.PROFESSIONAL,
        )

        account = BankAccount.objects.create(
            salon=salon,
            name='Conta PJ Nubank',
            balance=Decimal('1500.00'),
        )
        assert account.is_active is True
        assert account.balance == Decimal('1500.00')

        tx_in = FinancialTransaction.objects.create(
            salon=salon,
            account=account,
            type=FinancialTransaction.TransactionType.IN,
            category=FinancialTransaction.Category.SERVICE,
            amount=Decimal('180.00'),
            description='Recebimento Corte + Escova',
            employee=employee,
            date_occurred=date(2026, 11, 8),
        )
        tx_out = FinancialTransaction.objects.create(
            salon=salon,
            account=account,
            type=FinancialTransaction.TransactionType.OUT,
            category=FinancialTransaction.Category.COMMISSION,
            amount=Decimal('90.00'),
            description='Comissão Beatriz',
            employee=employee,
            date_occurred=date(2026, 11, 9),
        )

        transactions = list(FinancialTransaction.objects.filter(salon=salon))
        # Ordenação padrão: ['-date_occurred', '-created_at']
        assert transactions[0].id == tx_out.id
        assert transactions[1].id == tx_in.id
        assert account.transactions.count() == 2
