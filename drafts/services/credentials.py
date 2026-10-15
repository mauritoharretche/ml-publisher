from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from .exceptions import MercadoLibreConfigurationError


def _fernet():
    key = settings.MERCADO_LIBRE_ENCRYPTION_KEY
    if not key:
        raise MercadoLibreConfigurationError("Falta configurar la clave de cifrado de Mercado Libre.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as error:
        raise MercadoLibreConfigurationError("La clave de cifrado de Mercado Libre no es válida.") from error


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as error:
        raise MercadoLibreConfigurationError("No se pudieron leer las credenciales almacenadas.") from error
