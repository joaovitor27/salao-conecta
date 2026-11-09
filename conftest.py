"""Configurações globais de fixtures do pytest para o Salão Conecta."""
import pytest
from rest_framework.test import APIClient


@pytest.fixture
def api_client() -> APIClient:
    """Retorna uma instância de APIClient do Django REST Framework."""
    return APIClient()
