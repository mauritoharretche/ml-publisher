from datetime import timedelta
from unittest.mock import Mock

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.utils import timezone

from drafts.models import MercadoLibreConnection, ProductDraft
from drafts.services.credentials import encrypt
from drafts.services.preflight import PublicationPreflightService


KEY = Fernet.generate_key().decode()


@override_settings(MERCADO_LIBRE_ENCRYPTION_KEY=KEY, MERCADO_LIBRE_ENABLE_TEST_WRITES=False)
class CommercialUserProductsPreflightTests(TestCase):
    def setUp(self):
        self.connection = MercadoLibreConnection.objects.create(
            seller_id=178383685,
            nickname="BLISTERMARKET",
            access_token_encrypted=encrypt("token"),
            refresh_token_encrypted=encrypt("refresh"),
            token_expires_at=timezone.now() + timedelta(hours=1),
        )
        self.draft = ProductDraft.objects.create(
            title="Producto comercial de referencia",
            family_name="Producto comercial",
            domain_id="MLA-COMMERCIAL_PRODUCT",
            category_id="MLA123",
            listing_type_id="gold_special",
            price="15000",
            description="Descripción local",
            condition=ProductDraft.Condition.NEW,
            stock=2,
            edition_and_authenticity_verified=True,
            commercialization_authorization_confirmed=True,
            additional_attributes={
                "BRAND": {"value_id": "brand-1", "value_name": "Marca admitida"},
                "ITEM_CONDITION": {"value_id": "2230284", "value_name": "Nuevo"},
                "MATERIAL": {"value_id": "m-1", "value_name": "Acero"},
            },
        )
        self.metadata = Mock()
        self.metadata.seller_details.return_value = {
            "id": 178383685,
            "site_id": "MLA",
            "tags": ["normal", "user_product_seller"],
        }
        self.metadata.predict_categories.return_value = [{"category_id": "MLA123", "domain_id": "MLA-COMMERCIAL_PRODUCT"}]
        self.metadata.category_attributes.return_value = [
            {"id": "BRAND", "name": "Marca", "hierarchy": "PARENT_PK", "tags": {"required": True}, "values": [{"id": "brand-1", "name": "Marca admitida"}]},
            {"id": "ITEM_CONDITION", "name": "Condición del ítem", "hierarchy": "PARENT_PK", "tags": {"hidden": True}, "values": [{"id": "2230284", "name": "Nuevo"}, {"id": "2230582", "name": "Reacondicionado"}]},
            {"id": "COLOR", "name": "Color", "hierarchy": "CHILD_PK", "tags": {"required": True}, "values": [{"id": "red", "name": "Rojo"}]},
            {"id": "MATERIAL", "name": "Material", "tags": {"required": True}, "values": [{"id": "m-1", "name": "Acero"}]},
            {"id": "WARRANTY", "name": "Garantía", "tags": {"conditional_required": True}, "values": []},
        ]
        self.metadata.category_details.return_value = {"name": "Categoría comercial", "settings": {"max_pictures_per_item": 6}}
        self.metadata.technical_specs_input.return_value = {"groups": []}
        self.metadata.category_sale_terms.return_value = []
        self.metadata.listing_prices.return_value = []
        self.metadata.seller_shipping_preferences.return_value = {"modes": ["me2"], "local_pick_up": True}
        self.metadata.category_shipping_preferences.return_value = {"logistics": [{"mode": "me2"}]}
        self.metadata.available_listing_types.return_value = {"available": [{"id": "gold_special", "name": "Clásica", "remaining_listings": None}]}

    def service(self):
        return PublicationPreflightService(self.metadata, token_getter=lambda *_args, **_kwargs: "token")

    def test_commercial_user_products_preview_omits_title_and_separates_entities(self):
        self.metadata.http = Mock()
        report = self.service().run(self.draft)
        self.assertEqual(report.flow, "user_products")
        self.assertEqual(report.mode, "commercial")
        self.assertFalse(report.blockers)
        self.assertIn("family_name", report.payload_preview["user_product"])
        self.assertNotIn("title", report.payload_preview["user_product"])
        self.assertEqual(report.payload_preview["selling_condition"]["listing_type_id"], "gold_special")
        self.assertTrue(any(item["id"] == "BRAND" for item in report.family_attributes))
        self.assertTrue(any(item["id"] == "COLOR" for item in report.product_attributes))
        self.assertTrue(any("CHILD_PK" in item for item in report.pending))
        self.metadata.available_listing_types.assert_called_once_with(178383685, "MLA123", "token")
        self.metadata.http.post_json.assert_not_called()
        self.metadata.http.upload_file.assert_not_called()

    def test_missing_item_condition_and_unavailable_listing_type_block(self):
        self.draft.additional_attributes.pop("ITEM_CONDITION")
        self.draft.listing_type_id = "free"
        self.draft.save()
        report = self.service().run(self.draft)
        self.assertTrue(any("Condición del ítem" in item for item in report.blockers))
        self.assertTrue(any("no está disponible" in item for item in report.blockers))

    def test_refurbished_condition_requires_warranty_terms(self):
        self.draft.additional_attributes["ITEM_CONDITION"] = {"value_id": "2230582", "value_name": "Reacondicionado"}
        self.draft.save()
        report = self.service().run(self.draft)
        self.assertTrue(any("WARRANTY_TIME" in item for item in report.blockers))
        self.draft.sale_terms = [{"id": "WARRANTY_TYPE", "value_id": "factory"}, {"id": "WARRANTY_TIME", "value_name": "12 meses"}]
        self.draft.save()
        report = self.service().run(self.draft)
        self.assertFalse(any("WARRANTY_TIME" in item for item in report.blockers))

    def test_test_seller_keeps_test_mode_without_commercial_title_blocker(self):
        self.metadata.seller_details.return_value["tags"].append("test_user")
        report = self.service().run(self.draft)
        self.assertEqual(report.mode, "test")
        self.assertFalse(any("prefijo" in item.lower() for item in report.blockers))
        self.assertTrue(any("prefijo" in item.lower() for item in report.pending))

    def test_undetermined_flow_is_blocked_without_falling_back_to_legacy(self):
        self.metadata.seller_details.return_value["tags"] = ["normal"]
        report = self.service().run(self.draft)
        self.assertEqual(report.flow, "undetermined")
        self.assertTrue(any("no está determinado" in item for item in report.blockers))
        self.metadata.category_attributes.assert_not_called()

    def test_incomplete_domain_metadata_is_pending_not_invented(self):
        self.metadata.predict_categories.return_value = [{"category_id": "MLA123"}]
        report = self.service().run(self.draft)
        self.assertTrue(any("no informó domain_id" in item for item in report.pending))
        self.assertFalse(any("confirmado por el predictor" in item for item in report.verified))

    def test_technical_specs_and_sale_terms_are_read_only_metadata(self):
        self.metadata.technical_specs_input.return_value = {"groups": [{"components": [{"attributes": [{"id": "ALBUM_TYPE", "name": "Tipo de álbum", "tags": ["required"]}]}]}]}
        self.metadata.category_sale_terms.return_value = [{"id": "WARRANTY_TYPE", "name": "Tipo de garantía", "values": []}]
        report = self.service().run(self.draft)
        self.assertTrue(any("Tipo de álbum" in item for item in report.pending))
        self.assertEqual(report.selling_condition["available_sale_terms"][0]["id"], "WARRANTY_TYPE")
        self.metadata.http.post_json.assert_not_called()
