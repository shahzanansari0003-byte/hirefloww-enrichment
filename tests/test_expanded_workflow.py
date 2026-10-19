"""Expanded workflow regressions; all candidate records and credentials are fake.

Keep the original provider/security regression suite intact. Network access is
blocked by default, and transport tests replace only the final HTTP operation.
"""
import contextlib
import csv
import importlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import enrich
from provider_registry import DEFAULT_PROVIDERS, DEFAULT_VALIDATORS, REGISTRY


class _OfflineWorkflowHarness(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.ledger_number = 0
        self.lead = {"first_name": "Synthetic", "last_name": "Example", "company_domain": "example.test"}
        self.with_email = dict(self.lead, email="synthetic@example.test")
        keys = {key: "synthetic-value" for spec in REGISTRY.values() for key in spec.required_keys}
        environment = patch.dict(os.environ, keys, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        transport = patch("enrich.urlopen", side_effect=AssertionError("External network is forbidden in tests"))
        transport.start()
        self.addCleanup(transport.stop)

    def budget(self, limits):
        self.ledger_number += 1
        budget = enrich.CreditBudget(self.root / (str(self.ledger_number) + ".sqlite3"), "2026-10", limits)
        self.addCleanup(budget.close)
        return budget

    def run_rows(self, rows, **options):
        kwargs = dict(provider_names=["getprospect"], live=True, want_phone=False,
                      max_calls=10, max_phone_credits=0)
        kwargs.update(options)
        return enrich.run(rows, **kwargs)

    @staticmethod
    def response(payload):
        return io.BytesIO(json.dumps(payload).encode("utf-8"))


class ExpandedWorkflowTests(_OfflineWorkflowHarness):
    def test_registry_separates_discovery_verification_and_risk(self):
        self.assertEqual(set(REGISTRY), {
            "getprospect", "prospeo", "hunter", "tomba", "dropcontact", "apollo", "snov",
            "abstract", "zerobounce", "emailable", "zerobounce_ai",
        })
        self.assertEqual({name for name, spec in REGISTRY.items() if spec.kind == "discovery"}, set(enrich.PROVIDERS))
        self.assertEqual({name for name, spec in REGISTRY.items() if spec.kind in {"verification", "risk"}}, set(enrich.VALIDATORS))
        self.assertEqual(tuple(DEFAULT_PROVIDERS), ("getprospect", "prospeo", "hunter"))
        self.assertEqual(tuple(DEFAULT_VALIDATORS), ("abstract", "zerobounce", "emailable"))
        self.assertEqual(REGISTRY["zerobounce_ai"].kind, "risk")

    def test_all_providers_and_validators_remain_offline_in_dry_run(self):
        discovery = {name: Mock(side_effect=AssertionError("Dry-run discovery")) for name in enrich.PROVIDERS}
        validation = {name: Mock(side_effect=AssertionError("Dry-run validation")) for name in enrich.VALIDATORS}
        with patch.dict(enrich.PROVIDERS, discovery), patch.dict(enrich.VALIDATORS, validation):
            result, calls = self.run_rows([self.lead, self.with_email], provider_names=list(enrich.PROVIDERS),
                                          live=False, verify=True, validators=list(enrich.VALIDATORS),
                                          max_verifications=20, approved_providers={"apollo", "snov"})
        self.assertEqual(calls, 0)
        self.assertEqual(len(result), 2)
        for callback in [*discovery.values(), *validation.values()]:
            callback.assert_not_called()

    def test_zero_allocations_disable_every_expanded_operation(self):
        callbacks = {name: Mock(side_effect=AssertionError("Zero-cap discovery")) for name in enrich.PROVIDERS}
        validators = {name: Mock(side_effect=AssertionError("Zero-cap validation")) for name in enrich.VALIDATORS}
        budget = self.budget({})
        with patch.dict(enrich.PROVIDERS, callbacks), patch.dict(enrich.VALIDATORS, validators):
            _, calls = self.run_rows([self.lead, self.with_email], provider_names=list(enrich.PROVIDERS),
                                     budget=budget, verify=True, validators=list(enrich.VALIDATORS),
                                     max_verifications=20, approved_providers={"apollo", "snov"})
        self.assertEqual(calls, 0)
        for callback in [*callbacks.values(), *validators.values()]:
            callback.assert_not_called()

    def test_invalid_or_wrong_kind_selections_fail_before_callbacks(self):
        invalid = (
            {"provider_names": ["unknown"]},
            {"provider_names": ["abstract"]},
            {"provider_names": ["tomba", "tomba"]},
            {"verify": True, "validators": ["tomba"]},
            {"verify": True, "validators": ["abstract", "abstract"]},
            {"max_verifications": -1},
            {"max_http_requests": -1},
        )
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_rows([self.lead], live=False, **changes)

    def test_live_validation_requires_persistent_budget(self):
        callback = Mock(side_effect=AssertionError("No live validation without ledger"))
        with patch.dict(enrich.VALIDATORS, {"abstract": callback}), self.assertRaises(ValueError):
            self.run_rows([self.with_email], verify=True, validators=["abstract"], max_verifications=1)
        callback.assert_not_called()

    def test_verification_is_explicit_and_separate_from_discovery(self):
        budget = self.budget({"abstract": {"email": 2}})
        callback = Mock(return_value="deliverable")
        with patch.dict(enrich.VALIDATORS, {"abstract": callback}):
            result, calls = self.run_rows([self.with_email], max_calls=0, budget=budget,
                                          validators=["abstract"], max_verifications=1)
        self.assertEqual(calls, 0)
        callback.assert_not_called()
        self.assertEqual(result[0]["email_status"], "input_unverified")
        self.assertFalse(result[0].get("verification_source"))

    def test_verification_does_not_overwrite_email_provenance_or_status(self):
        budget = self.budget({"tomba": {"email": 1}, "abstract": {"email": 1}})
        finder = Mock(return_value=enrich.Contact(email="found@example.test", email_status="provider_verified"))
        validator = Mock(return_value="deliverable")
        stats = {}
        with patch.dict(enrich.PROVIDERS, {"tomba": finder}), patch.dict(enrich.VALIDATORS, {"abstract": validator}):
            result, calls = self.run_rows([self.lead], provider_names=["tomba"], budget=budget,
                                          verify=True, validators=["abstract"], max_verifications=1, stats=stats)
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["email_source"], "tomba")
        self.assertEqual(result[0]["email_status"], "provider_verified")
        self.assertEqual(result[0]["verification_source"], "abstract")
        self.assertEqual(result[0]["verification_status"], "deliverable")
        validator.assert_called_once_with("found@example.test")
        self.assertEqual(stats["verifications"], 1)

    def test_validation_cap_spans_fallbacks_and_candidates(self):
        budget = self.budget({"abstract": {"email": 4}, "zerobounce": {"email": 4}})
        first = Mock(side_effect=enrich.ProviderError("private-key-and-candidate"))
        second = Mock(return_value="valid")
        stats = {}
        rows = [self.with_email, dict(self.with_email, email="other@example.test")]
        with patch.dict(enrich.VALIDATORS, {"abstract": first, "zerobounce": second}):
            result, calls = self.run_rows(rows, max_calls=0, budget=budget, verify=True,
                                          validators=["abstract", "zerobounce"], max_verifications=1, stats=stats)
        self.assertEqual(calls, 0)
        self.assertEqual(first.call_count, 1)
        second.assert_not_called()
        self.assertEqual(stats["verifications"], 1)
        self.assertNotIn("private-key-and-candidate", repr(result))

    def test_failed_validation_reservation_survives_next_run(self):
        budget = self.budget({"abstract": {"email": 1}})
        callback = Mock(side_effect=enrich.ProviderError("synthetic private payload"))
        with patch.dict(enrich.VALIDATORS, {"abstract": callback}):
            for _ in range(2):
                self.run_rows([self.with_email], max_calls=0, budget=budget, verify=True,
                              validators=["abstract"], max_verifications=1)
        self.assertEqual(callback.call_count, 1)
        self.assertFalse(budget.reserve("abstract", email=1))

    def test_default_verifiers_do_not_send_data_to_keyless_risk_service(self):
        budget = self.budget({name: {"email": 1} for name in enrich.VALIDATORS})
        risk = Mock(side_effect=AssertionError("Risk service requires explicit selection"))
        with patch.dict(os.environ, {}, clear=True), patch.dict(enrich.VALIDATORS, {"zerobounce_ai": risk}):
            self.run_rows([self.with_email], max_calls=0, budget=budget, verify=True, max_verifications=5)
        risk.assert_not_called()

    def test_keyless_risk_check_has_separate_status_and_positive_caps(self):
        budget = self.budget({"zerobounce_ai": {"email": 2}})
        callback = Mock(return_value="risk_report_only")
        with patch.dict(enrich.VALIDATORS, {"zerobounce_ai": callback}):
            self.run_rows([self.with_email], max_calls=0, budget=budget, verify=True,
                          validators=["zerobounce_ai"], max_verifications=0)
            callback.assert_not_called()
            result, calls = self.run_rows([self.with_email], max_calls=0, budget=budget, verify=True,
                                          validators=["zerobounce_ai"], max_verifications=1)
        self.assertEqual(calls, 0)
        callback.assert_called_once_with("synthetic@example.test")
        self.assertEqual(result[0]["email_status"], "input_unverified")
        self.assertEqual(result[0]["risk_source"], "zerobounce_ai")
        self.assertEqual(result[0]["risk_status"], "risk_report_only")
        self.assertFalse(result[0].get("verification_source"))

    def test_risk_transport_sends_only_email(self):
        budget = self.budget({"zerobounce_ai": {"email": 1}})
        def http_response(req, **kwargs):
            self.assertEqual(req.full_url, "https://zerobounce.ai/developers/api/email")
            self.assertEqual(json.loads(req.data), {"email": "synthetic@example.test"})
            return self.response({"risk": "low"})
        with patch("enrich.urlopen", side_effect=http_response) as transport:
            result, _ = self.run_rows([dict(self.with_email, phone="+12025550123")], max_calls=0,
                                      budget=budget, verify=True, validators=["zerobounce_ai"], max_verifications=1)
        transport.assert_called_once()
        self.assertEqual(result[0]["email_status"], "input_unverified")
        self.assertFalse(result[0].get("verification_source"))

    def test_untrusted_validator_status_is_not_stored(self):
        private = "private-provider-message-or-contact"
        budget = self.budget({"abstract": {"email": 1}})
        with patch.dict(enrich.VALIDATORS, {"abstract": lambda email: private}):
            result, _ = self.run_rows([self.with_email], max_calls=0, budget=budget, verify=True,
                                      validators=["abstract"], max_verifications=1)
        self.assertNotIn(private, repr(result))

    def test_malformed_validator_status_does_not_abort_results(self):
        private = "private-provider-message-or-contact"
        budget = self.budget({"abstract": {"email": 1}})
        with patch.dict(enrich.VALIDATORS, {"abstract": lambda email: [private]}):
            result, _ = self.run_rows([self.with_email], max_calls=0, budget=budget, verify=True,
                                      validators=["abstract"], max_verifications=1)
        self.assertEqual(result[0]["verification_status"], "unknown")
        self.assertNotIn(private, repr(result))

    def test_imported_verification_fields_cannot_bypass_requested_validation(self):
        budget = self.budget({"abstract": {"email": 1}})
        callback = Mock(return_value="undeliverable")
        row = dict(self.with_email, verification_source="abstract", verification_status="deliverable")
        with patch.dict(enrich.VALIDATORS, {"abstract": callback}):
            result, _ = self.run_rows([row], max_calls=0, budget=budget, verify=True,
                                      validators=["abstract"], max_verifications=1)
        callback.assert_called_once_with("synthetic@example.test")
        self.assertEqual(result[0]["verification_status"], "undeliverable")

    def test_restricted_discovery_needs_provider_specific_approval(self):
        for provider, cap in (("apollo", 9), ("snov", 1)):
            with self.subTest(provider=provider):
                budget = self.budget({provider: {"email": cap}})
                callback = Mock(return_value=enrich.Contact(email="found@example.test"))
                with patch.dict(enrich.PROVIDERS, {provider: callback}):
                    _, calls = self.run_rows([self.lead], provider_names=[provider], budget=budget)
                    self.assertEqual(calls, 0)
                    callback.assert_not_called()
                    result, calls = self.run_rows([self.lead], provider_names=[provider], budget=budget,
                                                  approved_providers={provider},
                                                  request_credit_caps={"snov": cap} if provider == "snov" else {})
                self.assertEqual(calls, 1)
                self.assertEqual(result[0]["email_source"], provider)

    def test_snov_without_confirmed_request_cost_cannot_submit(self):
        budget = self.budget({"snov": {"email": 20}})
        callback = Mock(side_effect=AssertionError("Unknown charge bound must block submission"))
        with patch.dict(enrich.PROVIDERS, {"snov": callback}):
            _, calls = self.run_rows([self.lead], provider_names=["snov"], budget=budget,
                                     approved_providers={"snov"})
        self.assertEqual(calls, 0)
        callback.assert_not_called()

    def test_snov_reserves_explicit_worst_case_before_submission(self):
        budget = self.budget({"snov": {"email": 6}})
        callback = Mock(return_value=enrich.Contact(pending_task="snov:synthetic_job"))
        stats = {}
        with patch.dict(enrich.PROVIDERS, {"snov": callback}):
            result, calls = self.run_rows([self.lead], provider_names=["snov"], budget=budget,
                                          approved_providers={"snov"}, request_credit_caps={"snov": 6}, stats=stats)
        callback.assert_called_once()
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["pending_task"], "snov:synthetic_job")
        self.assertEqual(stats["reserved_credit_units"], 6)
        self.assertFalse(budget.reserve("snov", email=1))

    def test_pending_async_discovery_stops_fallback_and_preserves_task(self):
        budget = self.budget({"dropcontact": {"email": 1}, "tomba": {"email": 1}})
        pending = Mock(return_value=enrich.Contact(pending_task="dropcontact:synthetic_job"))
        fallback = Mock(side_effect=AssertionError("Pending job must not start another discovery"))
        with patch.dict(enrich.PROVIDERS, {"dropcontact": pending, "tomba": fallback}):
            result, calls = self.run_rows([self.lead], provider_names=["dropcontact", "tomba"], budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["pending_task"], "dropcontact:synthetic_job")
        self.assertEqual(result[0]["enrichment_status"], "pending")
        fallback.assert_not_called()

    def test_pending_rows_are_not_resubmitted_by_normal_enrichment(self):
        row = dict(self.lead, pending_task="dropcontact:synthetic_job")
        budget = self.budget({"dropcontact": {"email": 1}, "tomba": {"email": 1}})
        callbacks = {name: Mock(side_effect=AssertionError("Use explicit resume for pending jobs"))
                     for name in ("dropcontact", "tomba")}
        with patch.dict(enrich.PROVIDERS, callbacks):
            result, calls = self.run_rows([row], provider_names=list(callbacks), budget=budget)
        self.assertEqual(calls, 0)
        self.assertEqual(result[0]["pending_task"], row["pending_task"])

    def test_pending_identity_prevents_later_duplicate_submission(self):
        row = dict(self.lead, pending_task="dropcontact:existing_job")
        for pending_index in (0, 1):
            with self.subTest(pending_index=pending_index):
                budget = self.budget({"dropcontact": {"email": 1}})
                callback = Mock(side_effect=AssertionError("Candidate already has a pending job"))
                rows = [row, self.lead] if pending_index == 0 else [self.lead, row]
                with patch.dict(enrich.PROVIDERS, {"dropcontact": callback}):
                    result, calls = self.run_rows(rows, provider_names=["dropcontact"], budget=budget)
                self.assertEqual(calls, 0)
                callback.assert_not_called()
                self.assertEqual(result[pending_index]["pending_task"], "dropcontact:existing_job")
                self.assertEqual(result[1 - pending_index]["enrichment_status"], "duplicate_skipped")

    def test_later_ledger_failure_keeps_submitted_task_and_stops_discovery(self):
        budget = self.budget({"dropcontact": {"email": 3}, "abstract": {"email": 3}})
        def submit(row, phone):
            budget.close()  # Simulate a ledger becoming unavailable after submission.
            return enrich.Contact(pending_task="dropcontact:submitted_job")
        submitter = Mock(side_effect=submit)
        validator = Mock(side_effect=AssertionError("Ledger failure must stop validation too"))
        rows = [self.lead, dict(self.lead, first_name="Second"), dict(self.with_email, email="later@example.test")]
        stats = {}
        with patch.dict(enrich.PROVIDERS, {"dropcontact": submitter}), patch.dict(enrich.VALIDATORS, {"abstract": validator}):
            result, calls = self.run_rows(rows, provider_names=["dropcontact"], budget=budget,
                                          verify=True, validators=["abstract"], max_verifications=3, stats=stats)
        submitter.assert_called_once()
        validator.assert_not_called()
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["pending_task"], "dropcontact:submitted_job")
        self.assertEqual(result[0]["enrichment_status"], "pending")
        for row in result[1:]:
            self.assertEqual(row["enrichment_status"], "credit_ledger_error")
            self.assertIn("credit_ledger:unavailable", row["errors"])
        self.assertEqual(stats["ledger_errors"], 1)

    def test_async_start_returns_pending_without_automatic_polling(self):
        budget = self.budget({"dropcontact": {"email": 1}})
        def http_response(req, **kwargs):
            self.assertEqual(req.method, "POST")
            self.assertEqual(req.full_url, "https://api.dropcontact.com/v1/enrich/all")
            return self.response({"success": True, "request_id": "synthetic_job"})
        with patch("enrich.urlopen", side_effect=http_response) as transport:
            result, calls = self.run_rows([self.lead], provider_names=["dropcontact"], budget=budget)
        self.assertEqual(calls, 1)
        transport.assert_called_once()
        self.assertEqual(result[0]["pending_task"], "dropcontact:synthetic_job")

    def test_http_request_cap_applies_inside_multirequest_callback(self):
        budget = self.budget({"tomba": {"email": 1}})
        def provider(row, phone):
            enrich.request("GET", "https://api.tomba.io/v1/email-finder")
            enrich.request("GET", "https://api.tomba.io/v1/email-finder")
            return enrich.Contact()
        stats = {}
        with patch.dict(enrich.PROVIDERS, {"tomba": provider}), patch("enrich.urlopen", side_effect=lambda *a, **k: self.response({})) as transport:
            _, calls = self.run_rows([self.lead], provider_names=["tomba"], budget=budget,
                                     max_http_requests=1, stats=stats)
        self.assertEqual(calls, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(stats["http_requests"], 1)

    def test_snov_never_starts_job_when_http_budget_cannot_cover_authentication(self):
        budget = self.budget({"snov": {"email": 1}})
        seen = []
        def http_response(req, **kwargs):
            seen.append(req.full_url)
            self.assertTrue(req.full_url.endswith("/oauth/access_token"))
            return self.response({"access_token": "synthetic-access-token"})
        with patch("enrich.urlopen", side_effect=http_response):
            self.run_rows([self.lead], provider_names=["snov"], budget=budget,
                          approved_providers={"snov"}, request_credit_caps={"snov": 1}, max_http_requests=1)
        self.assertLessEqual(len(seen), 1)
        self.assertFalse(any(url.endswith("/start") for url in seen))

    def test_phone_request_is_never_sent_to_apollo(self):
        budget = self.budget({"apollo": {"email": 9, "phone": 9}})
        def http_response(req, **kwargs):
            body = json.loads(req.data)
            for flag in ("reveal_personal_emails", "reveal_phone_number", "run_waterfall_email", "run_waterfall_phone"):
                self.assertIs(body[flag], False)
            return self.response({"person": None})
        with patch("enrich.urlopen", side_effect=http_response) as transport:
            self.run_rows([self.lead], provider_names=["apollo"], budget=budget,
                          want_phone=True, max_phone_credits=9, approved_providers={"apollo"})
        transport.assert_called_once()


class ResumeWorkflowTests(_OfflineWorkflowHarness):
    """Explicit resume is subject to the same privacy and quota boundaries."""

    def setUp(self):
        super().setUp()
        self.resume = importlib.import_module("resume_pending")
        self.pending = dict(self.lead, pending_task="dropcontact:synthetic_job")

    def test_resume_dry_run_does_not_authenticate_or_poll(self):
        result, calls = self.resume.run([self.pending], live=False, budget=None)
        self.assertEqual(calls, 0)
        self.assertEqual(result[0]["pending_task"], self.pending["pending_task"])

    def test_resume_retrieves_existing_dropcontact_task_without_submission(self):
        budget = self.budget({"dropcontact": {"email": 1}})
        def http_response(req, **kwargs):
            self.assertEqual(req.method, "GET")
            self.assertEqual(req.full_url, "https://api.dropcontact.com/v1/enrich/all/synthetic_job")
            return self.response({"success": True, "data": [{"email": [
                {"email": "recovered@example.test", "qualification": "nominative@pro"},
            ]}]})
        with patch("enrich.urlopen", side_effect=http_response) as transport:
            result, calls = self.resume.run([self.pending], live=True, budget=budget)
        self.assertEqual(calls, 1)
        transport.assert_called_once()
        self.assertEqual(result[0]["email"], "recovered@example.test")
        self.assertEqual(result[0]["pending_task"], "")

    def test_resume_rejects_unrecognized_or_unsafe_tasks_before_network(self):
        budget = self.budget({"dropcontact": {"email": 10}})
        for task in ("dropcontact:../../secret", "dropcontact:job?token=secret", "dropcontact:job/other",
                     "dropcontact:", "hunter:validjob", "unknown:validjob", "missing-separator"):
            with self.subTest(task=task):
                result, calls = self.resume.run([dict(self.pending, pending_task=task)], live=True, budget=budget)
                self.assertEqual(calls, 0)
                self.assertFalse(result[0].get("email"))

    def test_resume_duplicate_tasks_are_polled_at_most_once(self):
        budget = self.budget({"dropcontact": {"email": 2}})
        with patch("enrich.urlopen", side_effect=lambda *a, **k: self.response({"success": False})) as transport:
            result, calls = self.resume.run([self.pending, dict(self.pending)], live=True, budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(result[0]["pending_task"], self.pending["pending_task"])

    def test_resume_preserves_pending_on_legitimate_in_progress(self):
        budget = self.budget({"dropcontact": {"email": 1}})
        with patch("enrich.urlopen", side_effect=lambda *a, **k: self.response({"success": False})):
            result, _ = self.resume.run([self.pending], live=True, budget=budget)
        self.assertEqual(result[0]["pending_task"], self.pending["pending_task"])
        self.assertFalse(result[0].get("email"))

    def test_resume_snov_authenticates_and_retrieves_but_never_starts(self):
        budget = self.budget({"snov": {"email": 1}})
        seen = []
        def http_response(req, **kwargs):
            seen.append(req.full_url)
            if req.full_url.endswith("/oauth/access_token"):
                return self.response({"access_token": "synthetic-access-token"})
            self.assertEqual(req.method, "GET")
            self.assertIn("/emails-by-domain-by-name/result?", req.full_url)
            return self.response({"status": "completed", "data": [{"result": [{
                "email": "recovered@example.test", "smtp_status": "valid", "is_valid_format": True,
            }]}]})
        with patch("enrich.urlopen", side_effect=http_response):
            result, calls = self.resume.run([dict(self.pending, pending_task="snov:synthetic_job")],
                                            live=True, budget=budget, approved_providers={"snov"},
                                            request_credit_caps={"snov": 1})
        self.assertEqual(calls, 1)
        self.assertEqual(len(seen), 2)
        self.assertFalse(any(url.endswith("/start") for url in seen))
        self.assertEqual(result[0]["email"], "recovered@example.test")

    def test_resume_http_budget_blocks_snov_result_after_authentication(self):
        budget = self.budget({"snov": {"email": 1}})
        def http_response(req, **kwargs):
            self.assertTrue(req.full_url.endswith("/oauth/access_token"))
            return self.response({"access_token": "synthetic-access-token"})
        stats = {}
        with patch("enrich.urlopen", side_effect=http_response) as transport:
            result, _ = self.resume.run([dict(self.pending, pending_task="snov:synthetic_job")], live=True,
                                        budget=budget, approved_providers={"snov"}, request_credit_caps={"snov": 1},
                                        max_http_requests=1, stats=stats)
        self.assertLessEqual(transport.call_count, 1)
        self.assertEqual(result[0]["pending_task"], "snov:synthetic_job")
        self.assertLessEqual(stats["http_requests"], 1)

    def test_resume_snov_requires_confirmed_request_cost_before_authentication(self):
        budget = self.budget({"snov": {"email": 20}})
        reader = Mock(side_effect=AssertionError("Unknown charging bound must block retrieval"))
        row = dict(self.pending, pending_task="snov:synthetic_job")
        with patch.object(self.resume, "read_pending", reader):
            result, calls = self.resume.run([row], live=True, budget=budget, approved_providers={"snov"})
        reader.assert_not_called()
        self.assertEqual(calls, 0)
        self.assertEqual(result[0]["pending_task"], row["pending_task"])

    def test_resume_zero_operation_cap_makes_no_requests(self):
        budget = self.budget({"dropcontact": {"email": 1}})
        result, calls = self.resume.run([self.pending], live=True, budget=budget, max_calls=0)
        self.assertEqual(calls, 0)
        self.assertEqual(result[0]["pending_task"], self.pending["pending_task"])

    def test_resume_does_not_trust_imported_contact_verification_claims(self):
        row = dict(self.pending, email="input@example.test", phone="+12025550123",
                   email_source="hunter", email_status="provider_verified",
                   phone_source="prospeo", phone_status="provider_verified",
                   verification_source="abstract", verification_status="deliverable",
                   risk_source="zerobounce_ai", risk_status="risk_report_only")
        result, calls = self.resume.run([row], live=False, budget=None)
        self.assertEqual(calls, 0)
        self.assertEqual(result[0]["email_source"], "input")
        self.assertEqual(result[0]["email_status"], "input_unverified")
        self.assertEqual(result[0]["phone_source"], "input")
        self.assertEqual(result[0]["phone_status"], "input_unverified")
        for field in ("verification_source", "verification_status", "risk_source", "risk_status"):
            self.assertEqual(result[0][field], "")

    def test_resume_keeps_recovered_contact_when_ledger_fails_for_later_jobs(self):
        budget = self.budget({"dropcontact": {"email": 3}})
        def retrieve(task):
            budget.close()
            return enrich.Contact(email="recovered@example.test", email_status="provider_found")
        reader = Mock(side_effect=retrieve)
        rows = [self.pending, dict(self.pending, pending_task="dropcontact:second_job"),
                dict(self.pending, pending_task="dropcontact:third_job")]
        stats = {}
        with patch.object(self.resume, "read_pending", reader):
            result, calls = self.resume.run(rows, live=True, budget=budget, stats=stats)
        reader.assert_called_once()
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["email"], "recovered@example.test")
        self.assertEqual(result[0]["enrichment_status"], "enriched")
        self.assertEqual(result[0]["pending_task"], "")
        for source, recovered in zip(rows[1:], result[1:]):
            self.assertEqual(recovered["pending_task"], source["pending_task"])
            self.assertEqual(recovered["enrichment_status"], "credit_ledger_error")
            self.assertIn("credit_ledger:unavailable", recovered["errors"])
        self.assertEqual(stats["ledger_errors"], 1)

    def input_file(self, **changes):
        path = self.root / "pending.csv"
        row = dict(self.pending, **changes)
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(row))
            writer.writeheader()
            writer.writerow(row)
        return str(path)

    def cli_arguments(self, output, **row):
        return ["--input", self.input_file(**row), "--output", str(output), "--live",
                "--credit-cycle", "2026-10", "--email-credits", "dropcontact=1",
                "--credit-ledger", str(self.root / "cli.sqlite3")]

    def test_resume_existing_output_prevents_authentication_and_polling(self):
        output = self.root / "existing.csv"
        output.write_text("preserve me")
        with patch.object(self.resume, "run") as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            self.resume.main(self.cli_arguments(output))
        run.assert_not_called()
        self.assertEqual(output.read_text(), "preserve me")

    def test_resume_output_is_private_and_neutralizes_formula_cells(self):
        output = self.root / "results" / "resumed.csv"
        with patch("enrich.urlopen", side_effect=lambda *a, **k: self.response({"success": False})), contextlib.redirect_stdout(io.StringIO()):
            self.resume.main(self.cli_arguments(output, full_name="=1+1"))
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with output.open() as handle:
            row = next(csv.DictReader(handle))
        self.assertTrue(row["full_name"].startswith("'"))
        self.assertEqual(row["pending_task"], self.pending["pending_task"])


if __name__ == "__main__":
    unittest.main()
