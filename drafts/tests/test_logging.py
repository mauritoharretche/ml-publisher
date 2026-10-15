import logging
from django.test import SimpleTestCase
from config.logging import OAuthCallbackRedactionFilter


class OAuthCallbackRedactionFilterTests(SimpleTestCase):
    def test_callback_query_is_redacted_from_server_log_arguments(self):
        record = logging.LogRecord("django.server", logging.INFO, "", 0, '"%s %s %s"', ("GET", "/mercadolibre/callback/?code=temporary&state=temporary", "400"), None)
        OAuthCallbackRedactionFilter().filter(record)
        self.assertNotIn("temporary", record.args[1])
        self.assertEqual(record.args[1], "/mercadolibre/callback/?[REDACTED]")
