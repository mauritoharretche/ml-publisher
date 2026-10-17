"""Validaciones locales para imágenes antes de cualquier transferencia externa."""
from django.conf import settings
from django.core.exceptions import ValidationError
from PIL import Image


ALLOWED_IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png"}
MERCADO_LIBRE_MAX_IMAGE_SIZE = 10 * 1024 * 1024
MERCADO_LIBRE_MIN_RESOLUTION = 500
MERCADO_LIBRE_MAX_RESOLUTION = 1920


def validate_image_file(upload, *, declared_content_type="", declared_size=None):
    """Comprueba contenido y dimensiones; no convierte ni transmite archivos."""
    size = declared_size if declared_size is not None else upload.size
    maximum = min(settings.MAX_UPLOAD_SIZE, MERCADO_LIBRE_MAX_IMAGE_SIZE)
    if size > maximum:
        raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: supera el máximo local de {maximum // (1024 * 1024)} MB.")
    if declared_content_type and declared_content_type not in set(ALLOWED_IMAGE_FORMATS.values()):
        if declared_content_type == "image/webp":
            raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: WebP no se admite; usá JPG/JPEG o PNG.")
        raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: formato no permitido.")
    try:
        image = Image.open(upload)
        image.verify()
        upload.seek(0)
        image = Image.open(upload)
        actual_format = image.format
        width, height = image.size
    except Exception as error:
        raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: archivo de imagen inválido.") from error
    finally:
        try:
            upload.seek(0)
        except Exception:
            pass
    if actual_format not in ALLOWED_IMAGE_FORMATS:
        raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: Mercado Libre admite únicamente JPG/JPEG o PNG.")
    if width < MERCADO_LIBRE_MIN_RESOLUTION or height < MERCADO_LIBRE_MIN_RESOLUTION:
        raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: la resolución mínima es {MERCADO_LIBRE_MIN_RESOLUTION} × {MERCADO_LIBRE_MIN_RESOLUTION}px.")
    if width > MERCADO_LIBRE_MAX_RESOLUTION or height > MERCADO_LIBRE_MAX_RESOLUTION:
        raise ValidationError(f"{getattr(upload, 'name', 'Imagen')}: la resolución máxima admitida es {MERCADO_LIBRE_MAX_RESOLUTION} × {MERCADO_LIBRE_MAX_RESOLUTION}px.")
    return {"format": actual_format, "content_type": ALLOWED_IMAGE_FORMATS[actual_format], "width": width, "height": height}
