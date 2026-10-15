import json
import mimetypes
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
from django.conf import settings
from django.utils import timezone
from .exceptions import MercadoLibreAuthorizationError, MercadoLibreConfigurationError, MercadoLibreNetworkError, MercadoLibreRateLimitError

AUTHORIZATION_URL = "https://auth.mercadolibre.com.ar/authorization"
API_URL = "https://api.mercadolibre.com"


@dataclass(frozen=True)
class TokenData:
    access_token: str
    refresh_token: str
    expires_at: object
    seller_id: int
    scope: str


class MercadoLibreHttpClient:
    """Único lugar con HTTP; no registra bodies, headers ni credenciales."""
    def __init__(self, opener=urlopen):
        self.opener = opener

    def _request(self, url, *, method="GET", data=None, access_token=None):
        headers = {"Accept": "application/json"}
        if data is not None:
            data = urlencode(data).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=settings.MERCADO_LIBRE_TIMEOUT) as response:
                return json.loads(response.read().decode())
        except HTTPError as error:
            if error.code in (400, 401, 403):
                raise MercadoLibreAuthorizationError("Mercado Libre rechazó la autorización. Volvé a conectar la cuenta.") from error
            if error.code == 429:
                raise MercadoLibreRateLimitError("Mercado Libre limitó temporalmente las consultas. Esperá e intentá nuevamente.") from error
            raise MercadoLibreNetworkError("Mercado Libre no pudo procesar la solicitud.") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise MercadoLibreNetworkError("No fue posible comunicarse con Mercado Libre. Intentá nuevamente.") from error

    def post_token(self, payload):
        return self._request(f"{API_URL}/oauth/token", method="POST", data=payload)

    def get(self, path, *, params=None, access_token):
        url = f"{API_URL}{path}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._request(url, access_token=access_token)

    def post_json(self, path, payload, *, access_token):
        request = Request(f"{API_URL}{path}", data=json.dumps(payload).encode(), headers={"Accept": "application/json", "Content-Type": "application/json", "Authorization": f"Bearer {access_token}"}, method="POST")
        return self._open_json(request)

    def upload_file(self, path, file_field, *, access_token):
        boundary = uuid.uuid4().hex
        content = file_field.read()
        filename = getattr(file_field, "name", "image.jpg").split("/")[-1]
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body = b"--" + boundary.encode() + b"\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" + filename.encode() + b"\r\nContent-Type: " + content_type.encode() + b"\r\n\r\n" + content + b"\r\n--" + boundary.encode() + b"--\r\n"
        request = Request(f"{API_URL}{path}", data=body, headers={"Accept": "application/json", "Content-Type": f"multipart/form-data; boundary={boundary}", "Authorization": f"Bearer {access_token}"}, method="POST")
        return self._open_json(request)

    def _open_json(self, request):
        try:
            with self.opener(request, timeout=settings.MERCADO_LIBRE_TIMEOUT) as response:
                return json.loads(response.read().decode())
        except HTTPError as error:
            if error.code in (400, 401, 403):
                raise MercadoLibreAuthorizationError("Mercado Libre rechazó la operación autorizada.") from error
            if error.code == 429:
                raise MercadoLibreRateLimitError("Mercado Libre limitó temporalmente las operaciones.") from error
            raise MercadoLibreNetworkError("Mercado Libre no pudo procesar la operación.") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise MercadoLibreNetworkError("No fue posible comunicar la operación a Mercado Libre.") from error


class MercadoLibreOAuthService:
    def __init__(self, http_client=None):
        self.http = http_client or MercadoLibreHttpClient()

    def _configuration(self):
        if self.missing_oauth_configuration():
            raise MercadoLibreConfigurationError("Falta configurar Client ID, Client Secret o URL de callback.")

    @staticmethod
    def missing_oauth_configuration():
        required = {
            "Client ID": settings.MERCADO_LIBRE_CLIENT_ID,
            "Client Secret": settings.MERCADO_LIBRE_CLIENT_SECRET,
            "URL de callback": settings.MERCADO_LIBRE_REDIRECT_URI,
        }
        missing = [label for label, value in required.items() if not value]
        callback = settings.MERCADO_LIBRE_REDIRECT_URI
        parsed = urlparse(callback) if callback else None
        if callback and (parsed.scheme != "https" or not parsed.netloc):
            missing.append("URL de callback HTTPS válida")
        return missing

    @staticmethod
    def missing_connection_configuration():
        """Variables necesarias antes de iniciar un flujo que guardará tokens."""
        missing = MercadoLibreOAuthService.missing_oauth_configuration()
        if not settings.MERCADO_LIBRE_ENCRYPTION_KEY:
            missing.append("clave de cifrado")
        return missing

    def connection_is_configured(self):
        return not self.missing_connection_configuration()

    def authorization_url(self, state, code_challenge):
        self._configuration()
        query = urlencode({"response_type": "code", "client_id": settings.MERCADO_LIBRE_CLIENT_ID, "redirect_uri": settings.MERCADO_LIBRE_REDIRECT_URI, "state": state, "code_challenge": code_challenge, "code_challenge_method": "S256"})
        return f"{AUTHORIZATION_URL}?{query}"

    def _to_token_data(self, payload):
        try:
            return TokenData(access_token=payload["access_token"], refresh_token=payload["refresh_token"], expires_at=timezone.now() + timedelta(seconds=int(payload["expires_in"])), seller_id=int(payload["user_id"]), scope=payload.get("scope", ""))
        except (KeyError, ValueError, TypeError) as error:
            raise MercadoLibreAuthorizationError("La respuesta de autorización de Mercado Libre es inválida.") from error

    def exchange_code(self, code, code_verifier):
        self._configuration()
        return self._to_token_data(self.http.post_token({"grant_type": "authorization_code", "client_id": settings.MERCADO_LIBRE_CLIENT_ID, "client_secret": settings.MERCADO_LIBRE_CLIENT_SECRET, "code": code, "redirect_uri": settings.MERCADO_LIBRE_REDIRECT_URI, "code_verifier": code_verifier}))

    def refresh(self, refresh_token):
        self._configuration()
        return self._to_token_data(self.http.post_token({"grant_type": "refresh_token", "client_id": settings.MERCADO_LIBRE_CLIENT_ID, "client_secret": settings.MERCADO_LIBRE_CLIENT_SECRET, "refresh_token": refresh_token}))

    def seller_profile(self, access_token):
        return self.http.get("/users/me", access_token=access_token)


class MercadoLibreMetadataService:
    def __init__(self, http_client=None):
        self.http = http_client or MercadoLibreHttpClient()

    def predict_categories(self, title, access_token):
        result = self.http.get(f"/sites/{settings.MERCADO_LIBRE_SITE_ID}/domain_discovery/search", params={"q": title, "limit": 3}, access_token=access_token)
        return [{key: item.get(key, "") for key in ("domain_id", "domain_name", "category_id", "category_name", "attributes")} for item in result]

    def category_attributes(self, category_id, access_token):
        return self.http.get(f"/categories/{category_id}/attributes", access_token=access_token)

    def category_details(self, category_id, access_token):
        return self.http.get(f"/categories/{category_id}", access_token=access_token)

    def seller_details(self, seller_id, access_token):
        return self.http.get(f"/users/{seller_id}", access_token=access_token)

    def available_listing_types(self, seller_id, category_id, access_token):
        return self.http.get(f"/users/{seller_id}/available_listing_types", params={"category_id": category_id}, access_token=access_token)
