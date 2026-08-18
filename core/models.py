from django.db import models

# Create your models here.
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
        db_table = "countries"


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
        db_table = "states"


class City(TimeStampedModel):
    """Model to represent a city."""
    name = models.CharField(max_length=100, verbose_name="Nome da Cidade")
    state = models.ForeignKey(State, on_delete=models.CASCADE, related_name='cities', verbose_name="Estado")

    class Meta:
        unique_together = ('name', 'state')
        verbose_name = "Cidade"
        verbose_name_plural = "Cidades"
        db_table = "cities"

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
        db_table = "addresses"
