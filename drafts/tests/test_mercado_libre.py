from datetime import timedelta
from unittest.mock import Mock, patch
from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from drafts.models import MercadoLibreConnection, MercadoLibreOAuthTransaction
from drafts.services.connections import access_token
from drafts.services.credentials import decrypt, encrypt
from drafts.services.exceptions import MercadoLibreAuthorizationError
from drafts.services.mercado_libre_client import MercadoLibreMetadataService, TokenData
from drafts.services.oauth_transactions import create_transaction

KEY = Fernet.generate_key().decode()


@override_settings(MERCADO_LIBRE_ENCRYPTION_KEY=KEY, MERCADO_LIBRE_CLIENT_ID="123", MERCADO_LIBRE_CLIENT_SECRET="secret", MERCADO_LIBRE_REDIRECT_URI="https://testserver/mercadolibre/callback/")
class OAuthSecurityTests(TestCase):
    @override_settings(MERCADO_LIBRE_CLIENT_ID="", MERCADO_LIBRE_CLIENT_SECRET="", MERCADO_LIBRE_REDIRECT_URI="", MERCADO_LIBRE_ENCRYPTION_KEY="")
    def test_unconfigured_oauth_is_visible_and_does_not_redirect_externally(self):
        response = self.client.get(reverse("drafts:connection"))
        self.assertContains(response, "OAuth está deshabilitado")
        self.assertNotContains(response, reverse("drafts:connect"))

        response = self.client.get(reverse("drafts:connect"), follow=True)
        self.assertRedirects(response, reverse("drafts:connection"))
        self.assertContains(response, "OAuth no está configurado")

    @override_settings(MERCADO_LIBRE_REDIRECT_URI="http://localhost:8000/mercadolibre/callback/")
    def test_http_callback_is_rejected_before_authorization_redirect(self):
        response = self.client.get(reverse("drafts:connect"), follow=True)
        self.assertRedirects(response, reverse("drafts:connection"))
        self.assertContains(response, "URL de callback HTTPS válida")

    def test_credentials_are_encrypted_and_round_trip(self):
        encrypted = encrypt("access-token-value")
        self.assertNotIn("access-token-value", encrypted)
        self.assertEqual(decrypt(encrypted), "access-token-value")

    def test_callback_rejects_missing_or_unknown_state_without_session(self):
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "code", "state": "wrong"})
        self.assertEqual(response.status_code, 400)

    def test_callback_rejects_expired_or_reused_persisted_state(self):
        expired = create_transaction("expired", "verifier")
        expired.expires_at = timezone.now() - timedelta(seconds=1)
        expired.save(update_fields=["expires_at"])
        self.assertEqual(self.client.get(reverse("drafts:oauth_callback"), {"code": "code", "state": "expired"}).status_code, 400)

        used = create_transaction("used", "verifier")
        used.consumed_at = timezone.now()
        used.save(update_fields=["consumed_at"])
        self.assertEqual(self.client.get(reverse("drafts:oauth_callback"), {"code": "code", "state": "used"}).status_code, 400)

    @patch("drafts.views.MercadoLibreOAuthService")
    def test_callback_exchanges_code_without_persisting_plaintext(self, oauth_class):
        oauth = oauth_class.return_value
        scopes = "read " * 100
        oauth.exchange_code.return_value = TokenData("access", "refresh", timezone.now() + timedelta(hours=1), 77, scopes)
        oauth.seller_profile.return_value = {"nickname": "seller"}
        transaction = create_transaction("expected", "verifier")
        self.assertNotEqual(transaction.state_hash, "expected")
        self.assertNotEqual(transaction.code_verifier_encrypted, "verifier")
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "temporary-code", "state": "expected"})
        self.assertRedirects(response, reverse("drafts:connection"))
        connection = MercadoLibreConnection.objects.get(seller_id=77)
        self.assertNotEqual(connection.access_token_encrypted, "access")
        self.assertEqual(decrypt(connection.refresh_token_encrypted), "refresh")
        self.assertEqual(connection.scopes, scopes)
        transaction.refresh_from_db()
        self.assertIsNotNone(transaction.consumed_at)

    @patch("drafts.views.MercadoLibreOAuthService")
    def test_failed_exchange_consumes_transaction_and_cannot_be_replayed(self, oauth_class):
        oauth_class.return_value.exchange_code.side_effect = MercadoLibreAuthorizationError("Error seguro")
        create_transaction("one-time", "verifier")
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "temporary-code", "state": "one-time"})
        self.assertRedirects(response, reverse("drafts:connection"))
        response = self.client.get(reverse("drafts:oauth_callback"), {"code": "temporary-code", "state": "one-time"})
        self.assertEqual(response.status_code, 400)

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
