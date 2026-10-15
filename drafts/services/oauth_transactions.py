import hashlib
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from drafts.models import MercadoLibreOAuthTransaction
from .credentials import decrypt, encrypt
from .exceptions import MercadoLibreAuthorizationError


OAUTH_TRANSACTION_TTL = timedelta(minutes=10)


def _state_hash(state):
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def create_transaction(state, code_verifier):
    return MercadoLibreOAuthTransaction.objects.create(
        state_hash=_state_hash(state),
        code_verifier_encrypted=encrypt(code_verifier),
        expires_at=timezone.now() + OAUTH_TRANSACTION_TTL,
    )


def consume_transaction(state):
    """Consume un state una sola vez, incluso si dos callbacks llegan a la vez."""
    if not state:
        raise MercadoLibreAuthorizationError("La respuesta de autorización no es válida o expiró.")
    with transaction.atomic():
        oauth_transaction = MercadoLibreOAuthTransaction.objects.select_for_update().filter(state_hash=_state_hash(state)).first()
        if not oauth_transaction or oauth_transaction.consumed_at or oauth_transaction.expires_at <= timezone.now():
            raise MercadoLibreAuthorizationError("La respuesta de autorización no es válida o expiró.")
        oauth_transaction.consumed_at = timezone.now()
        oauth_transaction.save(update_fields=["consumed_at"])
        return decrypt(oauth_transaction.code_verifier_encrypted)
