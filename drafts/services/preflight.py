"""Informes de preparación. Esta capa sólo usa los métodos GET de metadatos."""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError

from drafts.models import ProductDraft
from .connections import access_token, active_connection
from .exceptions import MercadoLibreError
from .image_validation import validate_image_file
from .mercado_libre_client import MercadoLibreMetadataService


@dataclass
class PreflightReport:
    flow: str = "undetermined"
    mode: str = "undetermined"
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    category: dict[str, Any] = field(default_factory=dict)
    attributes: list[dict[str, Any]] = field(default_factory=list)
    family_attributes: list[dict[str, Any]] = field(default_factory=list)
    product_attributes: list[dict[str, Any]] = field(default_factory=list)
    selling_condition: dict[str, Any] = field(default_factory=dict)
    image_details: list[dict[str, Any]] = field(default_factory=list)
    payload_preview: dict[str, Any] = field(default_factory=dict)
    operations: list[str] = field(default_factory=list)


def _attribute_value(attributes, attribute_id):
    value = attributes.get(attribute_id)
    if isinstance(value, dict):
        return value.get("value_id") or value.get("value_name")
    return value


def _attribute_payload(attribute_id, value):
    if isinstance(value, dict):
        result = {"id": attribute_id}
        if value.get("value_id"):
            result["value_id"] = value["value_id"]
        if value.get("value_name"):
            result["value_name"] = value["value_name"]
        return result
    return {"id": attribute_id, "value_name": value}


def _has_tag(tags, name):
    """La API usa mapas en atributos y listas en technical_specs/input."""
    return bool(tags.get(name)) if isinstance(tags, dict) else name in tags if isinstance(tags, list) else False


def _technical_attributes(payload):
    """Extrae definiciones sin asumir la forma interna de groups/components."""
    found, seen = [], set()

    def walk(value):
        if isinstance(value, dict):
            attribute_id, name = value.get("id"), value.get("name")
            if attribute_id and name and "tags" in value:
                key = (str(attribute_id), str(name))
                if key not in seen:
                    seen.add(key)
                    found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(payload)
    return found


class PublicationPreflightService:
    """Preflight comercial para User Products, sin llamar a código de escritura."""

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
        report.verified.append(f"Seller consultado: {seller.get('id', connection.seller_id)}.")
        if site_id == "MLA":
            report.verified.append("Sitio MLA confirmado por Mercado Libre.")
        else:
            report.blockers.append("La cuenta conectada no confirmó el sitio MLA.")
        if "user_product_seller" not in tags:
            report.payload_preview = {"model": "undetermined", "not_sent": ["No se determinó un contrato de escritura."]}
            report.blockers.append("El flujo de publicación no está determinado: la API no confirmó user_product_seller.")
            report.pending.append("El tag no confirma un flujo clásico: se debe confirmar el flujo habilitado para este seller.")
            self._check_common(draft, report)
            return report

        report.flow = "user_products"
        report.payload_preview = {"model": "user_products"}
        report.mode = "test" if "test_user" in tags else "commercial"
        report.verified.append("Tag user_product_seller confirmado.")
        if report.mode == "test":
            report.pending.append("Es un seller de test: la confirmación y el prefijo de prueba siguen siendo exigidos exclusivamente por el publicador de ensayos.")
        else:
            report.verified.append("El perfil no tiene tag test_user; este informe es comercial y no aplica el prefijo de ensayo.")
        self._check_common(draft, report)
        self._check_user_product_metadata(draft, token, report)
        self._technical_operations(report)
        return report

    def _check_common(self, draft, report):
        if len(draft.title.strip()) < 3:
            report.blockers.append("Falta un título de referencia válido para categorizar el producto.")
        if Decimal(str(draft.price)) <= 0:
            report.blockers.append("El precio debe ser mayor que cero.")
        if draft.currency != "ARS":
            report.blockers.append("La moneda del borrador debe ser ARS para MLA.")
        if draft.condition == ProductDraft.Condition.NOT_SPECIFIED:
            report.blockers.append("Falta definir la condición de referencia.")
        if draft.stock <= 0:
            report.warnings.append("El stock es cero. La asignación final de stock depende del contrato User Products aplicable.")
        if not draft.description.strip():
            report.warnings.append("No hay descripción; su entidad y obligatoriedad deben confirmarse para el producto y categoría.")
        if not draft.edition_and_authenticity_verified:
            report.blockers.append("Falta la confirmación manual de que se verificaron edición, sello y autenticidad según la información física disponible.")
        if not draft.commercialization_authorization_confirmed:
            report.blockers.append("Falta confirmar que se revisó la autorización para comercializar el producto.")

    def _check_user_product_metadata(self, draft, token, report):
        if not draft.family_name.strip():
            report.blockers.append("Falta el nombre de familia (family_name).")
        if not draft.category_id:
            report.blockers.append("Falta seleccionar una categoría MLA.")
            return
        if not draft.domain_id:
            report.blockers.append("Falta el dominio (domain_id) devuelto por el predictor.")

        suggestions = self._predict(draft, token, report)
        self._verify_domain(draft, suggestions, report)
        try:
            definitions = self.metadata.category_attributes(draft.category_id, token)
            details = self.metadata.category_details(draft.category_id, token)
        except MercadoLibreError as error:
            report.blockers.append(str(error))
            return
        if not isinstance(definitions, list) or not isinstance(details, dict):
            report.blockers.append("Mercado Libre devolvió metadatos incompletos para la categoría.")
            return
        try:
            listing_response = self.metadata.available_listing_types(active_connection().seller_id, draft.category_id, token)
        except MercadoLibreError as error:
            listing_response = {}
            report.blockers.append(str(error))
        report.category = {"id": draft.category_id, "name": details.get("name"), "settings": details.get("settings", {})}
        report.verified.append(f"Categoría {draft.category_id} y atributos consultados en Mercado Libre.")
        self._check_attributes(draft, definitions, report)
        self._check_technical_specs(draft, token, report)
        self._check_available_sale_terms(draft, token, report)
        self._check_listing_type(draft, listing_response if isinstance(listing_response, dict) else {}, report)
        self._check_costs_and_shipping(draft, token, listing_response if isinstance(listing_response, dict) else {}, report)
        self._check_sale_terms(draft, report)
        self._check_images(draft, details, report)
        self._build_preview(draft, report)

    def _predict(self, draft, token, report):
        if len(draft.title.strip()) < 3:
            return []
        try:
            suggestions = self.metadata.predict_categories(draft.title.strip(), token)
        except MercadoLibreError as error:
            report.warnings.append(f"No se pudo confirmar categoría y dominio con el predictor: {error}")
            return []
        if not isinstance(suggestions, list):
            report.warnings.append("El predictor devolvió datos incompletos; no se confirmó el dominio.")
            return []
        report.verified.append(f"Predictor consultado: {len(suggestions)} sugerencia(s) recibida(s).")
        return suggestions

    def _verify_domain(self, draft, suggestions, report):
        matches = [item for item in suggestions if item.get("category_id") == draft.category_id]
        if not matches:
            report.pending.append("La categoría elegida no apareció entre las sugerencias actuales; confirmá manualmente categoría y dominio antes de publicar.")
            return
        actual_domain = matches[0].get("domain_id")
        if not actual_domain:
            report.pending.append("El predictor no informó domain_id para la categoría elegida.")
        elif not draft.domain_id:
            report.blockers.append("Falta guardar el domain_id devuelto por el predictor.")
        elif draft.domain_id != actual_domain:
            report.blockers.append("El domain_id guardado no coincide con el devuelto actualmente por el predictor.")
        else:
            report.verified.append(f"Domain {draft.domain_id} confirmado por el predictor.")

    def _check_attributes(self, draft, definitions, report):
        for definition in definitions:
            attribute_id = definition.get("id")
            if not attribute_id:
                continue
            tags = definition.get("tags") or {}
            hierarchy = definition.get("hierarchy", "")
            required = bool(_has_tag(tags, "required") or (_has_tag(tags, "new_required") and draft.condition == ProductDraft.Condition.NEW) or attribute_id == "ITEM_CONDITION")
            conditional = _has_tag(tags, "conditional_required")
            value = _attribute_value(draft.additional_attributes, attribute_id)
            allowed = definition.get("values") or []
            entry = {
                "id": attribute_id,
                "name": definition.get("name", attribute_id),
                "hierarchy": hierarchy,
                "required": required,
                "conditional": conditional,
                "value": value,
                "allowed_values": [{"id": item.get("id"), "name": item.get("name")} for item in allowed],
            }
            report.attributes.append(entry)
            is_family = hierarchy == "PARENT_PK" or attribute_id == "ITEM_CONDITION"
            (report.family_attributes if is_family else report.product_attributes).append(entry)
            if required and not value and not (hierarchy == "CHILD_PK" and not draft.additional_attributes.get("VARIATIONS")):
                report.blockers.append(f"Falta el atributo obligatorio: {entry['name']}.")
            if hierarchy == "CHILD_PK" and not value:
                report.pending.append(f"{entry['name']} es CHILD_PK; sólo completalo si el producto requiere esa variante.")
            if value and allowed and str(value) not in {str(item.get("id")) for item in allowed} | {str(item.get("name")) for item in allowed}:
                report.blockers.append(f"{entry['name']} no coincide con los valores permitidos consultados actualmente.")
            if conditional:
                report.pending.append(f"{entry['name']} es condicional; su aplicación depende de contexto no inferible en este preflight.")
        if not any(entry["id"] == "ITEM_CONDITION" for entry in report.attributes):
            report.pending.append("La categoría no devolvió ITEM_CONDITION; confirmá el contrato y valores admitidos antes de publicar.")

    def _check_technical_specs(self, draft, token, report):
        try:
            payload = self.metadata.technical_specs_input(draft.category_id, token)
        except MercadoLibreError as error:
            report.pending.append(f"No se pudieron consultar especificaciones técnicas: {error}")
            return
        if not isinstance(payload, dict):
            report.pending.append("Mercado Libre devolvió especificaciones técnicas incompletas.")
            return
        missing = []
        for definition in _technical_attributes(payload):
            if _has_tag(definition.get("tags"), "required") and not _attribute_value(draft.additional_attributes, definition.get("id")):
                missing.append(definition.get("name", definition["id"]))
        if missing:
            report.pending.append("Especificaciones técnicas requeridas para completar la ficha: " + ", ".join(missing) + ".")
        report.verified.append("Especificaciones técnicas de categoría consultadas.")

    def _check_available_sale_terms(self, draft, token, report):
        try:
            definitions = self.metadata.category_sale_terms(draft.category_id, token)
        except MercadoLibreError as error:
            report.pending.append(f"No se pudieron consultar términos de venta: {error}")
            return
        if not isinstance(definitions, list):
            report.pending.append("Mercado Libre devolvió términos de venta incompletos.")
            return
        report.selling_condition["available_sale_terms"] = [
            {"id": item.get("id"), "name": item.get("name"), "values": item.get("values") or []}
            for item in definitions if item.get("id")
        ]
        report.verified.append(f"Términos de venta consultados: {len(definitions)} opción(es).")

    def _check_listing_type(self, draft, response, report):
        available = response.get("available") or []
        options = [item for item in available if item.get("id")]
        report.selling_condition["available_listing_types"] = [{"id": item.get("id"), "name": item.get("name"), "remaining_listings": item.get("remaining_listings")} for item in options]
        if options:
            report.verified.append(f"Tipos de publicación consultados: {len(options)} opción(es) disponible(s).")
        else:
            report.blockers.append("Mercado Libre no informó tipos de publicación disponibles para este seller y categoría.")
        available_ids = {item.get("id") for item in options}
        if not draft.listing_type_id:
            report.blockers.append("Falta seleccionar explícitamente un tipo de publicación disponible.")
        elif draft.listing_type_id not in available_ids:
            report.blockers.append("El tipo de publicación seleccionado no está disponible actualmente para este seller y categoría.")
        else:
            report.verified.append(f"Tipo de publicación {draft.listing_type_id} confirmado para seller y categoría.")

    def _check_costs_and_shipping(self, draft, token, listing_response, report):
        available_ids = {item.get("id") for item in (listing_response.get("available") or [])}
        try:
            costs = self.metadata.listing_prices(site_id="MLA", price=draft.price, currency_id=draft.currency, category_id=draft.category_id, access_token=token)
            report.selling_condition["cost_estimates"] = [
                {key: item.get(key) for key in ("listing_type_id", "listing_type_name", "currency_id", "sale_fee_amount", "sale_fee_details")}
                for item in costs if item.get("listing_type_id") in available_ids
            ] if isinstance(costs, list) else []
            report.verified.append("Costos estimados consultados para precio, categoría y tipos disponibles.")
        except MercadoLibreError as error:
            report.pending.append(f"No se pudieron consultar costos estimados: {error}")
        try:
            seller = self.metadata.seller_shipping_preferences(active_connection().seller_id, token)
            category = self.metadata.category_shipping_preferences(draft.category_id, token)
            seller_modes = set(seller.get("modes") or []) if isinstance(seller, dict) else set()
            category_modes = {item.get("mode") for item in (category.get("logistics") or []) if item.get("mode")} if isinstance(category, dict) else set()
            compatible = sorted(seller_modes & category_modes)
            report.selling_condition["shipping_options"] = compatible
            report.selling_condition["local_pick_up"] = seller.get("local_pick_up") if isinstance(seller, dict) else None
            report.verified.append("Preferencias de envío de seller y categoría consultadas.")
            details = draft.commercial_details or {}
            if not details.get("channels"):
                report.pending.append("Falta confirmar el canal de venta aplicable.")
            if not details.get("shipping_mode"):
                report.pending.append("Falta seleccionar un modo de envío compatible o documentar que no aplica.")
        except MercadoLibreError as error:
            report.pending.append(f"No se pudieron consultar opciones de envío: {error}")

    def _check_sale_terms(self, draft, report):
        report.selling_condition.update({"price": str(draft.price), "currency_id": draft.currency, "category_id": draft.category_id, "buying_mode": "buy_it_now", "listing_type_id": draft.listing_type_id or None, "sale_terms": draft.sale_terms})
        details = draft.commercial_details or {}
        if details.get("channels"):
            report.selling_condition["channels"] = details["channels"]
        if details.get("shipping_mode"):
            report.selling_condition["shipping_mode"] = details["shipping_mode"]
        condition = draft.additional_attributes.get("ITEM_CONDITION", {})
        condition_name = str(condition.get("value_name", "") if isinstance(condition, dict) else condition).lower()
        if "reacond" in condition_name:
            terms = {term.get("id") for term in draft.sale_terms if isinstance(term, dict)}
            missing = {"WARRANTY_TYPE", "WARRANTY_TIME"} - terms
            if missing:
                report.blockers.append("ITEM_CONDITION reacondicionado requiere términos de garantía: " + ", ".join(sorted(missing)) + ".")
        if not draft.sale_terms:
            report.pending.append("No hay términos de venta cargados; completalos sólo cuando la categoría o condición lo requiera.")
        report.pending.append("Canales y configuración de envío no se infieren: deben confirmarse con un recurso documentado o una decisión explícita antes de escribir.")

    def _check_images(self, draft, details, report):
        images = list(draft.images.all())
        settings = details.get("settings") or {}
        maximum = settings.get("max_pictures_per_item")
        if maximum is not None and len(images) > maximum:
            report.blockers.append(f"Hay {len(images)} imágenes y la categoría informa un máximo de {maximum}.")
        if not images:
            report.warnings.append("No hay imágenes locales; la obligatoriedad puede depender de la categoría y tipo de publicación.")
            report.pending.append("No hay imágenes locales. La entidad User Product deberá recibirlas antes de crear una condición de venta.")
        for image in images:
            try:
                with image.image.open("rb") as file_handle:
                    result = validate_image_file(file_handle, declared_content_type=image.content_type, declared_size=image.size)
                report.image_details.append({"id": image.pk, "filename": image.original_filename, "position": image.position, **result})
            except ValidationError as error:
                report.blockers.append(str(error.message))
        if images:
            report.pending.append("Las imágenes son locales y no fueron transferidas ni relacionadas con Mercado Libre.")

    def _build_preview(self, draft, report):
        family = {"family_name": draft.family_name, "domain_id": draft.domain_id, "attributes": [_attribute_payload(entry["id"], draft.additional_attributes.get(entry["id"])) for entry in report.family_attributes if entry["value"]]}
        product = {"attributes": [_attribute_payload(entry["id"], draft.additional_attributes.get(entry["id"])) for entry in report.product_attributes if entry["value"]], "local_images": [{"draft_image_id": image["id"], "position": image["position"]} for image in report.image_details]}
        selling = {key: value for key, value in report.selling_condition.items() if key not in {"available_listing_types", "available_sale_terms", "cost_estimates", "shipping_options"} and value not in (None, [], "")}
        report.payload_preview = {"model": "user_products", "reference_title_not_sent": draft.title, "user_product": family, "product_specific_data": product, "selling_condition": selling, "inherited_by_selling_condition": ["family_name", "domain_id", "attributes", "pictures", "title", "available_quantity"]}

    def _technical_operations(self, report):
        report.operations = [
            "Sin ejecutar: crear o identificar el User Product con datos de familia, atributos e imágenes validados.",
            "Sin ejecutar: transferir imágenes y persistir cada identificador externo por etapa.",
            "Sin ejecutar: asociar una condición de venta con precio, categoría, moneda y tipo seleccionado.",
            "Sin ejecutar: reconciliar resultados partial o unknown mediante consultas GET antes de cualquier reintento.",
            "Sin ejecutar: requerir una confirmación comercial explícita inmediatamente antes de cada escritura.",
        ]


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
