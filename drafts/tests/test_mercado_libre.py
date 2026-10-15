from datetime import timedelta
from unittest.mock import Mock, patch
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from drafts.models import MercadoLibreConnection
from drafts.services.connections import access_token
from drafts.services.credentials import decrypt, encrypt
from drafts.services.mercado_libre_client import MercadoLibreMetadataService, TokenData

KEY = Fernet.generate_key().decode()


@override_settings(MERCADO_LIBRE_ENCRYPTION_KEY=KEY, MERCADO_LIBRE_CLIENT_ID="123", MERCADO_LIBRE_CLIENT_SECRET="secret", MERCADO_LIBRE_REDIRECT_URI="http://testserver/mercadolibre/callback/")
class OAuthSecurityTests(TestCase):
    def test_credentials_are_encrypted_and_round_trip(self):
        encrypted = encrypt("access-token-value")
        self.assertNotIn("access-token-value", encrypted)
        self.assertEqual(decrypt(encrypted), "access-token-value")

    def test_callback_rejects_missing_or_wrong_state(self):
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "code", "state": "wrong"})
        self.assertEqual(response.status_code, 400)
        session = self.client.session
        session["ml_oauth_state"] = "expected"
        session["ml_oauth_verifier"] = "verifier"
        session["ml_oauth_started_at"] = timezone.now().timestamp()
        session.save()
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "code", "state": "wrong"})
        self.assertEqual(response.status_code, 400)

    @patch("drafts.views.MercadoLibreOAuthService")
    def test_callback_exchanges_code_without_persisting_plaintext(self, oauth_class):
        oauth = oauth_class.return_value
        oauth.exchange_code.return_value = TokenData("access", "refresh", timezone.now() + timedelta(hours=1), 77, "read")
        oauth.seller_profile.return_value = {"nickname": "seller"}
        session = self.client.session
        session["ml_oauth_state"] = "expected"
        session["ml_oauth_verifier"] = "verifier"
        session["ml_oauth_started_at"] = timezone.now().timestamp()
        session.save()
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "temporary-code", "state": "expected"})
        self.assertRedirects(response, reverse("drafts:connection"))
        connection = MercadoLibreConnection.objects.get(seller_id=77)
        self.assertNotEqual(connection.access_token_encrypted, "access")
        self.assertEqual(decrypt(connection.refresh_token_encrypted), "refresh")

    @patch("drafts.services.connections.MercadoLibreOAuthService")
    def test_expired_connection_refreshes_and_rotates_refresh_token(self, oauth_class):
        connection = MercadoLibreConnection.objects.create(seller_id=3, access_token_encrypted=encrypt("old-access"), refresh_token_encrypted=encrypt("old-refresh"), token_expires_at=timezone.now(), scopes="read")
        oauth_class.return_value.refresh.return_value = TokenData("new-access", "new-refresh", timezone.now() + timedelta(hours=1), 3, "read")
        self.assertEqual(access_token(connection), "new-access")
        connection.refresh_from_db()
        self.assertEqual(decrypt(connection.refresh_token_encrypted), "new-refresh")


class MetadataServiceTests(TestCase):
    def test_prediction_and_attributes_use_documented_read_endpoints(self):
        http = Mock()
        http.get.side_effect = [[{"category_id": "MLA1", "category_name": "Prueba", "domain_id": "DOMAIN", "domain_name": "Dominio", "attributes": []}], [{"id": "BRAND", "tags": {"required": True}}]]
        service = MercadoLibreMetadataService(http)
        self.assertEqual(service.predict_categories("producto", "token")[0]["category_id"], "MLA1")
        self.assertEqual(service.category_attributes("MLA1", "token")[0]["id"], "BRAND")
        self.assertEqual(http.get.call_count, 2)

    @patch("drafts.views.MercadoLibreMetadataService")
    @patch("drafts.views.access_token", return_value="token")
    def test_category_suggestions_render_from_mocked_api(self, _token, service_class):
        service_class.return_value.predict_categories.return_value = [{"category_id": "MLA1", "category_name": "Prueba"}]
        response = self.client.get(reverse("drafts:category_suggestions"), {"title": "producto"})
        self.assertContains(response, "MLA1")
