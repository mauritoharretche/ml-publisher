from io import BytesIO
from tempfile import TemporaryDirectory
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from drafts.models import ProductDraft


class DraftViewTests(TestCase):
    def make_image(self):
        stream = BytesIO()
        Image.new("RGB", (10, 10)).save(stream, "JPEG")
        return SimpleUploadedFile("product.jpg", stream.getvalue(), content_type="image/jpeg")

    def test_create_draft_with_multiple_images(self):
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            response = self.client.post(reverse("drafts:create"), {
                "title": "Producto de prueba", "price": "1250.50", "description": "Descripción",
                "category_id": "", "condition": "new", "stock": "2", "status": "draft",
                "attributes_json": '{"COLOR": "Azul"}', "images": [self.make_image(), self.make_image()],
            })
        draft = ProductDraft.objects.get()
        self.assertRedirects(response, reverse("drafts:detail", kwargs={"pk": draft.pk}))
        self.assertEqual(draft.images.count(), 2)
        self.assertEqual(draft.currency, "ARS")

    def test_create_form_remains_available_without_connection(self):
        response = self.client.get(reverse("drafts:create"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sugerir categorías")

    def test_create_requires_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(reverse("drafts:create"), {"title": "Producto válido", "price": "12", "stock": 0, "condition": "new", "status": "draft"})
        self.assertEqual(response.status_code, 403)
