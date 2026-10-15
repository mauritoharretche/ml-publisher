"""Proxy mínimo para exponer exclusivamente el callback OAuth mediante un túnel."""
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlencode, urlsplit


CALLBACK_PATH = "/mercadolibre/callback/"
LOCAL_CONNECTION_URL = "http://127.0.0.1:8000/mercadolibre/connection/"
ALLOWED_QUERY_PARAMETERS = {"code", "state", "error", "error_description", "error_uri"}
MAX_TARGET_LENGTH = 2048


class CallbackProxyRequestError(Exception):
    def __init__(self, status):
        self.status = status


def validated_callback_target(method, target):
    if method != "GET":
        raise CallbackProxyRequestError(405)
    if len(target) > MAX_TARGET_LENGTH:
        raise CallbackProxyRequestError(414)
    parsed = urlsplit(target)
    if parsed.path != CALLBACK_PATH:
        raise CallbackProxyRequestError(404)
    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True, max_num_fields=len(ALLOWED_QUERY_PARAMETERS))
    except ValueError as error:
        raise CallbackProxyRequestError(400) from error
    names = [name for name, _value in pairs]
    if len(names) != len(set(names)) or any(name not in ALLOWED_QUERY_PARAMETERS for name in names):
        raise CallbackProxyRequestError(400)
    return CALLBACK_PATH + ("?" + urlencode(pairs) if pairs else "")


class CallbackProxyHandler(BaseHTTPRequestHandler):
    server_version = "MLOAuthCallbackProxy"

    def log_message(self, _format, *_args):
        # Nunca registrar URLs, códigos, cookies ni cabeceras OAuth.
        return

    def do_GET(self):
        self._forward()

    def do_POST(self):
        self._reject_method()

    def do_PUT(self):
        self._reject_method()

    def do_PATCH(self):
        self._reject_method()

    def do_DELETE(self):
        self._reject_method()

    def do_HEAD(self):
        self._reject_method()

    def do_OPTIONS(self):
        self._reject_method()

    def do_TRACE(self):
        self._reject_method()

    def do_CONNECT(self):
        self._reject_method()

    def _reject_method(self):
        self.send_error(405, "Método no permitido")

    def _forward(self):
        try:
            target = validated_callback_target(self.command, self.path)
        except CallbackProxyRequestError as error:
            self.send_error(error.status, "Solicitud de callback no válida")
            return
        try:
            connection = HTTPConnection("127.0.0.1", 8000, timeout=5)
            connection.request("GET", target, headers={"Host": "127.0.0.1", "Accept": "text/html"})
            response = connection.getresponse()
            status = response.status
            response.read()
            connection.close()
        except OSError:
            self.send_error(502, "No fue posible validar el callback")
            return
        if status in {301, 302, 303, 307, 308}:
            self.send_response(302)
            self.send_header("Location", LOCAL_CONNECTION_URL)
            self.end_headers()
            return
        if status == 400:
            self.send_error(400, "Callback OAuth rechazado")
            return
        self.send_error(502, "No fue posible validar el callback")


def serve(host="127.0.0.1", port=8765):
    ThreadingHTTPServer((host, port), CallbackProxyHandler).serve_forever()
