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
        Image.new("RGB", (500, 500)).save(stream, "JPEG")
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

    def test_list_filters_by_search_and_status(self):
        matching = ProductDraft.objects.create(title="Amplificador valvular", price="1200", status=ProductDraft.Status.READY)
        ProductDraft.objects.create(title="Cable de audio", price="50", status=ProductDraft.Status.DRAFT)

        response = self.client.get(reverse("drafts:list"), {"q": "amplificador", "status": ProductDraft.Status.READY})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, matching.title)
        self.assertNotContains(response, "Cable de audio")
        self.assertContains(response, "Todos los estados")

    def test_catalog_formats_ars_price_once(self):
        ProductDraft.objects.create(title="Precio formateado", price="100000", status=ProductDraft.Status.DRAFT)

        response = self.client.get(reverse("drafts:list"))

        self.assertContains(response, "$ 100.000,00")
        self.assertNotContains(response, "AR$ 100000,00ARS")

    def test_diagnostic_without_connection_makes_no_external_request(self):
        response = self.client.get(reverse("drafts:diagnostic"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No hay cuenta conectada.")

    def test_create_requires_csrf_token(self):
        client = self.client_class(enforce_csrf_checks=True)
        response = client.post(reverse("drafts:create"), {"title": "Producto válido", "price": "12", "stock": 0, "condition": "new", "status": "draft"})
        self.assertEqual(response.status_code, 403)
