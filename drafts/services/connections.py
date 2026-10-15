from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from drafts.models import MercadoLibreConnection
from .credentials import decrypt, encrypt
from .exceptions import MercadoLibreAuthorizationError, MercadoLibreTokenRefreshRequired
from .mercado_libre_client import MercadoLibreOAuthService


def save_tokens(token_data, profile):
    defaults = {"nickname": profile.get("nickname", ""), "access_token_encrypted": encrypt(token_data.access_token), "refresh_token_encrypted": encrypt(token_data.refresh_token), "token_expires_at": token_data.expires_at, "scopes": token_data.scope}
    return MercadoLibreConnection.objects.update_or_create(seller_id=token_data.seller_id, defaults=defaults)[0]


def active_connection():
    return MercadoLibreConnection.objects.order_by("-updated_at").first()


def access_token(connection=None, oauth_service=None, *, allow_refresh=True):
    connection = connection or active_connection()
    if not connection:
        raise MercadoLibreAuthorizationError("Conectá una cuenta de Mercado Libre para consultar metadatos.")
    if connection.token_expires_at > timezone.now() + timedelta(minutes=5):
        return decrypt(connection.access_token_encrypted)
    if not allow_refresh:
        raise MercadoLibreTokenRefreshRequired("El token requiere renovación. El diagnóstico de sólo lectura no la ejecuta; reconectá la cuenta para renovarlo.")
    oauth_service = oauth_service or MercadoLibreOAuthService()
    with transaction.atomic():
        connection = MercadoLibreConnection.objects.select_for_update().get(pk=connection.pk)
        if connection.token_expires_at > timezone.now() + timedelta(minutes=5):
            return decrypt(connection.access_token_encrypted)
        token_data = oauth_service.refresh(decrypt(connection.refresh_token_encrypted))
        if token_data.seller_id != connection.seller_id:
            raise MercadoLibreAuthorizationError("La renovación no corresponde a la cuenta conectada.")
        connection.access_token_encrypted = encrypt(token_data.access_token)
        connection.refresh_token_encrypted = encrypt(token_data.refresh_token)
        connection.token_expires_at = token_data.expires_at
        connection.scopes = token_data.scope
        connection.save(update_fields=["access_token_encrypted", "refresh_token_encrypted", "token_expires_at", "scopes", "updated_at"])
        return token_data.access_token
