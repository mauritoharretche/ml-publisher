from io import BytesIO
from urllib.error import HTTPError, URLError
from django.test import SimpleTestCase
from drafts.services.exceptions import MercadoLibreAuthorizationError, MercadoLibreNetworkError, MercadoLibreRateLimitError
from drafts.services.mercado_libre_client import MercadoLibreHttpClient


class HttpErrorTests(SimpleTestCase):
    def test_rate_limit_is_distinguished(self):
        client = MercadoLibreHttpClient(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPError("url", 429, "rate", {}, BytesIO())))
        with self.assertRaises(MercadoLibreRateLimitError):
            client.get("/users/me", access_token="x")

    def test_auth_and_network_errors_are_safe(self):
        auth = MercadoLibreHttpClient(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(HTTPError("url", 403, "denied", {}, BytesIO())))
        with self.assertRaises(MercadoLibreAuthorizationError):
            auth.get("/users/me", access_token="x")
        network = MercadoLibreHttpClient(opener=lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("offline")))
        with self.assertRaises(MercadoLibreNetworkError):
            network.get("/users/me", access_token="x")
