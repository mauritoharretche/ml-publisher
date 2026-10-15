from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from drafts.models import ProductDraft
from .connections import access_token, active_connection
from .exceptions import MercadoLibreError
from .mercado_libre_client import MercadoLibreMetadataService


@dataclass
class PreflightReport:
    flow: str = "undetermined"
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    category: dict[str, Any] = field(default_factory=dict)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    payload_preview: dict[str, Any] = field(default_factory=dict)


def _attribute_value(attributes, attribute_id):
    value = attributes.get(attribute_id)
    if isinstance(value, dict):
        return value.get("value_id") or value.get("value_name")
    return value


class PublicationPreflightService:
    """Prepara un informe local; no tiene métodos HTTP de escritura."""
    def __init__(self, metadata_service=None, token_getter=access_token):
        self.metadata = metadata_service or MercadoLibreMetadataService()
        self.token_getter = token_getter

    def run(self, draft: ProductDraft) -> PreflightReport:
        report = PreflightReport()
        connection = active_connection()
        if not connection:
            report.blockers.append("No hay una cuenta de Mercado Libre conectada.")
            return report
        try:
            token = self.token_getter(connection, allow_refresh=False)
            seller = self.metadata.seller_details(connection.seller_id, token)
        except MercadoLibreError as error:
            report.blockers.append(str(error))
            return report

        tags = seller.get("tags") or []
        site_id = seller.get("site_id")
        if site_id != "MLA":
            report.blockers.append("La cuenta conectada no confirmó el sitio MLA.")
        if "user_product_seller" in tags:
            report.flow = "user_products"
            report.blockers.append("El ensayo User Products está bloqueado: Mercado Libre genera el título y no es posible garantizar el prefijo obligatorio de usuarios test.")
            report.pending.append("Antes de habilitarlo se debe confirmar un contrato oficial para título de prueba generado, domain_id, ITEM_CONDITION, atributos PARENT/CHILD PK y stock del User Product.")
        else:
            report.flow = "undetermined"
            report.pending.append("No se detectó user_product_seller. Esto no confirma que el flujo clásico esté habilitado; verificá permisos y activaciones con Mercado Libre.")

        self._check_draft_basics(draft, report)
        if not draft.category_id:
            report.blockers.append("Falta seleccionar una categoría.")
            return report
        try:
            definitions = self.metadata.category_attributes(draft.category_id, token)
            details = self.metadata.category_details(draft.category_id, token)
        except MercadoLibreError as error:
            report.blockers.append(str(error))
            return report
        report.category = {"id": draft.category_id, "name": details.get("name"), "settings": details.get("settings", {})}
        self._check_attributes(draft, definitions, report)
        self._check_images(draft, details, report)
        report.payload_preview = self._payload_preview(draft, report.flow)
        return report

    def _check_draft_basics(self, draft, report):
        if not draft.title.strip():
            report.blockers.append("Falta el título.")
        if not draft.description.strip():
            report.warnings.append("No hay descripción. Su recurso y obligatoriedad dependen del flujo aplicable.")
        if Decimal(str(draft.price)) <= 0:
            report.blockers.append("El precio debe ser mayor que cero.")
        if draft.currency != "ARS":
            report.blockers.append("La moneda del borrador debe ser ARS para este alcance.")
        if draft.condition == ProductDraft.Condition.NOT_SPECIFIED:
            report.blockers.append("Falta definir la condición.")
        if draft.stock <= 0:
            report.warnings.append("El stock es cero. La gestión de stock puede depender de depósitos o del flujo aplicable.")

    def _check_attributes(self, draft, definitions, report):
        for definition in definitions:
            attribute_id = definition.get("id")
            if not attribute_id:
                continue
            tags = definition.get("tags") or {}
            required = tags.get("required") or (tags.get("new_required") and draft.condition == ProductDraft.Condition.NEW)
            conditional = tags.get("conditional_required")
            value = _attribute_value(draft.additional_attributes, attribute_id)
            allowed = definition.get("values") or []
            entry = {"id": attribute_id, "name": definition.get("name", attribute_id), "required": bool(required), "conditional": bool(conditional), "value": value, "allowed_values": [{"id": item.get("id"), "name": item.get("name")} for item in allowed]}
            report.attributes.append(entry)
            if required and not value:
                report.blockers.append(f"Falta el atributo obligatorio: {entry['name']}.")
            if value and allowed and str(value) not in {str(item.get("id")) for item in allowed} | {str(item.get("name")) for item in allowed}:
                report.warnings.append(f"{entry['name']} no coincide con los valores permitidos actualmente consultados.")
            if conditional:
                report.pending.append(f"{entry['name']} es condicional. No se evaluó porque requiere contexto de publicación y esta fase no realiza POST.")

    def _check_images(self, draft, details, report):
        count = draft.images.count()
        settings = details.get("settings") or {}
        maximum = settings.get("max_pictures_per_item")
        if not count:
            report.warnings.append("No hay imágenes. La obligatoriedad depende del tipo de publicación y la categoría.")
        if maximum and count > maximum:
            report.blockers.append(f"Hay {count} imágenes y la categoría informa un máximo de {maximum}.")
        if count:
            report.pending.append("Las imágenes siguen locales; no fueron transferidas al servicio de imágenes de Mercado Libre.")

    def _payload_preview(self, draft, flow):
        attributes = [{"id": key, **value} if isinstance(value, dict) else {"id": key, "value_name": value} for key, value in draft.additional_attributes.items()]
        common = {"category_id": draft.category_id, "attributes": attributes, "condition": draft.condition, "price": str(draft.price), "currency_id": draft.currency, "stock": draft.stock, "local_image_count": draft.images.count()}
        if flow == "user_products":
            return {"model": "user_products", "candidate_product_data": common, "not_sent": ["El título, descripción, canales y tipo de publicación requieren confirmación en el flujo aplicable."]}
        return {"model": "undetermined", "candidate_item_data": {**common, "title": draft.title}, "not_sent": ["No se determinó un flujo de escritura; no es un payload listo para enviar."]}


class IntegrationDiagnosticService:
    def __init__(self, metadata_service=None, token_getter=access_token):
        self.metadata = metadata_service or MercadoLibreMetadataService()
        self.token_getter = token_getter

    def run(self, *, title="", category_id=""):
        checks = []
        connection = active_connection()
        if not connection:
            return [{"name": "Conexión OAuth", "ok": False, "detail": "No hay cuenta conectada."}]
        checks.append({"name": "Conexión OAuth", "ok": True, "detail": f"Seller almacenado: {connection.seller_id}."})
        try:
            token = self.token_getter(connection, allow_refresh=False)
            seller = self.metadata.seller_details(connection.seller_id, token)
            tags = seller.get("tags") or []
            site_id = seller.get("site_id")
            checks.append({"name": "Perfil del vendedor", "ok": True, "detail": f"Seller {seller.get('id', connection.seller_id)}; sitio {site_id or 'no informado'}."})
            checks.append({"name": "Sitio MLA", "ok": site_id == "MLA", "detail": "MLA confirmado." if site_id == "MLA" else "La API no confirmó MLA."})
            checks.append({"name": "Flujo", "ok": True, "detail": "Tag user_product_seller detectado." if "user_product_seller" in tags else "Tag no detectado; el flujo de publicación queda por confirmar."})
            if title:
                suggestions = self.metadata.predict_categories(title, token)
                checks.append({"name": "Predictor de categorías", "ok": True, "detail": f"{len(suggestions)} sugerencia(s) recibida(s)."})
                category_id = category_id or (suggestions[0].get("category_id") if suggestions else "")
            if category_id:
                attributes = self.metadata.category_attributes(category_id, token)
                checks.append({"name": "Atributos de categoría", "ok": True, "detail": f"{len(attributes)} atributo(s) consultado(s) para {category_id}."})
        except MercadoLibreError as error:
            checks.append({"name": "Consulta de Mercado Libre", "ok": False, "detail": str(error)})
        return checks
