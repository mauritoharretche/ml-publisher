from datetime import timedelta
from io import BytesIO
from unittest.mock import Mock, patch
from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image
from drafts.models import MercadoLibreConnection, ProductDraft, ProductImage, PublicationAttempt
from drafts.services.credentials import encrypt
from drafts.services.exceptions import MercadoLibreAuthorizationError, MercadoLibreNetworkError
from drafts.services.publishing import PublicationBlocked, PublicationOptions, PublicationService

KEY = Fernet.generate_key().decode()
PREFIX = "Item de Prueba - Por favor, NO OFERTAR"


@override_settings(MERCADO_LIBRE_ENCRYPTION_KEY=KEY, MERCADO_LIBRE_ENABLE_TEST_WRITES=True, MERCADO_LIBRE_TEST_SELLER_ID="44")
class PublicationServiceTests(TestCase):
    def setUp(self):
        self.connection = MercadoLibreConnection.objects.create(seller_id=44, access_token_encrypted=encrypt("token"), refresh_token_encrypted=encrypt("refresh"), token_expires_at=timezone.now() + timedelta(hours=1))
        self.draft = ProductDraft.objects.create(title=f"{PREFIX} producto", price="100", description="Prueba", category_id="MLA1", condition="new", stock=1)
        stream = BytesIO(); Image.new("RGB", (10, 10)).save(stream, "JPEG")
        ProductImage.objects.create(draft=self.draft, image=SimpleUploadedFile("test.jpg", stream.getvalue(), content_type="image/jpeg"), position=0, original_filename="test.jpg", content_type="image/jpeg", size=len(stream.getvalue()))
        self.metadata = Mock()
        self.metadata.seller_details.return_value = {"id": 44, "site_id": "MLA", "tags": ["test_user", "user_product_seller"]}
        self.metadata.category_attributes.return_value = []
        self.metadata.category_details.return_value = {"name": "Otros", "settings": {}}
        self.writer = Mock(); self.writer.upload_picture.return_value = "PIC1"; self.writer.create_item.return_value = {"id": "MLA1", "user_product_id": "MLAU1", "permalink": "https://example.test/item"}
        self.options = PublicationOptions("free", ["marketplace"])

    def service(self):
        return PublicationService(self.writer, self.metadata, token_getter=lambda *_args, **_kwargs: "token")

    @patch("drafts.services.publishing.active_connection")
    def test_user_products_is_blocked_before_any_external_write(self, connection):
        connection.return_value = self.connection
        with self.assertRaisesMessage(PublicationBlocked, "genera el título"):
            self.service().publish(self.draft, self.options, confirmed=True)
        self.writer.upload_picture.assert_not_called()

    @override_settings(MERCADO_LIBRE_ENABLE_TEST_WRITES=False)
    @patch("drafts.services.publishing.active_connection")
    def test_disabled_flag_blocks_before_writer(self, connection):
        connection.return_value = self.connection
        with self.assertRaisesMessage(PublicationBlocked, "deshabilitadas"):
            self.service().publish(self.draft, self.options, confirmed=True)
        self.writer.upload_picture.assert_not_called()

    @override_settings(MERCADO_LIBRE_TEST_SELLER_ID="99")
    @patch("drafts.services.publishing.active_connection")
    def test_wrong_seller_and_confirmation_or_title_are_rejected(self, connection):
        connection.return_value = self.connection
        with self.assertRaisesMessage(PublicationBlocked, "no coincide"):
            self.service().publish(self.draft, self.options, confirmed=True)
        with self.assertRaisesMessage(PublicationBlocked, "confirmar"):
            self.service().publish(self.draft, self.options, confirmed=False)
        self.draft.title = "Título normal"
        with self.assertRaisesMessage(PublicationBlocked, "debe comenzar"):
            self.service().publish(self.draft, self.options, confirmed=True)

    @patch("drafts.services.publishing.active_connection")
    def test_upload_failure_and_timeout_are_persisted_without_retry(self, connection):
        connection.return_value = self.connection
        self.writer.upload_picture.side_effect = MercadoLibreAuthorizationError("Imagen rechazada")
        with self.assertRaises(PublicationBlocked): self.service().publish(self.draft, self.options, confirmed=True)
        self.assertFalse(PublicationAttempt.objects.exists())
        PublicationAttempt.objects.create(draft=self.draft, seller_id=44, flow="legacy", listing_type_id="free", channels=["marketplace"], request_key="12345678-1234-1234-1234-123456789012", status=PublicationAttempt.Status.UNKNOWN, safe_error="Resultado desconocido")
        self.assertEqual(PublicationAttempt.objects.get().status, PublicationAttempt.Status.UNKNOWN)

    @patch("drafts.services.publishing.active_connection")
    def test_duplicate_is_blocked_and_payloads_differ_by_flow(self, connection):
        connection.return_value = self.connection
        PublicationAttempt.objects.create(draft=self.draft, seller_id=44, flow="legacy", listing_type_id="free", channels=["marketplace"], request_key="12345678-1234-1234-1234-123456789012", status=PublicationAttempt.Status.PARTIAL)
        with self.assertRaisesMessage(PublicationBlocked, "Ya existe"):
            self.service()._create_intent(self.draft, 44, "legacy", self.options)
        pictures = ["P1"]
        self.assertIn("title", self.service()._payload(self.draft, "legacy", self.options, pictures))

    def test_webp_is_rejected_before_upload(self):
        stream = BytesIO(); Image.new("RGB", (10, 10)).save(stream, "WEBP")
        ProductImage.objects.create(draft=self.draft, image=SimpleUploadedFile("test.webp", stream.getvalue(), content_type="image/webp"), position=1, original_filename="test.webp", content_type="image/webp", size=len(stream.getvalue()))
        with self.assertRaisesMessage(PublicationBlocked, "WebP"):
            self.service()._validate_images(self.draft)
