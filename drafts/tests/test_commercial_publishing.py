from datetime import timedelta
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from PIL import Image

from drafts.models import MercadoLibreConnection, ProductDraft, ProductImage, PublicationAttempt
from drafts.services.commercial_publishing import CommercialPublicationService, draft_fingerprint
from drafts.services.credentials import encrypt
from drafts.services.exceptions import MercadoLibreAuthorizationError, MercadoLibreNetworkError
from drafts.services.publishing import PublicationBlocked

KEY = Fernet.generate_key().decode()


@override_settings(MERCADO_LIBRE_ENCRYPTION_KEY=KEY, MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES=True, MERCADO_LIBRE_PRODUCTION_SELLER_ID="178383685")
class CommercialPublishingTests(TestCase):
    def setUp(self):
        self.connection = MercadoLibreConnection.objects.create(seller_id=178383685, access_token_encrypted=encrypt("token"), refresh_token_encrypted=encrypt("refresh"), token_expires_at=timezone.now() + timedelta(hours=1))
        self.draft = ProductDraft.objects.create(title="Referencia", family_name="Álbum", domain_id="MLA-MUSIC_ALBUMS", category_id="MLA1174", price="100000", condition="used", stock=1, listing_type_id="gold_special", additional_attributes={"ITEM_CONDITION": {"value_id": "used", "value_name": "Usado"}}, commercial_details={"channels": ["marketplace"], "shipping_mode": "me2"}, edition_and_authenticity_verified=True, commercialization_authorization_confirmed=True)
        stream = BytesIO(); Image.new("RGB", (500, 500)).save(stream, "JPEG")
        ProductImage.objects.create(draft=self.draft, image=SimpleUploadedFile("cover.jpg", stream.getvalue(), content_type="image/jpeg"), position=0, original_filename="cover.jpg", content_type="image/jpeg", size=len(stream.getvalue()))
        self.writer = Mock(); self.writer.upload_picture.return_value = {"id": "PIC1"}; self.writer.create_initial_item.return_value = {"id": "MLA1", "user_product_id": "MLAU1", "family_id": "F1", "permalink": "https://example.test/item"}
        self.metadata = Mock(); self.metadata.seller_details.return_value = {"id": 178383685, "site_id": "MLA", "tags": ["user_product_seller"]}
        self.report = SimpleNamespace(flow="user_products", mode="commercial", blockers=[])

    def service(self):
        return CommercialPublicationService(self.writer, self.metadata, token_getter=lambda *_args, **_kwargs: "token", preflight_service=self.report)

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_disabled_flag_blocks_before_token_or_writer(self, active):
        active.return_value = self.connection
        with self.settings(MERCADO_LIBRE_ENABLE_PRODUCTION_WRITES=False):
            with self.assertRaisesMessage(PublicationBlocked, "deshabilitadas"):
                self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        self.writer.upload_picture.assert_not_called()
        self.metadata.seller_details.assert_not_called()

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_initial_creation_omits_title_and_persists_stages(self, active):
        active.return_value = self.connection
        attempt = self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        payload = self.writer.create_initial_item.call_args.args[0]
        self.assertNotIn("title", payload)
        self.assertEqual(payload["family_name"], "Álbum")
        self.assertEqual(attempt.status, PublicationAttempt.Status.PUBLISHED)
        self.assertEqual(attempt.picture_ids[0]["picture_id"], "PIC1")
        self.assertEqual(attempt.family_id, "F1")

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_partial_upload_and_timeout_are_not_retried(self, active):
        active.return_value = self.connection
        self.writer.create_initial_item.side_effect = MercadoLibreNetworkError("timeout")
        with self.assertRaisesMessage(PublicationBlocked, "desconocido"):
            self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        attempt = PublicationAttempt.objects.get()
        self.assertEqual(attempt.status, PublicationAttempt.Status.UNKNOWN)
        with self.assertRaises(PublicationBlocked):
            self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_confirmation_change_and_wrong_seller_are_blocked(self, active):
        active.return_value = self.connection
        fingerprint = draft_fingerprint(self.draft)
        self.draft.stock = 2; self.draft.save()
        with self.assertRaisesMessage(PublicationBlocked, "cambió"):
            self.service().publish_initial(self.draft, confirmation_fingerprint=fingerprint)
        self.draft.refresh_from_db()
        with self.settings(MERCADO_LIBRE_PRODUCTION_SELLER_ID="1"):
            with self.assertRaisesMessage(PublicationBlocked, "no coincide"):
                self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_add_condition_is_separate_and_reconciliation_is_get_only(self, active):
        active.return_value = self.connection
        self.writer.create_selling_condition.return_value = {"id": "MLA2"}
        result = self.service().add_selling_condition("MLAU1", self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        self.assertEqual(result["id"], "MLA2")
        payload = self.writer.create_selling_condition.call_args.args[1]
        self.assertNotIn("title", payload)
        attempt = PublicationAttempt.objects.create(draft=self.draft, seller_id=178383685, flow="user_products", listing_type_id="gold_special", channels=["marketplace"], request_key="12345678-1234-1234-1234-123456789012", user_product_id="MLAU1", status=PublicationAttempt.Status.UNKNOWN)
        self.metadata.items_for_user_product.return_value = {"results": ["MLA3"]}
        reconciled = self.service().reconcile(attempt)
        self.assertEqual(reconciled.item_id, "MLA3")

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_incomplete_preflight_seller_flow_and_duplicate_are_blocked(self, active):
        active.return_value = self.connection
        self.report.blockers = ["Falta condición"]
        with self.assertRaisesMessage(PublicationBlocked, "preflight"):
            self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        self.writer.upload_picture.assert_not_called()
        self.report.blockers = []
        self.metadata.seller_details.return_value["tags"] = []
        with self.assertRaisesMessage(PublicationBlocked, "User Products"):
            self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        self.metadata.seller_details.return_value["tags"] = ["user_product_seller"]
        PublicationAttempt.objects.create(draft=self.draft, seller_id=178383685, flow="user_products", listing_type_id="gold_special", channels=["marketplace"], request_key="12345678-1234-1234-1234-123456789012", status=PublicationAttempt.Status.PARTIAL)
        with self.assertRaisesMessage(PublicationBlocked, "Ya existe"):
            self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))

    @patch("drafts.services.commercial_publishing.active_connection")
    def test_create_error_and_condition_error_do_not_claim_success(self, active):
        active.return_value = self.connection
        self.writer.create_initial_item.side_effect = MercadoLibreAuthorizationError("rechazado")
        with self.assertRaisesMessage(PublicationBlocked, "rechazado"):
            self.service().publish_initial(self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
        self.assertEqual(PublicationAttempt.objects.get().status, PublicationAttempt.Status.PARTIAL)
        self.writer.create_selling_condition.side_effect = MercadoLibreAuthorizationError("condición rechazada")
        with self.assertRaisesMessage(MercadoLibreAuthorizationError, "condición rechazada"):
            self.service().add_selling_condition("MLAU1", self.draft, confirmation_fingerprint=draft_fingerprint(self.draft))
