from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Q


class ProductDraft(models.Model):
    class Condition(models.TextChoices):
        NEW = "new", "Nuevo"
        USED = "used", "Usado"
        NOT_SPECIFIED = "not_specified", "A definir"

    class Status(models.TextChoices):
        DRAFT = "draft", "Borrador"
        PREPARING = "preparing", "En preparación"
        READY = "ready", "Listo para validar"
        VALIDATION_ERROR = "validation_error", "Requiere revisión"
        PUBLISHED = "published", "Publicado"

    title = models.CharField("título", max_length=200)
    price = models.DecimalField("precio", max_digits=12, decimal_places=2, validators=[MinValueValidator(0.01)])
    currency = models.CharField(max_length=3, default="ARS", editable=False)
    description = models.TextField("descripción", blank=True)
    category_id = models.CharField("ID de categoría", max_length=30, blank=True)
    additional_attributes = models.JSONField("atributos adicionales", default=dict, blank=True)
    condition = models.CharField(max_length=20, choices=Condition.choices, default=Condition.NOT_SPECIFIED)
    stock = models.PositiveIntegerField("stock", default=0)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.DRAFT)
    mercado_libre_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        verbose_name = "borrador de producto"
        verbose_name_plural = "borradores de productos"

    def __str__(self):
        return self.title


class ProductImage(models.Model):
    draft = models.ForeignKey(ProductDraft, related_name="images", on_delete=models.CASCADE)
    image = models.ImageField(upload_to="drafts/%Y/%m/%d/")
    position = models.PositiveIntegerField(default=0)
    original_filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=100, blank=True)
    size = models.PositiveIntegerField()
    processing_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["position", "id"]
        constraints = [models.UniqueConstraint(fields=["draft", "position"], name="unique_image_position_per_draft")]

    def __str__(self):
        return self.original_filename


class MercadoLibreConnection(models.Model):
    """Una conexión de seller; los secretos siempre se guardan cifrados."""
    seller_id = models.BigIntegerField(unique=True)
    nickname = models.CharField(max_length=100, blank=True)
    access_token_encrypted = models.TextField()
    refresh_token_encrypted = models.TextField()
    token_expires_at = models.DateTimeField()
    scopes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "conexión de Mercado Libre"
        verbose_name_plural = "conexiones de Mercado Libre"

    def __str__(self):
        return f"Seller {self.seller_id}"


class PublicationAttempt(models.Model):
    class Status(models.TextChoices):
        INTENDED = "intended", "Intención registrada"
        UPLOADING_IMAGES = "uploading_images", "Subiendo imágenes"
        CREATING = "creating", "Creando publicación"
        PARTIAL = "partial", "Resultado parcial"
        UNKNOWN = "unknown", "Resultado desconocido"
        FAILED = "failed", "Fallida"
        PUBLISHED = "published", "Publicada"

    draft = models.ForeignKey(ProductDraft, related_name="publication_attempts", on_delete=models.PROTECT)
    seller_id = models.BigIntegerField()
    flow = models.CharField(max_length=30)
    listing_type_id = models.CharField(max_length=60)
    channels = models.JSONField(default=list)
    status = models.CharField(max_length=30, choices=Status.choices, default=Status.INTENDED)
    request_key = models.UUIDField(unique=True)
    payload_snapshot = models.JSONField(default=dict)
    picture_ids = models.JSONField(default=list)
    item_id = models.CharField(max_length=80, blank=True)
    user_product_id = models.CharField(max_length=80, blank=True)
    permalink = models.URLField(blank=True)
    safe_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["draft"], condition=Q(status__in=["intended", "uploading_images", "creating", "partial", "unknown", "published"]), name="one_active_publication_attempt")]
