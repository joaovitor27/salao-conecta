import logging
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags

from auth_users.models import User

logger = logging.getLogger(__name__)


def send_notification_email(subject: str, recipients: list, template_name: str, context: dict):
    """
    Renderiza um template HTML com um contexto e envia o e-mail.
    Um fallback para texto puro é gerado automaticamente a partir do HTML.
    """
    if not recipients:
        logger.warning("Tentativa de envio de e-mail sem destinatários.")
        return

    html_message = render_to_string(template_name, context)
    plain_message = strip_tags(html_message)

    email = EmailMultiAlternatives(
        subject=subject,
        body=plain_message,
        from_email=settings.EMAIL_FROM,
        to=recipients
    )
    email.attach_alternative(html_message, "text/html")

    image_path = Path(settings.BASE_DIR) / 'static' / 'img' / 'logo-system.png'

    try:
        with open(image_path, 'rb') as f:
            logo_image = MIMEImage(f.read())
            logo_image.add_header('Content-ID', '<logo-system>')
            email.attach(logo_image)
    except FileNotFoundError:
        logger.error(f"Não foi possível encontrar a imagem do logo em: {image_path}")

    logger.debug(f'Enviando e-mail: "{subject}" para {recipients}')

    email.send(fail_silently=False)


def send_email_reset_password(user: User, reset_link: str):
    """
    Envia um e-mail com o link para redefinição de senha.
    """
    subject = 'Redefinição de senha da sua conta'
    context = {
        'user': user,
        'system_name': settings.SYSTEM_NAME,
        'reset_link': reset_link
    }
    send_notification_email(
        subject=subject,
        recipients=[user.email],
        template_name='emails/password_reset.html',
        context=context
    )
