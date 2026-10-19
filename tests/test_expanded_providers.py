"""Official API adapter contracts, exercised with synthetic data and mocks only."""
import os
import unittest
from unittest.mock import Mock, patch

import expanded_providers as api
from provider_types import Contact, ProviderError


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.row = {"full_name": "Synthetic Example", "company_domain": "example.test", "company_name": "Example"}
        keys = {key: "synthetic-secret" for key in (
            "TOMBA_API_KEY", "TOMBA_SECRET_KEY", "APOLLO_API_KEY", "DROPCONTACT_API_KEY",
            "SNOV_CLIENT_ID", "SNOV_CLIENT_SECRET", "ABSTRACT_EMAIL_API_KEY", "ZEROBOUNCE_API_KEY", "EMAILABLE_API_KEY")}
        environment = patch.dict(os.environ, keys, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def test_tomba_official_request_and_normalization(self):
        request = Mock(return_value={"data": {"email": " FOUND@EXAMPLE.TEST ", "verification": {"status": "valid"}}})
        result = api.tomba(self.row, request=request)
        request.assert_called_once_with("GET", "https://api.tomba.io/v1/email-finder",
                                        params={"domain": "example.test", "first_name": "Synthetic", "last_name": "Example"},
                                        headers={"X-Tomba-Key": "synthetic-secret", "X-Tomba-Secret": "synthetic-secret"})
        self.assertEqual(result, Contact(email="found@example.test", email_status="provider_valid"))

    def test_unknown_discovery_status_is_never_persisted(self):
        for finder, payload in ((api.tomba, {"data": {"email": "found@example.test", "verification": {"status": "private-value"}}}),
                                (api.apollo, {"person": {"email": "found@example.test", "email_status": "private-value"}})):
            with self.subTest(finder=finder):
                result = finder(self.row, request=Mock(return_value=payload))
                self.assertEqual(result.email_status, "provider_found")

    def test_masked_and_placeholder_emails_are_not_contacts(self):
        for email in ("f***@example.test", "f\u2022\u2022@example.test", "f\u2026@example.test", "email_not_unlocked@domain.com", "not-an-email"):
            with self.subTest(email=email):
                self.assertFalse(api.apollo(self.row, request=Mock(return_value={"person": {"email": email}})).email)
                self.assertFalse(api.tomba(self.row, request=Mock(return_value={"data": {"email": email}})).email)

    def test_apollo_never_requests_phone_personal_or_waterfall(self):
        request = Mock(return_value={"person": {"email": "found@example.test", "email_status": "verified"}})
        result = api.apollo(dict(self.row, linkedin_url="https://linkedin.com/in/synthetic"), request=request)
        args, kwargs = request.call_args
        self.assertEqual(args, ("POST", "https://api.apollo.io/api/v1/people/match"))
        self.assertEqual(kwargs["headers"], {"x-api-key": "synthetic-secret"})
        body = kwargs["body"]
        self.assertEqual(body["first_name"], "Synthetic")
        self.assertEqual(body["domain"], "example.test")
        self.assertEqual(body["organization_name"], "Example")
        self.assertEqual(body["linkedin_url"], "https://linkedin.com/in/synthetic")
        for name in ("reveal_personal_emails", "reveal_phone_number", "run_waterfall_email", "run_waterfall_phone"):
            self.assertIs(body[name], False)
        self.assertEqual(result.email_status, "provider_verified")

    def test_nonphone_finders_reject_phone_before_network(self):
        for finder in (api.tomba, api.apollo, api.dropcontact, api.snov):
            request = Mock()
            with self.subTest(finder=finder), self.assertRaises(ProviderError):
                finder(self.row, True, request=request)
            request.assert_not_called()

    def test_missing_identifiers_do_not_authenticate_or_submit(self):
        for finder in (api.tomba, api.apollo, api.dropcontact, api.snov):
            request = Mock()
            with self.subTest(finder=finder):
                self.assertEqual(finder({}, request=request), Contact())
            request.assert_not_called()

    def test_dropcontact_submits_exactly_once_and_returns_pending(self):
        request = Mock(return_value={"success": True, "request_id": "synthetic_job-1"})
        result = api.dropcontact(self.row, request=request)
        request.assert_called_once_with("POST", "https://api.dropcontact.com/v1/enrich/all",
                                        headers={"X-Access-Token": "synthetic-secret"},
                                        body={"data": [{"first_name": "Synthetic", "last_name": "Example", "website": "example.test", "company": "Example"}], "language": "en"})
        self.assertEqual(result.pending_task, "dropcontact:synthetic_job-1")

    def test_dropcontact_email_only_input_can_start(self):
        request = Mock(return_value={"request_id": "job"})
        self.assertEqual(api.dropcontact({"email": "synthetic@example.test"}, request=request).pending_task, "dropcontact:job")
        self.assertEqual(request.call_args.kwargs["body"]["data"], [{"email": "synthetic@example.test"}])

    def test_snov_auth_and_submit_are_only_two_calls(self):
        request = Mock(side_effect=[{"access_token": "synthetic-token"}, {"data": {"task_hash": "synthetic_hash"}}])
        result = api.snov(self.row, request=request)
        self.assertEqual(request.call_count, 2)
        first, second = request.call_args_list
        self.assertEqual(first.args, ("POST", "https://api.snov.io/v1/oauth/access_token"))
        self.assertEqual(first.kwargs["form"], {"grant_type": "client_credentials", "client_id": "synthetic-secret", "client_secret": "synthetic-secret"})
        self.assertEqual(second.args, ("POST", "https://api.snov.io/v2/emails-by-domain-by-name/start"))
        self.assertEqual(second.kwargs["headers"], {"Authorization": "Bearer synthetic-token"})
        self.assertEqual(second.kwargs["body"], {"rows": [{"first_name": "Synthetic", "last_name": "Example", "domain": "example.test"}]})
        self.assertEqual(result.pending_task, "snov:synthetic_hash")

    def test_snov_invalid_auth_never_starts(self):
        for response in ({}, {"access_token": ""}, {"access_token": "token\r\nunsafe"}, {"error": "private"}):
            request = Mock(return_value=response)
            with self.subTest(response=response), self.assertRaises(ProviderError):
                api.snov(self.row, request=request)
            self.assertEqual(request.call_count, 1)

    def test_untrusted_task_ids_are_rejected_before_retrieval(self):
        for value in ("", "../secret", "a/b", "a?key=private", "a#b", "a%2Fb", "a:b", "é", "a\n", "a" * 201):
            for provider in ("dropcontact", "snov"):
                request = Mock()
                with self.subTest(value=value, provider=provider), self.assertRaises(ProviderError):
                    api.read_pending(provider + ":" + value, request=request)
                request.assert_not_called()

    def test_unknown_pending_provider_is_rejected_before_auth(self):
        request = Mock()
        for task in ("hunter:job", "unknown:job", "missing-separator", "dropcontact:job:other"):
            with self.subTest(task=task), self.assertRaises(ProviderError):
                api.read_pending(task, request=request)
        request.assert_not_called()

    def test_start_rejects_untrusted_returned_job_ids(self):
        for value in (None, 123, "a/b", "a?private=1", "a" * 201):
            with self.subTest(value=value):
                with self.assertRaises(ProviderError):
                    api.dropcontact(self.row, request=Mock(return_value={"request_id": value}))
                with self.assertRaises(ProviderError):
                    api.snov(self.row, request=Mock(side_effect=[{"access_token": "token"}, {"data": {"task_hash": value}}]))

    def test_dropcontact_resume_gets_once_never_restarts(self):
        request = Mock(return_value={"success": True, "data": [{"email": [
            {"email": "generic@example.test", "qualification": "generic@pro"},
            {"email": "MASK***@example.test", "qualification": "nominative@pro"},
            {"email": "Found@Example.Test", "qualification": "nominative@pro"}]}]})
        result = api.read_pending("dropcontact:synthetic_job", request=request)
        request.assert_called_once_with("GET", "https://api.dropcontact.com/v1/enrich/all/synthetic_job", headers={"X-Access-Token": "synthetic-secret"})
        self.assertEqual(result, Contact(email="found@example.test", email_status="provider_found"))

    def test_dropcontact_resume_pending_and_no_match(self):
        self.assertEqual(api.read_pending("dropcontact:job", request=Mock(return_value={"success": False})).pending_task, "dropcontact:job")
        self.assertEqual(api.read_pending("dropcontact:job", request=Mock(return_value={"success": True, "data": []})), Contact())

    def test_dropcontact_unknown_qualification_does_not_claim_verified(self):
        result = api.read_pending("dropcontact:job", request=Mock(return_value={"data": [{"email": [{"email": "found@example.test", "qualification": "private-status"}]}]}))
        self.assertEqual(result, Contact())

    def test_dropcontact_malformed_qualification_is_a_safe_error(self):
        for value in ([], {"private": "provider-field"}):
            request = Mock(return_value={"data": [{"email": [{"email": "found@example.test", "qualification": value}]}]})
            with self.subTest(value=value), self.assertRaises(ProviderError) as failure:
                api.read_pending("dropcontact:job", request=request)
            request.assert_called_once()
            self.assertNotIn("provider-field", str(failure.exception))

    def test_snov_resume_auth_and_result_only(self):
        request = Mock(side_effect=[{"access_token": "token"}, {"status": "completed", "data": [{"result": [
            {"email": "invalid@example.test", "smtp_status": "invalid", "is_valid_format": True},
            {"email": "found@example.test", "smtp_status": "valid", "is_valid_format": True, "is_disposable": False}]}]}])
        result = api.read_pending("snov:synthetic_job", request=request)
        self.assertEqual(request.call_count, 2)
        call = request.call_args_list[1]
        self.assertEqual(call.args, ("GET", "https://api.snov.io/v2/emails-by-domain-by-name/result"))
        self.assertEqual(call.kwargs, {"headers": {"Authorization": "Bearer token"}, "params": {"task_hash": "synthetic_job"}})
        self.assertEqual(result, Contact(email="found@example.test", email_status="provider_smtp_valid"))

    def test_snov_disposable_or_unverified_results_are_not_contacts(self):
        for choice in ({"email": "found@example.test", "smtp_status": "valid"},
                       {"email": "found@example.test", "smtp_status": "valid", "is_valid_format": True, "is_disposable": True},
                       {"email": "masked***@example.test", "smtp_status": "valid", "is_valid_format": True}):
            request = Mock(side_effect=[{"access_token": "token"}, {"status": "completed", "data": [{"result": [choice]}]}])
            with self.subTest(choice=choice):
                self.assertEqual(api.read_pending("snov:job", request=request), Contact())

    def test_snov_pending_and_unknown_status(self):
        for status in ("in_progress", "in progress", "pending", "processing", "queued"):
            result = api.read_pending("snov:job", request=Mock(side_effect=[{"access_token": "token"}, {"status": status}]))
            self.assertEqual(result.pending_task, "snov:job")
        with self.assertRaises(ProviderError):
            api.read_pending("snov:job", request=Mock(side_effect=[{"access_token": "token"}, {"status": "private-value"}]))

    def test_snov_malformed_status_is_a_safe_error_without_retry(self):
        for value in ([], {"private": "provider-field"}, None):
            request = Mock(side_effect=[{"access_token": "token"}, {"status": value}])
            with self.subTest(value=value), self.assertRaises(ProviderError) as failure:
                api.read_pending("snov:job", request=request)
            self.assertEqual(request.call_count, 2)
            self.assertNotIn("provider-field", str(failure.exception))

    def test_abstract_payload_and_safe_status(self):
        request = Mock(return_value={"deliverability": "DELIVERABLE"})
        self.assertEqual(api.verify_abstract(" Synthetic@Example.Test ", request=request), "deliverable")
        request.assert_called_once_with("GET", "https://emailvalidation.abstractapi.com/v1/", params={"api_key": "synthetic-secret", "email": "synthetic@example.test"})
        for payload, expected in (({"is_valid_format": False}, "invalid_format"),
                                  ({"is_valid_format": {"value": False}}, "invalid_format"),
                                  ({"is_disposable_email": {"value": True}}, "disposable"),
                                  ({"deliverability": "private-status"}, "unknown")):
            self.assertEqual(api.verify_abstract("synthetic@example.test", request=Mock(return_value=payload)), expected)

    def test_abstract_boolean_disposable_cannot_be_marked_deliverable(self):
        result = api.verify_abstract("synthetic@example.test", request=Mock(return_value={
            "is_valid_format": True, "is_disposable_email": True, "deliverability": "DELIVERABLE"}))
        self.assertEqual(result, "disposable")

    def test_legacy_hunter_malformed_verification_status_is_unverified(self):
        import enrich
        for status in ([], {"private": "provider-field"}):
            with self.subTest(status=status), patch.dict(os.environ, {"HUNTER_API_KEY": "synthetic-key"}), patch("enrich.request", return_value={
                    "data": {"email": "found@example.test", "verification": {"status": status}}}):
                result = enrich.hunter(self.row, False)
            self.assertEqual(result.email_status, "unverified")

    def test_zerobounce_payload_and_allowlist(self):
        request = Mock(return_value={"status": "valid"})
        self.assertEqual(api.verify_zerobounce("synthetic@example.test", request=request), "valid")
        request.assert_called_once_with("POST", "https://api.zerobounce.net/v2/validate", form={"api_key": "synthetic-secret", "email": "synthetic@example.test", "timeout": "15"})
        for status in ("valid", "invalid", "catch-all", "unknown", "spamtrap", "abuse", "do_not_mail"):
            self.assertEqual(api.verify_zerobounce("synthetic@example.test", request=Mock(return_value={"status": status})), status)
        self.assertEqual(api.verify_zerobounce("synthetic@example.test", request=Mock(return_value={"status": "private-status"})), "unknown")

    def test_emailable_auth_query_not_archive_bearer(self):
        request = Mock(return_value={"state": "deliverable"})
        self.assertEqual(api.verify_emailable("synthetic@example.test", request=request), "deliverable")
        request.assert_called_once_with("GET", "https://api.emailable.com/v1/verify", params={"api_key": "synthetic-secret", "email": "synthetic@example.test"})
        self.assertEqual(api.verify_emailable("synthetic@example.test", request=Mock(return_value={"state": "deliverable", "disposable": True})), "disposable")
        self.assertEqual(api.verify_emailable("synthetic@example.test", request=Mock(return_value={"state": "private-status"})), "unknown")

    def test_independent_keyless_service_only_reports_risk(self):
        request = Mock(return_value={"smtp": True, "deliverability": "deliverable"})
        self.assertEqual(api.verify_zerobounce_ai("synthetic@example.test", request=request), "risk_report_only")
        request.assert_called_once_with("POST", "https://zerobounce.ai/developers/api/email", body={"email": "synthetic@example.test"})

    def test_verification_rejects_invalid_email_before_transport(self):
        for verifier in (api.verify_abstract, api.verify_zerobounce, api.verify_emailable, api.verify_zerobounce_ai):
            request = Mock()
            with self.subTest(verifier=verifier), self.assertRaises(ProviderError):
                verifier("masked***@example.test", request=request)
            request.assert_not_called()

    def test_provider_errors_never_include_response_private_values(self):
        for finder in (api.tomba, api.apollo, api.dropcontact):
            with self.subTest(finder=finder), self.assertRaises(ProviderError) as failure:
                finder(self.row, request=Mock(return_value={"error": "private-provider-secret"}))
            self.assertNotIn("private-provider-secret", str(failure.exception))
        for verifier in (api.verify_abstract, api.verify_zerobounce, api.verify_emailable, api.verify_zerobounce_ai):
            with self.subTest(verifier=verifier), self.assertRaises(ProviderError) as failure:
                verifier("synthetic@example.test", request=Mock(return_value={"error": "private-provider-secret"}))
            self.assertNotIn("private-provider-secret", str(failure.exception))

    def test_missing_and_header_injection_keys_are_safe(self):
        for environment in ({}, {"TOMBA_API_KEY": "key\nunsafe", "TOMBA_SECRET_KEY": "secret"}):
            request = Mock()
            with patch.dict(os.environ, environment, clear=True), self.assertRaises(ProviderError):
                api.tomba(self.row, request=request)
            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
