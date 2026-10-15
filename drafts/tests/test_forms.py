from io import BytesIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image
from drafts.forms import ImageUploadForm, ProductDraftForm, TestPublicationForm


class DraftFormTests(SimpleTestCase):
    def test_title_and_attributes_validation(self):
        form = ProductDraftForm(data={"title": "ab", "price": "10", "stock": 0, "condition": "new", "status": "draft", "attributes_json": "[]"})
        self.assertFalse(form.is_valid())
        self.assertIn("title", form.errors)
        self.assertIn("attributes_json", form.errors)

    def test_valid_image_upload(self):
        stream = BytesIO()
        Image.new("RGB", (10, 10)).save(stream, "PNG")
        upload = SimpleUploadedFile("photo.png", stream.getvalue(), content_type="image/png")
        ImageUploadForm.validate_upload(upload)

    def test_invalid_image_type_is_rejected(self):
        upload = SimpleUploadedFile("file.txt", b"not image", content_type="text/plain")
        with self.assertRaisesMessage(Exception, "formato no permitido"):
            ImageUploadForm.validate_upload(upload)

    def test_required_category_attribute_is_validated_and_serialized(self):
        definition = {"id": "BRAND", "name": "Marca", "value_type": "list", "tags": {"required": True}, "values": [{"id": "1", "name": "Marca real"}]}
        data = {"title": "Producto válido", "price": "10", "stock": 0, "condition": "new", "status": "draft", "attributes_json": "{}"}
        missing = ProductDraftForm(data=data, attribute_definitions=[definition])
        self.assertFalse(missing.is_valid())
        self.assertIn("ml_attribute_BRAND", missing.errors)
        complete = ProductDraftForm(data={**data, "ml_attribute_BRAND": "1"}, attribute_definitions=[definition])
        self.assertTrue(complete.is_valid())
        self.assertEqual(complete.save(commit=False).additional_attributes["BRAND"]["value_name"], "Marca real")

    def test_test_publication_only_allows_free(self):
        form = TestPublicationForm(data={"listing_type_id": "gold_special", "channels_json": '["marketplace"]', "confirmed": True})
        self.assertFalse(form.is_valid())
