"""Enriquecimiento opcional: sugerencias con evidencia, nunca hechos comerciales."""
import base64
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction

from drafts.models import ProductEnrichment


class EnrichmentError(Exception):
    pass


class OpenAIVisionProvider:
    def configured(self):
        return bool(settings.OPENAI_API_KEY and settings.OPENAI_MODEL)

    def analyze(self, draft, images):
        if not self.configured():
            raise EnrichmentError("La IA no está configurada. Definí OPENAI_API_KEY y OPENAI_MODEL en el entorno local.")
        content = [{"type": "input_text", "text": "Analizá estas fotos de un producto musical físico. Devolvé sólo JSON con visible_text y fields. Cada field: key (artist, album_name, format, production_company, catalog_number, barcode, country, year, description), value, image_ids, evidence, confidence (high, medium o low), and uncertainty. description, si se incluye, debe ser español argentino factual y usar sólo datos visibles. Incluí sólo texto legible o datos claramente visibles; no afirmes autenticidad, licencia ni procedencia legal."}]
        for image in images:
            with image.image.open("rb") as handle:
                encoded = base64.b64encode(handle.read()).decode()
            content.append({"type": "input_image", "image_url": f"data:{image.content_type or 'image/jpeg'};base64,{encoded}", "detail": "high"})
        payload = {"model": settings.OPENAI_MODEL, "store": False, "input": [{"role": "user", "content": content}]}
        request = Request("https://api.openai.com/v1/responses", data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}", "Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=settings.OPENAI_TIMEOUT) as response:
                data = json.loads(response.read().decode())
        except Exception as error:
            raise EnrichmentError("No se pudo completar el análisis de imágenes con IA.") from error
        text = data.get("output_text", "")
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise EnrichmentError("La IA devolvió un resultado no estructurado; no se aplicó ningún dato.") from error


class MusicBrainzProvider:
    def search(self, *, title, artist="", barcode="", catalog_number=""):
        terms = [f'release:"{title}"']
        if artist: terms.append(f'artist:"{artist}"')
        if barcode: terms.append(f'barcode:{barcode}')
        if catalog_number: terms.append(f'catno:"{catalog_number}"')
        url = "https://musicbrainz.org/ws/2/release/?" + urlencode({"query": " AND ".join(terms), "fmt": "json", "limit": 5})
        request = Request(url, headers={"Accept": "application/json", "User-Agent": settings.MUSICBRAINZ_USER_AGENT})
        try:
            with urlopen(request, timeout=10) as response:
                results = json.loads(response.read().decode()).get("releases", [])
        except Exception as error:
            raise EnrichmentError("No se pudo consultar MusicBrainz.") from error
        return [{"id": row.get("id"), "title": row.get("title"), "date": row.get("date"), "country": row.get("country"), "barcode": row.get("barcode"), "score": row.get("score"), "url": f"https://musicbrainz.org/release/{row.get('id')}"} for row in results]


class ProductEnrichmentService:
    FIELD_TO_ATTRIBUTE = {"artist": "MUSIC_ARTIST_NAME", "album_name": "ALBUM_NAME", "production_company": "PRODUCTION_COMPANY", "barcode": "GTIN"}

    def __init__(self, vision=None, catalog=None):
        self.vision = vision or OpenAIVisionProvider()
        self.catalog = catalog or MusicBrainzProvider()

    def run(self, draft, *, attribute_definitions):
        if draft.enrichments.filter(status=ProductEnrichment.Status.RUNNING).exists():
            raise EnrichmentError("Ya hay un enriquecimiento en curso para este borrador.")
        images = list(draft.images.order_by("position", "id")[:settings.OPENAI_MAX_ENRICHMENT_IMAGES])
        if not images:
            raise EnrichmentError("Cargá al menos una imagen antes de solicitar el análisis.")
        run = ProductEnrichment.objects.create(draft=draft, provider="openai", model=settings.OPENAI_MODEL, image_ids=[image.pk for image in images])
        try:
            vision = self.vision.analyze(draft, images)
            fields = vision.get("fields", []) if isinstance(vision, dict) else []
            suggestions = self._suggest(fields, attribute_definitions)
            values = {entry.get("key"): entry.get("value", "") for entry in fields if isinstance(entry, dict)}
            candidates = self.catalog.search(title=values.get("album_name") or draft.title, artist=values.get("artist", ""), barcode=values.get("barcode", ""), catalog_number=values.get("catalog_number", ""))
            run.suggestions, run.catalog_candidates = suggestions, candidates
            run.technical_result, run.status = {"visible_text": vision.get("visible_text", []), "image_count": len(images)}, ProductEnrichment.Status.COMPLETED
            run.save(update_fields=["suggestions", "catalog_candidates", "technical_result", "status", "updated_at"])
            return run
        except EnrichmentError as error:
            run.status, run.safe_error = ProductEnrichment.Status.FAILED, str(error)
            run.save(update_fields=["status", "safe_error", "updated_at"])
            raise

    def _suggest(self, fields, definitions):
        definition_by_id = {row.get("id"): row for row in definitions}
        suggestions = []
        for field in fields:
            if not isinstance(field, dict) or not field.get("value") or not field.get("key"):
                continue
            key, attribute_id = field["key"], self.FIELD_TO_ATTRIBUTE.get(field["key"])
            target = "family_name" if key in {"artist", "album_name"} else "description" if key == "description" else attribute_id
            allowed = target in {"family_name", "description"} or (attribute_id in definition_by_id)
            suggestions.append({"key": key, "target": target, "attribute_id": attribute_id, "value": str(field["value"]), "source": "image", "evidence": field.get("evidence", "Texto o rasgo visible informado por IA."), "image_ids": field.get("image_ids", []), "confidence": field.get("confidence", "low"), "status": "needs_confirmation", "applicable": allowed, "uncertainty": field.get("uncertainty", "Requiere confirmación humana.")})
        return suggestions

    def apply(self, run, selected_keys):
        if run.status != ProductEnrichment.Status.COMPLETED:
            raise EnrichmentError("Sólo se pueden aplicar sugerencias de un análisis completado.")
        draft = run.draft
        selected = [item for item in run.suggestions if item.get("key") in selected_keys and item.get("applicable")]
        with transaction.atomic():
            draft = type(draft).objects.select_for_update().get(pk=draft.pk)
            attributes = dict(draft.additional_attributes or {})
            applied = []
            for item in selected:
                if item["target"] == "family_name":
                    if not draft.family_name:
                        draft.family_name = item["value"]; applied.append(item["key"])
                elif item["target"] == "description":
                    if not draft.description:
                        draft.description = item["value"]; applied.append(item["key"])
                elif item.get("attribute_id") and item["attribute_id"] not in attributes:
                    attributes[item["attribute_id"]] = {"value_name": item["value"]}; applied.append(item["key"])
            draft.additional_attributes = attributes
            draft.save(update_fields=["family_name", "description", "additional_attributes", "updated_at"])
            run.technical_result = {**run.technical_result, "applied_keys": applied}
            run.save(update_fields=["technical_result", "updated_at"])
        return draft, applied
