from datetime import timedelta
from unittest.mock import Mock
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone
from drafts.models import MercadoLibreConnection, ProductDraft
from drafts.services.credentials import encrypt
from drafts.services.exceptions import MercadoLibreAuthorizationError
from drafts.services.preflight import IntegrationDiagnosticService, PublicationPreflightService

KEY = Fernet.generate_key().decode()


@override_settings(MERCADO_LIBRE_ENCRYPTION_KEY=KEY)
class PreflightTests(TestCase):
    def setUp(self):
        self.connection = MercadoLibreConnection.objects.create(seller_id=42, access_token_encrypted=encrypt("token"), refresh_token_encrypted=encrypt("refresh"), token_expires_at=timezone.now() + timedelta(hours=1))
        self.draft = ProductDraft.objects.create(title="Producto real", price="100", description="Texto", category_id="MLA1", condition="new", stock=1)
        self.metadata = Mock()
        self.metadata.seller_details.return_value = {"id": 42, "site_id": "MLA", "tags": ["user_product_seller"]}
        self.metadata.category_attributes.return_value = [{"id": "BRAND", "name": "Marca", "tags": {"required": True}, "values": [{"id": "1", "name": "Marca admitida"}]}]
        self.metadata.category_details.return_value = {"name": "Categoría", "settings": {"max_pictures_per_item": 6}}

    def service(self):
        return PublicationPreflightService(self.metadata, token_getter=lambda *_args, **_kwargs: "token")

    def test_user_products_flow_and_missing_attribute_are_reported(self):
        report = self.service().run(self.draft)
        self.assertEqual(report.flow, "user_products")
        self.assertTrue(any("Marca" in item for item in report.blockers))
        self.assertEqual(report.payload_preview["model"], "user_products")
        self.assertTrue(any("imágenes" in item.lower() for item in report.warnings))

    def test_absent_tag_does_not_assert_classic_flow(self):
        self.draft.additional_attributes = {"BRAND": {"value_id": "1", "value_name": "Marca admitida"}}
        self.draft.save()
        self.metadata.seller_details.return_value = {"id": 42, "site_id": "MLA", "tags": []}
        report = self.service().run(self.draft)
        self.assertEqual(report.flow, "undetermined")
        self.assertTrue(any("no confirma" in item for item in report.pending))
        self.assertEqual(report.payload_preview["model"], "undetermined")

    def test_incomplete_api_data_and_permissions_are_comprehensible(self):
        self.metadata.seller_details.side_effect = MercadoLibreAuthorizationError("Permiso insuficiente.")
        report = self.service().run(self.draft)
        self.assertIn("Permiso insuficiente.", report.blockers)
        diagnostic = IntegrationDiagnosticService(self.metadata, token_getter=lambda *_args, **_kwargs: "token").run(title="producto")
        self.assertFalse(diagnostic[-1]["ok"])
        self.assertIn("Permiso insuficiente", diagnostic[-1]["detail"])
