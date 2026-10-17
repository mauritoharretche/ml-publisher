"""Escrituras comerciales User Products, aisladas y deshabilitadas por defecto."""
import hashlib
import json
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction

from drafts.models import PublicationAttempt
from .connections import access_token, active_connection
from .exceptions import MercadoLibreError, MercadoLibreNetworkError
from .image_validation import validate_image_file
from .mercado_libre_client import MercadoLibreHttpClient, MercadoLibreMetadataService
from .preflight import PublicationPreflightService
from .publishing import PublicationBlocked


def draft_fingerprint(draft):
    """Vincula la confirmación al contenido revisado, sin incluir secretos."""
    values = {
        "id": draft.pk, "updated_at": draft.updated_at.isoformat(), "family_name": draft.family_name,
        "domain_id": draft.domain_id, "category_id": draft.category_id, "price": str(draft.price),
        "stock": draft.stock, "listing_type_id": draft.listing_type_id, "attributes": draft.additional_attributes,
        "sale_terms": draft.sale_terms, "details": draft.commercial_details,
        "images": list(draft.images.order_by("position", "id").values_list("id", "size", "position")),
    }
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str).encode()).hexdigest()


class ProductionWriteGuard:
    def verify(self, connection, seller, report):
        if not settings.MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES:
            raise PublicationBlocked("Las escrituras comerciales están deshabilitadas.")
        if not settings.MERCADO_LIBRE_PRODUCTION_SELLER_ID or str(connection.seller_id) != settings.MERCADO_LIBRE_PRODUCTION_SELLER_ID:
            raise PublicationBlocked("La cuenta conectada no coincide con el seller comercial autorizado.")
        if seller.get("site_id") != "MLA":
            raise PublicationBlocked("La API no confirmó el sitio MLA para la cuenta comercial.")
        if "user_product_seller" not in (seller.get("tags") or []):
            raise PublicationBlocked("La API no confirmó el flujo User Products para este seller.")
        if report.flow != "user_products" or report.mode != "commercial" or report.blockers:
            raise PublicationBlocked("El preflight comercial no está completo; no se puede escribir.")


class CommercialPublicationClient:
    """Contrato de escritura separado del flujo de ensayo."""
    def upload_picture(self, image, access_token): raise NotImplementedError
    def create_initial_item(self, payload, access_token): raise NotImplementedError
    def create_selling_condition(self, user_product_id, payload, access_token): raise NotImplementedError


class LiveCommercialPublicationClient(CommercialPublicationClient):
    def __init__(self, http_client=None): self.http = http_client or MercadoLibreHttpClient()

    def upload_picture(self, image, access_token):
        with image.image.open("rb") as file_handle:
            response = self.http.upload_file("/pictures/items/upload", file_handle, access_token=access_token)
        if not response.get("id"):
            raise MercadoLibreNetworkError("Mercado Libre no devolvió un ID de imagen.")
        return response

    def create_initial_item(self, payload, access_token):
        return self.http.post_json("/items", payload, access_token=access_token)

    def create_selling_condition(self, user_product_id, payload, access_token):
        return self.http.post_json(f"/user-products/{user_product_id}/items", payload, access_token=access_token)


class CommercialPublicationService:
    def __init__(self, writer, metadata=None, guard=None, token_getter=access_token, preflight_service=None):
        self.writer = writer
        self.metadata = metadata or MercadoLibreMetadataService()
        self.guard = guard or ProductionWriteGuard()
        self.token_getter = token_getter
        self.preflight_service = preflight_service

    def publish_initial(self, draft, *, confirmation_fingerprint):
        if confirmation_fingerprint != draft_fingerprint(draft):
            raise PublicationBlocked("El borrador cambió después de la confirmación; revisalo y confirmalo nuevamente.")
        connection = active_connection()
        if not connection:
            raise PublicationBlocked("No hay una cuenta conectada.")
        # El flag se comprueba antes de descifrar token o ejecutar HTTP.
        if not settings.MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES:
            raise PublicationBlocked("Las escrituras comerciales están deshabilitadas.")
        token = self.token_getter(connection, allow_refresh=False)
        seller = self.metadata.seller_details(connection.seller_id, token)
        report = self.preflight_service or PublicationPreflightService(self.metadata, token_getter=lambda *_args, **_kwargs: token).run(draft)
        self.guard.verify(connection, seller, report)
        attempt = self._create_intent(draft, connection.seller_id)
        try:
            pictures = self._upload_images(draft, attempt, token)
            payload = self.initial_payload(draft, pictures)
            attempt.payload_snapshot = payload
            attempt.status = PublicationAttempt.Status.CREATING
            attempt.save(update_fields=["payload_snapshot", "status", "updated_at"])
            result = self.writer.create_initial_item(payload, token)
            self._persist_result(attempt, result, stage="initial_item")
            return attempt
        except MercadoLibreNetworkError:
            attempt.status, attempt.safe_error = PublicationAttempt.Status.UNKNOWN, "Resultado comercial desconocido por fallo de red; requiere reconciliación manual o GET verificable."
            attempt.save(update_fields=["status", "safe_error", "updated_at"])
            raise PublicationBlocked(attempt.safe_error)
        except MercadoLibreError as error:
            attempt.status, attempt.safe_error = (PublicationAttempt.Status.PARTIAL if attempt.picture_ids else PublicationAttempt.Status.FAILED), str(error)
            attempt.save(update_fields=["status", "safe_error", "updated_at"])
            raise PublicationBlocked(str(error))

    def add_selling_condition(self, user_product_id, draft, *, confirmation_fingerprint):
        if confirmation_fingerprint != draft_fingerprint(draft):
            raise PublicationBlocked("El borrador cambió después de la confirmación; revisalo y confirmalo nuevamente.")
        connection = active_connection()
        if not connection or not settings.MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES:
            raise PublicationBlocked("Las escrituras comerciales están deshabilitadas.")
        token = self.token_getter(connection, allow_refresh=False)
        seller = self.metadata.seller_details(connection.seller_id, token)
        report = self.preflight_service or PublicationPreflightService(self.metadata, token_getter=lambda *_args, **_kwargs: token).run(draft)
        self.guard.verify(connection, seller, report)
        payload = self.selling_condition_payload(draft)
        if any(key in payload for key in ("title", "attributes", "pictures", "family_name", "domain_id", "available_quantity")):
            raise PublicationBlocked("El payload de condición de venta contiene campos heredados no permitidos.")
        attempt = draft.publication_attempts.filter(user_product_id=user_product_id).order_by("-updated_at").first()
        try:
            result = self.writer.create_selling_condition(user_product_id, payload, token)
        except MercadoLibreNetworkError:
            if attempt:
                attempt.status, attempt.safe_error = PublicationAttempt.Status.UNKNOWN, "Resultado desconocido al asociar condición de venta; no reintentar automáticamente."
                attempt.save(update_fields=["status", "safe_error", "updated_at"])
            raise
        except MercadoLibreError as error:
            if attempt:
                attempt.stage_results = {**attempt.stage_results, "selling_condition_error": str(error)}
                attempt.save(update_fields=["stage_results", "updated_at"])
            raise
        if attempt:
            attempt.stage_results = {**attempt.stage_results, "selling_condition": {"item_id": result.get("id", "")}}
            attempt.save(update_fields=["stage_results", "updated_at"])
        return result

    def _create_intent(self, draft, seller_id):
        with transaction.atomic():
            locked = type(draft).objects.select_for_update().get(pk=draft.pk)
            if locked.publication_attempts.filter(status__in=["intended", "uploading_images", "creating", "partial", "unknown", "published"]).exists():
                raise PublicationBlocked("Ya existe un intento comercial activo o resuelto; no se reintentará automáticamente.")
            try:
                return PublicationAttempt.objects.create(draft=locked, seller_id=seller_id, flow="user_products", listing_type_id=draft.listing_type_id, channels=(draft.commercial_details or {}).get("channels", []), request_key=uuid.uuid4())
            except IntegrityError as error:
                raise PublicationBlocked("Hay otra publicación comercial en curso para este borrador.") from error

    def _upload_images(self, draft, attempt, token):
        attempt.status = PublicationAttempt.Status.UPLOADING_IMAGES
        attempt.save(update_fields=["status", "updated_at"])
        uploaded = []
        for image in draft.images.order_by("position", "id"):
            try:
                with image.image.open("rb") as handle:
                    validate_image_file(handle, declared_content_type=image.content_type, declared_size=image.size)
                result = self.writer.upload_picture(image, token)
                picture_id = result.get("id") if isinstance(result, dict) else result
                if not picture_id:
                    raise PublicationBlocked("Mercado Libre no devolvió un ID para una imagen transferida.")
                uploaded.append({"image_id": image.pk, "picture_id": picture_id, "position": image.position, "status": "uploaded"})
                attempt.picture_ids, attempt.stage_results = uploaded, {"images": uploaded}
                attempt.save(update_fields=["picture_ids", "stage_results", "updated_at"])
            except Exception as error:
                attempt.picture_ids, attempt.stage_results = uploaded, {"images": uploaded}
                attempt.save(update_fields=["picture_ids", "stage_results", "updated_at"])
                if isinstance(error, MercadoLibreError):
                    raise
                raise PublicationBlocked(f"No se pudo validar o transferir la imagen {image.pk}.") from error
        return [entry["picture_id"] for entry in uploaded]

    @staticmethod
    def _attributes(draft):
        return [{"id": key, **({"value_id": value["value_id"]} if value.get("value_id") else {"value_name": value.get("value_name")})} for key, value in draft.additional_attributes.items() if isinstance(value, dict) and (value.get("value_id") or value.get("value_name"))]

    def initial_payload(self, draft, picture_ids):
        payload = {"family_name": draft.family_name, "domain_id": draft.domain_id, "category_id": draft.category_id, "price": float(draft.price), "currency_id": draft.currency, "available_quantity": draft.stock, "buying_mode": "buy_it_now", "listing_type_id": draft.listing_type_id, "attributes": self._attributes(draft), "pictures": [{"id": picture_id} for picture_id in picture_ids]}
        if draft.sale_terms: payload["sale_terms"] = draft.sale_terms
        details = draft.commercial_details or {}
        if details.get("channels"): payload["channels"] = details["channels"]
        if details.get("shipping_mode"): payload["shipping"] = {"mode": details["shipping_mode"]}
        if "title" in payload: raise PublicationBlocked("User Products no admite title en la creación inicial.")
        return payload

    def selling_condition_payload(self, draft):
        payload = {"price": float(draft.price), "currency_id": draft.currency, "category_id": draft.category_id, "buying_mode": "buy_it_now", "listing_type_id": draft.listing_type_id}
        if draft.sale_terms: payload["sale_terms"] = draft.sale_terms
        details = draft.commercial_details or {}
        if details.get("channels"): payload["channels"] = details["channels"]
        if details.get("shipping_mode"): payload["shipping"] = {"mode": details["shipping_mode"]}
        return payload

    def _persist_result(self, attempt, result, *, stage):
        attempt.item_id = result.get("id", "")
        attempt.user_product_id = result.get("user_product_id", "")
        attempt.family_id = str(result.get("family_id", ""))
        attempt.permalink = result.get("permalink", "")
        attempt.stage_results = {**attempt.stage_results, stage: {key: result.get(key) for key in ("id", "user_product_id", "family_id", "permalink")}}
        attempt.status = PublicationAttempt.Status.PUBLISHED if attempt.item_id else PublicationAttempt.Status.PARTIAL
        attempt.save(update_fields=["item_id", "user_product_id", "family_id", "permalink", "stage_results", "status", "updated_at"])

    def reconcile(self, attempt):
        if not attempt.user_product_id:
            raise PublicationBlocked("No hay user_product_id para reconciliar de forma fiable; requiere resolución manual.")
        connection = active_connection()
        if not connection:
            raise PublicationBlocked("No hay una cuenta conectada.")
        token = self.token_getter(connection, allow_refresh=False)
        result = self.metadata.items_for_user_product(connection.seller_id, attempt.user_product_id, token)
        item_ids = result.get("results") or []
        if not item_ids:
            raise PublicationBlocked("La consulta GET no confirmó una condición de venta; no se recuperó el intento.")
        attempt.item_id, attempt.status, attempt.safe_error = str(item_ids[0]), PublicationAttempt.Status.PUBLISHED, ""
        attempt.stage_results = {**attempt.stage_results, "reconciliation": {"item_id": attempt.item_id}}
        attempt.save(update_fields=["item_id", "status", "safe_error", "stage_results", "updated_at"])
        return attempt
