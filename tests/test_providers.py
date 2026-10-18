"""Official provider contract tests, using synthetic responses and no network."""
import io
import json
import os
import socket
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import enrich


class ProviderContracts(unittest.TestCase):
    def setUp(self):
        self.row = {
            "full_name": "Alex Example",
            "company_name": "Example Company",
            "company_domain": "example.test",
            "linkedin_url": "https://www.linkedin.com/in/alex-example-synthetic",
        }
        self.keys = patch.dict(os.environ, {
            "PROSPEO_API_KEY": "synthetic-prospeo-key",
            "GETPROSPECT_API_KEY": "synthetic-getprospect-key",
            "HUNTER_API_KEY": "synthetic-hunter-key",
        }, clear=True)
        self.keys.start()
        self.addCleanup(self.keys.stop)
        self.network = patch("enrich.urlopen", side_effect=AssertionError("Tests must never use the network"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def test_prospeo_documented_body_and_verified_contacts(self):
        response = {"error": False, "person": {
            "email": {"status": "VERIFIED", "revealed": True, "email": "alex@example.test"},
            "mobile": {"status": "VERIFIED", "revealed": True, "mobile": "+12025550123"},
        }}
        with patch("enrich.request", return_value=response) as request:
            contact = enrich.prospeo(self.row, True)
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "https://api.prospeo.io/enrich-person"))
        self.assertEqual(kwargs["headers"], {"X-KEY": "synthetic-prospeo-key"})
        self.assertEqual(kwargs["body"]["data"]["company_website"], "example.test")
        self.assertNotIn("company_domain", kwargs["body"]["data"])
        self.assertTrue(kwargs["body"]["only_verified_email"])
        self.assertTrue(kwargs["body"]["enrich_mobile"])
        self.assertEqual(contact.email, "alex@example.test")
        self.assertEqual(contact.phone, "+12025550123")
        self.assertEqual(contact.email_status, "provider_verified")
        self.assertEqual(contact.phone_status, "provider_verified")

    def test_prospeo_does_not_reveal_unrequested_phone(self):
        response = {"error": False, "person": {
            "mobile": {"status": "VERIFIED", "revealed": True, "mobile": "+12025550123"},
        }}
        with patch("enrich.request", return_value=response) as request:
            contact = enrich.prospeo(self.row, False)
        self.assertFalse(request.call_args.kwargs["body"]["enrich_mobile"])
        self.assertEqual(contact.phone, "")

    def test_prospeo_unrevealed_or_unverified_values_are_not_contacts(self):
        for status, revealed in (("VERIFIED", False), ("UNAVAILABLE", True)):
            with self.subTest(status=status, revealed=revealed):
                response = {"person": {
                    "email": {"status": status, "revealed": revealed, "email": "alex@example.test"},
                    "mobile": {"status": status, "revealed": revealed, "mobile": "+12025550123"},
                }}
                with patch("enrich.request", return_value=response):
                    contact = enrich.prospeo(self.row, True)
                self.assertEqual(contact.email, "")
                self.assertEqual(contact.phone, "")

    def test_getprospect_documented_body_and_long_timeout(self):
        response = {"success": True, "data": {
            "email": {"status": "valid", "revealed": True, "address": "alex@example.test"},
            "mobile_phone": {"status": "found", "revealed": True, "number": "+12025550123"},
        }}
        with patch("enrich.request", return_value=response) as request:
            contact = enrich.getprospect(self.row, True)
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "https://api.getprospect.com/v2/people/enrich"))
        self.assertEqual(kwargs["headers"], {"x-api-key": "synthetic-getprospect-key"})
        self.assertEqual(kwargs["body"]["data"]["company_domain"], "example.test")
        self.assertNotIn("company_website", kwargs["body"]["data"])
        self.assertTrue(kwargs["body"]["enrich_email"])
        self.assertTrue(kwargs["body"]["enrich_phone"])
        self.assertGreaterEqual(kwargs["timeout"], 95)
        self.assertEqual(contact.email, "alex@example.test")
        self.assertEqual(contact.phone, "+12025550123")
        self.assertEqual(contact.email_status, "provider_valid")
        self.assertEqual(contact.phone_status, "provider_found")

    def test_getprospect_documented_no_match_is_empty(self):
        response = {"success": False, "data": None, "errors": [{"name": "NOT_FOUND"}]}
        with patch("enrich.request", return_value=response):
            self.assertEqual(enrich.getprospect(self.row, False), enrich.Contact())

    def test_getprospect_other_rejections_remain_errors(self):
        response = {"success": False, "data": None, "errors": [
            {"name": "VALIDATION_ERROR", "message": "private provider message"},
        ]}
        with patch("enrich.request", return_value=response):
            with self.assertRaises(enrich.ProviderError) as error:
                enrich.getprospect(self.row, False)
        self.assertNotIn("private provider message", str(error.exception))

    def test_getprospect_unrevealed_contacts_are_not_used(self):
        response = {"success": True, "data": {
            "email": {"status": "valid", "revealed": False, "address": "alex@example.test"},
            "mobile_phone": {"status": "found", "revealed": False, "number": "+12025550123"},
        }}
        with patch("enrich.request", return_value=response):
            contact = enrich.getprospect(self.row, True)
        self.assertEqual(contact.email, "")
        self.assertEqual(contact.phone, "")

    def test_null_contact_objects_are_supported(self):
        cases = (
            (enrich.prospeo, {"person": {"email": None, "mobile": None}}),
            (enrich.getprospect, {"success": True, "data": {"email": None, "mobile_phone": None}}),
            (enrich.hunter, {"data": None}),
        )
        for provider, response in cases:
            with self.subTest(provider=provider.__name__), patch("enrich.request", return_value=response):
                contact = provider(self.row, True)
                self.assertEqual(contact.email, "")
                self.assertEqual(contact.phone, "")

    def test_masked_fields_are_never_used(self):
        cases = (
            (enrich.prospeo, {"person": {
                "email": {"status": "VERIFIED", "revealed": True, "email": "a***@example.test"},
                "mobile": {"status": "VERIFIED", "revealed": True, "mobile": "+1202***0123"},
            }}),
            (enrich.getprospect, {"success": True, "data": {
                "email": {"status": "valid", "revealed": True, "address": "a***@example.test"},
                "mobile_phone": {"status": "found", "revealed": True, "number": "+1202***0123"},
            }}),
            (enrich.hunter, {"data": {"email": "a***@example.test"}}),
        )
        for provider, response in cases:
            with self.subTest(provider=provider.__name__), patch("enrich.request", return_value=response):
                contact = provider(self.row, True)
                self.assertEqual(contact.email, "")
                self.assertEqual(contact.phone, "")

    def test_hunter_keeps_verification_distinctions(self):
        for status, expected in (
            ("valid", "provider_verified"), ("accept_all", "accept_all"),
            ("invalid", "invalid"), ("unknown", "unknown"),
        ):
            with self.subTest(status=status):
                response = {"data": {
                    "email": "alex@example.test", "verification": {"status": status},
                    "phone_number": "+12025550123",
                }}
                with patch("enrich.request", return_value=response):
                    contact = enrich.hunter(self.row, True)
                self.assertEqual(contact.email_status, expected)
                self.assertEqual(contact.phone, "")

    def test_hunter_missing_verification_is_unverified(self):
        with patch("enrich.request", return_value={"data": {"email": "alex@example.test"}}):
            self.assertEqual(enrich.hunter(self.row, False).email_status, "unverified")

    def test_hunter_documented_request(self):
        with patch("enrich.request", return_value={"data": None}) as request:
            enrich.hunter(self.row, False)
        args, kwargs = request.call_args
        self.assertEqual(args, ("GET", "https://api.hunter.io/v2/email-finder"))
        self.assertEqual(kwargs["params"], {
            "domain": "example.test", "first_name": "Alex", "last_name": "Example",
            "api_key": "synthetic-hunter-key",
        })

    def test_missing_keys_do_not_make_network_requests(self):
        with patch.dict(os.environ, {}, clear=True), patch("enrich.request") as request:
            for provider in (enrich.prospeo, enrich.getprospect, enrich.hunter):
                with self.subTest(provider=provider.__name__), self.assertRaises(enrich.ProviderError):
                    provider(self.row, False)
        request.assert_not_called()

    def test_insufficient_identity_does_not_make_requests(self):
        with patch("enrich.request") as request:
            for provider in (enrich.prospeo, enrich.getprospect, enrich.hunter):
                with self.subTest(provider=provider.__name__):
                    self.assertEqual(provider({"company_domain": "example.test"}, False), enrich.Contact())
        request.assert_not_called()


class TransportSafety(unittest.TestCase):
    url = "https://api.prospeo.io/enrich-person"

    @staticmethod
    def response(payload):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))

    def test_prospeo_http400_no_match_is_a_normal_empty_result(self):
        error = HTTPError(self.url, 400, "Bad Request", {}, self.response({
            "error": True, "error_code": "NO_MATCH", "message": "private contact payload",
        }))
        with patch.dict(os.environ, {"PROSPEO_API_KEY": "synthetic-key"}), patch("enrich.urlopen", side_effect=error) as network:
            self.assertEqual(enrich.prospeo({"email": "alex@example.test"}, False), enrich.Contact())
        network.assert_called_once()

    def test_legacy_getprospect_404_is_a_normal_empty_result(self):
        error = HTTPError("https://api.getprospect.com/v2/people/enrich", 404, "Not Found", {}, self.response({}))
        with patch.dict(os.environ, {"GETPROSPECT_API_KEY": "synthetic-key"}), patch("enrich.urlopen", side_effect=error):
            self.assertEqual(enrich.getprospect({"email": "alex@example.test"}, False), enrich.Contact())

    def test_http_errors_do_not_include_arbitrary_provider_fields(self):
        private = "synthetic-private-identifier-do-not-log"
        for payload in (
            {"error": True, "error_code": private, "message": private},
            {"error": True, "error_code": "INVALID_API_KEY", "email": private},
            {"errors": [{"name": private, "message": private}]},
        ):
            with self.subTest(payload=payload):
                error = HTTPError(self.url + "?api_key=" + private, 400, private, {}, self.response(payload))
                with patch("enrich.urlopen", side_effect=error), self.assertRaises(enrich.ProviderError) as raised:
                    enrich.request("POST", self.url)
                self.assertNotIn(private, str(raised.exception))

    def test_network_failures_are_redacted_and_never_retried(self):
        private = "synthetic-private-identifier-do-not-log"
        for failure in (URLError(private), socket.timeout(private)):
            with self.subTest(failure=type(failure).__name__):
                with patch("enrich.urlopen", side_effect=failure) as network:
                    with self.assertRaises(enrich.ProviderError) as raised:
                        enrich.request("POST", self.url)
                self.assertNotIn(private, str(raised.exception))
                network.assert_called_once()

    def test_invalid_json_is_reported_safely(self):
        with patch("enrich.urlopen", return_value=io.BytesIO(b"private contact response, not JSON")):
            with self.assertRaises(enrich.ProviderError) as raised:
                enrich.request("POST", self.url)
        self.assertNotIn("private contact response", str(raised.exception))

    def test_nonobject_json_is_rejected_at_transport_boundary(self):
        for payload in ([], None, "private contact payload"):
            with self.subTest(payload=payload), patch("enrich.urlopen", return_value=self.response(payload)):
                with self.assertRaises(enrich.ProviderError):
                    enrich.request("POST", self.url)

    def test_per_provider_timeout_is_passed_to_transport(self):
        with patch("enrich.urlopen", return_value=self.response({"data": None})) as network:
            enrich.request("POST", self.url, timeout=95)
        self.assertEqual(network.call_args.kwargs["timeout"], 95)


if __name__ == "__main__":
    unittest.main()
