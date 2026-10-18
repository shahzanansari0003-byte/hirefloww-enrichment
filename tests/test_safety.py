"""Credit, privacy, deduplication and CLI regressions. No external requests."""
import contextlib
import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import enrich


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.row = {"first_name": "Synthetic", "last_name": "One", "company_domain": "example.test"}
        network = patch("enrich.urlopen", side_effect=AssertionError("Network forbidden in tests"))
        network.start()
        self.addCleanup(network.stop)
        keys = patch.dict(os.environ, {"GETPROSPECT_API_KEY": "dummy", "PROSPEO_API_KEY": "dummy", "HUNTER_API_KEY": "dummy"}, clear=True)
        keys.start()
        self.addCleanup(keys.stop)

    def budget(self, limits):
        budget = enrich.CreditBudget(self.root / "quota.sqlite3", "2026-10", limits)
        self.addCleanup(budget.close)
        return budget

    def run_rows(self, rows, **kwargs):
        options = dict(provider_names=["getprospect"], live=True, want_phone=False, max_calls=10, max_phone_credits=0)
        options.update(kwargs)
        return enrich.run(rows, **options)

    def test_live_requires_budget_even_when_keys_exist(self):
        with patch("enrich.request") as request, self.assertRaises(ValueError):
            self.run_rows([self.row])
        request.assert_not_called()

    def test_zero_default_allowance_never_calls_provider(self):
        with patch("enrich.request") as request:
            rows, calls = self.run_rows([self.row], budget=self.budget({}))
        self.assertEqual(calls, 0)
        self.assertEqual(rows[0]["enrichment_status"], "not_attempted_missing_keys_or_budget")
        request.assert_not_called()

    def test_credit_reservation_happens_before_failure_and_is_not_refunded(self):
        budget = self.budget({"getprospect": {"email": 1}})
        with patch.dict(enrich.PROVIDERS, {"getprospect": lambda *a: (_ for _ in ()).throw(enrich.ProviderError("private-key-and-candidate"))}):
            rows, calls = self.run_rows([self.row], budget=budget)
            _, later_calls = self.run_rows([self.row], budget=budget)
        self.assertEqual((calls, later_calls), (1, 0))
        self.assertEqual(rows[0]["errors"], "getprospect:request_failed")
        self.assertNotIn("private-key", str(rows))

    def test_fallback_shares_global_call_cap(self):
        budget = self.budget({p: {"email": 5} for p in enrich.PROVIDERS})
        with patch.dict(enrich.PROVIDERS, {p: lambda *a: enrich.Contact() for p in enrich.PROVIDERS}):
            _, calls = self.run_rows([self.row], provider_names=list(enrich.PROVIDERS), max_calls=1, budget=budget)
        self.assertEqual(calls, 1)

    def test_phone_credit_cap_downgrades_to_email_only(self):
        budget = self.budget({"getprospect": {"email": 1, "phone": 0}})
        with patch.dict(enrich.PROVIDERS, {"getprospect": lambda row, phone: self.assertFalse(phone) or enrich.Contact(email="synthetic@example.test")}):
            rows, calls = self.run_rows([self.row], want_phone=True, max_phone_credits=5, budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(rows[0]["email"], "synthetic@example.test")

    def test_phone_only_getprospect_needs_no_email_credit(self):
        budget = self.budget({"getprospect": {"email": 0, "phone": 1}})
        row = dict(self.row, email="input@example.test")
        def provider(row, phone):
            self.assertTrue(phone)
            return enrich.Contact(email="replacement@example.test", phone="+12025550123", phone_status="provider_found")
        with patch.dict(enrich.PROVIDERS, {"getprospect": provider}):
            rows, calls = self.run_rows([row], want_phone=True, max_phone_credits=1, budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(rows[0]["email"], "input@example.test")
        self.assertEqual(rows[0]["email_status"], "input_unverified")
        self.assertEqual(rows[0]["phone_status"], "provider_found")
        self.assertFalse(budget.reserve("getprospect", phone=1))

    def test_distinct_linkedin_identities_are_not_merged_on_common_name(self):
        rows = [dict(self.row, linkedin_url="https://linkedin.com/in/synthetic-one"),
                dict(self.row, linkedin_url="https://linkedin.com/in/synthetic-two")]
        result, _ = self.run_rows(rows, live=False)
        self.assertEqual([r["enrichment_status"] for r in result], ["dry_run_no_credits_used"] * 2)

    def test_malformed_later_identifiers_do_not_lose_prior_result(self):
        budget = self.budget({"getprospect": {"email": 2}})
        with patch.dict(enrich.PROVIDERS, {"getprospect": lambda *a: enrich.Contact(email="found@example.test")}):
            result, calls = self.run_rows([self.row, {"linkedin_url": "https://["}], budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["email"], "found@example.test")
        self.assertEqual(result[1]["enrichment_status"], "invalid_identifiers")

    def test_punctuation_only_phone_is_not_accepted(self):
        budget = self.budget({"getprospect": {"email": 1, "phone": 1}})
        with patch.dict(enrich.PROVIDERS, {"getprospect": lambda *a: enrich.Contact(phone="-------", phone_status="provider_found")}):
            result, _ = self.run_rows([self.row], want_phone=True, max_phone_credits=1, budget=budget)
        self.assertEqual(result[0]["phone"], "")

    def test_prospeo_phone_cost_includes_email(self):
        budget = self.budget({"prospeo": {"email": 0, "phone": 10}})
        def provider(row, phone):
            self.assertTrue(phone)
            return enrich.Contact(email="found@example.test", phone="+12025550123")
        with patch.dict(enrich.PROVIDERS, {"prospeo": provider}):
            result, calls = self.run_rows([self.row], provider_names=["prospeo"], want_phone=True, max_phone_credits=10, budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["phone"], "+12025550123")
        self.assertFalse(budget.reserve("prospeo", phone=1))

    def test_existing_contacts_do_not_inherit_claimed_verification(self):
        row = dict(self.row, email="input@example.test", email_status="provider_verified", email_source="hunter")
        rows, calls = self.run_rows([row], budget=self.budget({}))
        self.assertEqual(calls, 0)
        self.assertEqual(rows[0]["enrichment_status"], "already_present")
        self.assertEqual(rows[0]["email_source"], "input")
        self.assertEqual(rows[0]["email_status"], "input_unverified")

    def test_duplicate_uses_all_normalized_identifiers(self):
        rows = [dict(self.row, linkedin_url="https://www.linkedin.com/in/synthetic-one/?x=1", email="one@example.test"),
                {"linkedin_url": "http://linkedin.com/in/synthetic-one", "email": "two@example.test"},
                {"email": "TWO@example.test"}]
        result, calls = self.run_rows(rows, live=False)
        self.assertEqual(calls, 0)
        self.assertEqual([r["enrichment_status"] for r in result[1:]], ["duplicate_skipped", "duplicate_skipped"])

    def test_newly_found_email_prevents_later_duplicate_call(self):
        budget = self.budget({"getprospect": {"email": 3, "phone": 3}})
        def provider(row, phone):
            return enrich.Contact(email="found@example.test")
        with patch.dict(enrich.PROVIDERS, {"getprospect": provider}):
            result, calls = self.run_rows([self.row, {"email": "FOUND@example.test"}], want_phone=True, max_phone_credits=3, budget=budget)
        self.assertEqual(calls, 1)
        self.assertEqual(result[1]["enrichment_status"], "duplicate_skipped")

    def test_invalid_caps_and_duplicate_providers_fail_before_call(self):
        for changes in ({"max_calls": -1}, {"max_phone_credits": -1}, {"provider_names": ["hunter", "hunter"]}, {"provider_names": ["invalid"]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.run_rows([self.row], live=False, **changes)

    def input_file(self):
        path = self.root / "input.csv"
        path.write_text("first_name,last_name,company_domain\nSynthetic,One,example.test\n")
        return str(path)

    def test_cli_default_dryrun_is_private_and_offline(self):
        output = self.root / "results" / "dryrun.csv"
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(enrich.main(["--input", self.input_file(), "--output", str(output)]), 0)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        with output.open() as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(rows[0]["enrichment_status"], "dry_run_no_credits_used")
        self.assertFalse((self.root / "quota.sqlite3").exists())

    def test_existing_output_prevents_live_lookup(self):
        output = self.root / "existing.csv"
        output.write_text("preserve me")
        with patch("enrich.run") as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            enrich.main(["--input", self.input_file(), "--output", str(output), "--live", "--credit-cycle", "2026-10", "--email-credits", "getprospect=1"])
        run.assert_not_called()
        self.assertEqual(output.read_text(), "preserve me")

    def test_cli_live_without_cycle_fails_before_call(self):
        with patch("enrich.run") as run, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            enrich.main(["--input", self.input_file(), "--live"])
        run.assert_not_called()

    def test_formula_cells_are_neutralized_in_csv(self):
        for cell in ("=1+1", "+1", "-1", "@SUM(1)", "  =1", "\t=1"):
            self.assertTrue(enrich._csv_safe(cell).startswith("'"))
        self.assertEqual(enrich._csv_safe("safe@example.test"), "safe@example.test")

    def test_live_cli_without_allocations_makes_zero_calls(self):
        output = self.root / "empty-cap.csv"
        with patch("enrich.request") as request, contextlib.redirect_stdout(io.StringIO()):
            enrich.main(["--input", self.input_file(), "--output", str(output), "--live", "--credit-cycle", "2026-10", "--credit-ledger", str(self.root / "quota.sqlite3")])
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
