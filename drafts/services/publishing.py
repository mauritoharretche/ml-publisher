import uuid
from dataclasses import dataclass
from django.conf import settings
from django.db import IntegrityError, transaction
from drafts.models import PublicationAttempt
from .connections import access_token, active_connection
from .exceptions import MercadoLibreAuthorizationError, MercadoLibreError, MercadoLibreNetworkError
from .image_validation import validate_image_file
from .mercado_libre_client import MercadoLibreHttpClient, MercadoLibreMetadataService
from .preflight import PublicationPreflightService

TEST_TITLE_PREFIX = "Item de Prueba - Por favor, NO OFERTAR"


class PublicationBlocked(MercadoLibreError):
    pass


class TestWriteGuard:
    def verify(self, connection, seller, flow):
        if not settings.MERCADO_LIBRE_ENABLE_TEST_WRITES:
            raise PublicationBlocked("Las escrituras de prueba están deshabilitadas.")
        if not settings.MERCADO_LIBRE_TEST_SELLER_ID or str(connection.seller_id) != settings.MERCADO_LIBRE_TEST_SELLER_ID:
            raise PublicationBlocked("La cuenta conectada no coincide con el seller de prueba configurado.")
        if seller.get("site_id") != "MLA" or "test_user" not in (seller.get("tags") or []):
            raise PublicationBlocked("La cuenta no fue confirmada como usuario de test MLA.")
        if flow not in ("user_products", "legacy"):
            raise PublicationBlocked("El flujo de publicación no está determinado.")


class MercadoLibrePublicationClient:
    """Contrato de escritura: sólo PublicationService lo invoca después del guard."""
    def upload_picture(self, image, access_token): raise NotImplementedError
    def create_item(self, payload, access_token): raise NotImplementedError


class LiveMercadoLibrePublicationClient(MercadoLibrePublicationClient):
    def __init__(self, http_client=None):
        self.http = http_client or MercadoLibreHttpClient()

    def upload_picture(self, image, access_token):
        with image.image.open("rb") as file_handle:
            response = self.http.upload_file("/pictures/items/upload", file_handle, access_token=access_token)
        picture_id = response.get("id")
        if not picture_id:
            raise MercadoLibreNetworkError("Mercado Libre no devolvió un identificador de imagen.")
        return picture_id

    def create_item(self, payload, access_token):
        return self.http.post_json("/items", payload, access_token=access_token)


@dataclass
class PublicationOptions:
    listing_type_id: str
    channels: list[str]


class PublicationService:
    def __init__(self, writer, metadata=None, guard=None, token_getter=access_token):
        self.writer = writer
        self.metadata = metadata or MercadoLibreMetadataService()
        self.guard = guard or TestWriteGuard()
        self.token_getter = token_getter

    def publish(self, draft, options, *, confirmed=False):
        if not confirmed:
            raise PublicationBlocked("Debés confirmar explícitamente la publicación de prueba.")
        if not draft.title.startswith(TEST_TITLE_PREFIX):
            raise PublicationBlocked(f'El título debe comenzar con "{TEST_TITLE_PREFIX}".')
        if options.listing_type_id != "free":
            raise PublicationBlocked("El único tipo permitido para este ensayo es free.")
        if not options.listing_type_id or not options.channels:
            raise PublicationBlocked("Falta tipo de publicación o canales.")
        connection = active_connection()
        if not connection:
            raise PublicationBlocked("No hay una cuenta conectada.")
        token = self.token_getter(connection)
        seller = self.metadata.seller_details(connection.seller_id, token)
        flow = "user_products" if "user_product_seller" in (seller.get("tags") or []) else "undetermined"
        self.guard.verify(connection, seller, flow)
        if flow == "user_products":
            raise PublicationBlocked("User Products queda bloqueado para usuarios test: Mercado Libre genera el título y esta aplicación no puede garantizar el prefijo obligatorio de prueba. Falta un contrato oficial de título de test compatible.")
        available = self.metadata.available_listing_types(connection.seller_id, draft.category_id, token).get("available", [])
        if "free" not in {entry.get("id") for entry in available}:
            raise PublicationBlocked("El tipo free no está disponible para este seller y categoría; no se seleccionará un tipo de pago.")
        report = PublicationPreflightService(self.metadata, token_getter=lambda *_args, **_kwargs: token).run(draft)
        if report.blockers:
            raise PublicationBlocked("Preflight bloqueó la publicación: " + " ".join(report.blockers))
        if not draft.images.exists():
            raise PublicationBlocked("La publicación de prueba requiere al menos una imagen local.")
        attempt = self._create_intent(draft, connection.seller_id, flow, options)
        try:
            attempt.status = PublicationAttempt.Status.UPLOADING_IMAGES
            attempt.save(update_fields=["status", "updated_at"])
            self._validate_images(draft)
            uploads = [{"image_id": image.pk, "picture_id": self.writer.upload_picture(image, token)} for image in draft.images.all()]
            picture_ids = [entry["picture_id"] for entry in uploads]
            attempt.picture_ids = uploads
            attempt.status = PublicationAttempt.Status.CREATING
            attempt.save(update_fields=["picture_ids", "status", "updated_at"])
            payload = self._payload(draft, flow, options, picture_ids)
            attempt.payload_snapshot = payload
            attempt.save(update_fields=["payload_snapshot", "updated_at"])
            result = self.writer.create_item(payload, token)
            attempt.item_id = result.get("id", "")
            attempt.user_product_id = result.get("user_product_id", "")
            attempt.permalink = result.get("permalink", "")
            attempt.status = PublicationAttempt.Status.PUBLISHED if attempt.item_id else PublicationAttempt.Status.PARTIAL
            attempt.save(update_fields=["item_id", "user_product_id", "permalink", "status", "updated_at"])
            return attempt
        except MercadoLibreNetworkError:
            attempt.status = PublicationAttempt.Status.UNKNOWN
            attempt.safe_error = "Resultado desconocido por fallo de red. Verificá el estado antes de reintentar."
            attempt.save(update_fields=["status", "safe_error", "updated_at"])
            raise PublicationBlocked(attempt.safe_error)
        except MercadoLibreError as error:
            attempt.status = PublicationAttempt.Status.PARTIAL if attempt.picture_ids else PublicationAttempt.Status.FAILED
            attempt.safe_error = str(error)
            attempt.save(update_fields=["status", "safe_error", "updated_at"])
            raise PublicationBlocked(str(error))

    def _create_intent(self, draft, seller_id, flow, options):
        with transaction.atomic():
            draft = type(draft).objects.select_for_update().get(pk=draft.pk)
            active = draft.publication_attempts.filter(status__in=["intended", "uploading_images", "creating", "partial", "unknown", "published"])
            if active.exists():
                raise PublicationBlocked("Ya existe una intención o resultado de publicación para este borrador. Verificá su estado antes de reintentar.")
            try:
                return PublicationAttempt.objects.create(draft=draft, seller_id=seller_id, flow=flow, listing_type_id=options.listing_type_id, channels=options.channels, request_key=uuid.uuid4())
            except IntegrityError as error:
                raise PublicationBlocked("Hay otra operación de publicación en curso para este borrador.") from error

    def _payload(self, draft, flow, options, picture_ids):
        attributes = [{"id": key, "value_id": value.get("value_id"), "value_name": value.get("value_name")} for key, value in draft.additional_attributes.items() if isinstance(value, dict)]
        common = {"category_id": draft.category_id, "price": float(draft.price), "currency_id": draft.currency, "buying_mode": "buy_it_now", "listing_type_id": options.listing_type_id, "channels": options.channels, "pictures": [{"id": image_id} for image_id in picture_ids]}
        if flow == "user_products":
            raise PublicationBlocked("No hay payload User Products habilitado para este ensayo de test.")
        return {**common, "title": draft.title, "condition": draft.condition, "available_quantity": draft.stock, "attributes": attributes}

    def _validate_images(self, draft):
        for image in draft.images.all():
            try:
                with image.image.open("rb") as file_handle:
                    validate_image_file(file_handle, declared_content_type=image.content_type, declared_size=image.size)
            except Exception as error:
                raise PublicationBlocked(f"{image.original_filename}: {error}") from error
