import pytest
from django.conf import settings


def test_django_settings_configured() -> None:
    """Verifica se o pytest-django carregou as configurações do projeto."""
    assert settings.ROOT_URLCONF == 'salao_conecta.urls'
    assert 'business.apps.BusinessConfig' in settings.INSTALLED_APPS


@pytest.mark.parametrize(
    'invalid_color',
    ['233B5C', '#GGGGGG', '{{7*7}}', 'rgb(0,0,0)'],
)
def test_hex_color_validator_rejects_invalid_or_ssti_payloads(invalid_color: str) -> None:
    """Garante que o validador de cores hexadecimais rejeita formatos inválidos e payloads maliciosos."""
    from django.core.exceptions import ValidationError
    from business.models import hex_color_validator

    with pytest.raises(ValidationError):
        hex_color_validator(invalid_color)
