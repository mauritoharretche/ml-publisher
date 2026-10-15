from django.test import SimpleTestCase
from drafts.services.oauth_callback_proxy import CALLBACK_PATH, CallbackProxyRequestError, validated_callback_target


class CallbackProxyRoutingTests(SimpleTestCase):
    def test_allows_only_the_exact_get_callback_and_documented_parameters(self):
        self.assertEqual(validated_callback_target("GET", CALLBACK_PATH + "?code=x&state=y"), CALLBACK_PATH + "?code=x&state=y")

    def test_rejects_other_routes_and_methods(self):
        with self.assertRaisesRegex(CallbackProxyRequestError, "") as route_error:
            validated_callback_target("GET", "/admin/")
        self.assertEqual(route_error.exception.status, 404)
        with self.assertRaisesRegex(CallbackProxyRequestError, "") as method_error:
            validated_callback_target("POST", CALLBACK_PATH)
        self.assertEqual(method_error.exception.status, 405)

    def test_rejects_oversized_callback_target(self):
        with self.assertRaisesRegex(CallbackProxyRequestError, "") as error:
            validated_callback_target("GET", CALLBACK_PATH + "?state=" + "a" * 2048)
        self.assertEqual(error.exception.status, 414)

    def test_rejects_unexpected_or_duplicate_parameters(self):
        for query in ("state=a&state=b", "state=a&next=https://example.invalid"):
            with self.assertRaisesRegex(CallbackProxyRequestError, "") as error:
                validated_callback_target("GET", CALLBACK_PATH + "?" + query)
            self.assertEqual(error.exception.status, 400)
