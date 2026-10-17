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
        Image.new("RGB", (500, 500)).save(stream, "PNG")
        upload = SimpleUploadedFile("photo.png", stream.getvalue(), content_type="image/png")
        ImageUploadForm.validate_upload(upload)

    def test_invalid_image_type_is_rejected(self):
        upload = SimpleUploadedFile("file.txt", b"not image", content_type="text/plain")
        with self.assertRaisesMessage(Exception, "formato no permitido"):
            ImageUploadForm.validate_upload(upload)

    def test_webp_and_small_images_are_rejected_before_storage(self):
        webp = BytesIO()
        Image.new("RGB", (500, 500)).save(webp, "WEBP")
        with self.assertRaisesMessage(Exception, "WebP no se admite"):
            ImageUploadForm.validate_upload(SimpleUploadedFile("photo.webp", webp.getvalue(), content_type="image/webp"))
        small = BytesIO()
        Image.new("RGB", (499, 500)).save(small, "PNG")
        with self.assertRaisesMessage(Exception, "resolución mínima"):
            ImageUploadForm.validate_upload(SimpleUploadedFile("small.png", small.getvalue(), content_type="image/png"))

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

    def test_commercial_completion_is_persisted_and_sale_terms_are_constrained(self):
        definition = {"id": "ITEM_CONDITION", "name": "Condición del ítem", "value_type": "list", "tags": {}, "values": [{"id": "used", "name": "Usado"}]}
        data = {"title": "Vinilo verificado", "price": "100000", "stock": 1, "condition": "used", "status": "draft", "attributes_json": "{}", "ml_attribute_ITEM_CONDITION": "used", "sale_terms_json": '[{"id":"WARRANTY_TYPE","value_name":"Sin garantía"}]', "label_or_company": "Sello leído", "catalog_number": "CAT-1", "physical_edition": "Edición física", "manufacturing_country": "Argentina", "channels_json": '["marketplace"]', "shipping_mode": "me2", "edition_and_authenticity_verified": "on", "commercialization_authorization_confirmed": "on"}
        form = ProductDraftForm(data=data, attribute_definitions=[definition], sale_term_definitions=[{"id": "WARRANTY_TYPE"}], shipping_options=["me2"])
        self.assertTrue(form.is_valid())
        draft = form.save(commit=False)
        self.assertEqual(draft.commercial_details["catalog_number"], "CAT-1")
        self.assertEqual(draft.commercial_details["channels"], ["marketplace"])
        invalid = ProductDraftForm(data={**data, "sale_terms_json": '[{"id":"UNKNOWN"}]'}, attribute_definitions=[definition], sale_term_definitions=[{"id": "WARRANTY_TYPE"}], shipping_options=["me2"])
        self.assertFalse(invalid.is_valid())
        self.assertIn("sale_terms_json", invalid.errors)
