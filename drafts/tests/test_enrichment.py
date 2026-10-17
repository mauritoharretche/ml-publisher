from io import BytesIO
from unittest.mock import Mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image

from drafts.models import ProductDraft, ProductImage
from drafts.services.enrichment import EnrichmentError, ProductEnrichmentService


class EnrichmentTests(TestCase):
    def setUp(self):
        self.draft = ProductDraft.objects.create(title="Vinilo", price="100", stock=1, family_name="Manual", additional_attributes={"ALBUM_NAME": {"value_name": "Manual"}})
        stream = BytesIO(); Image.new("RGB", (500, 500)).save(stream, "JPEG")
        ProductImage.objects.create(draft=self.draft, image=SimpleUploadedFile("cover.jpg", stream.getvalue(), content_type="image/jpeg"), position=0, original_filename="cover.jpg", content_type="image/jpeg", size=len(stream.getvalue()))
        self.vision = Mock(); self.catalog = Mock(); self.catalog.search.return_value = [{"id": "r1", "title": "Candidate"}]
        self.definitions = [{"id": "MUSIC_ARTIST_NAME"}, {"id": "ALBUM_NAME"}, {"id": "PRODUCTION_COMPANY"}, {"id": "GTIN"}]

    def test_extracts_suggestions_and_preserves_manual_values(self):
        self.vision.analyze.return_value = {"visible_text": ["CAT-1"], "fields": [{"key": "artist", "value": "The Beatles", "image_ids": [1], "evidence": "Etiqueta", "confidence": "high"}, {"key": "album_name", "value": "Live", "confidence": "medium"}, {"key": "production_company", "value": "Label", "confidence": "low"}, {"key": "barcode", "value": "123", "confidence": "low"}]}
        service = ProductEnrichmentService(self.vision, self.catalog)
        run = service.run(self.draft, attribute_definitions=self.definitions)
        self.assertEqual(run.status, "completed")
        self.assertEqual(len(run.catalog_candidates), 1)
        _, applied = service.apply(run, ["artist", "album_name", "production_company", "barcode"])
        self.draft.refresh_from_db()
        self.assertIn("production_company", applied)
        self.assertEqual(self.draft.additional_attributes["ALBUM_NAME"]["value_name"], "Manual")
        self.assertEqual(self.draft.price, 100)
        self.assertEqual(self.draft.images.count(), 1)

    def test_unknown_attributes_and_provider_failure_are_safe(self):
        self.vision.analyze.return_value = {"fields": [{"key": "invented", "value": "No aplicar"}]}
        run = ProductEnrichmentService(self.vision, self.catalog).run(self.draft, attribute_definitions=self.definitions)
        self.assertFalse(run.suggestions[0]["applicable"])
        failing = Mock(); failing.analyze.side_effect = EnrichmentError("timeout seguro")
        with self.assertRaisesMessage(EnrichmentError, "timeout seguro"):
            ProductEnrichmentService(failing, self.catalog).run(self.draft, attribute_definitions=self.definitions)
        self.assertEqual(self.draft.images.count(), 1)

    @override_settings(OPENAI_API_KEY="", OPENAI_MODEL="")
    def test_missing_configuration_is_reported_by_provider(self):
        from drafts.services.enrichment import OpenAIVisionProvider
        with self.assertRaisesMessage(EnrichmentError, "no está configurada"):
            OpenAIVisionProvider().analyze(self.draft, list(self.draft.images.all()))

    def test_multiple_catalog_editions_remain_candidates(self):
        self.vision.analyze.return_value = {"fields": [{"key": "artist", "value": "The Beatles"}, {"key": "album_name", "value": "Live"}, {"key": "catalog_number", "value": "CAT-1"}, {"key": "barcode", "value": "123"}]}
        self.catalog.search.return_value = [{"id": "first"}, {"id": "second"}]
        run = ProductEnrichmentService(self.vision, self.catalog).run(self.draft, attribute_definitions=self.definitions)
        self.assertEqual([candidate["id"] for candidate in run.catalog_candidates], ["first", "second"])
        self.catalog.search.assert_called_once_with(title="Live", artist="The Beatles", barcode="123", catalog_number="CAT-1")
