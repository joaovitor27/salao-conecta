from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('business', '0007_alter_customer_cpf'),
    ]

    operations = [
        migrations.AddField(
            model_name='salon',
            name='require_professional_choice',
            field=models.BooleanField(
                default=False,
                help_text=(
                    "Quando ativo, a cliente precisa escolher o profissional de cada serviço no agendamento online. "
                    "Quando inativo, ela pode marcar 'Indiferente' e o salão define o profissional depois."
                ),
                verbose_name='Escolha de profissional obrigatória',
            ),
        ),
    ]
