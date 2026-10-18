import os
import tempfile
import unittest
from unittest.mock import patch
import enrich


class EnrichTests(unittest.TestCase):
    def setUp(self):
        self.row = {"full_name":"Jane Doe", "company_domain":"example.com", "linkedin_url":"https://linkedin.com/in/jane-doe"}

    def test_dry_run_makes_no_calls(self):
        with patch.dict(enrich.PROVIDERS, {"getprospect": lambda *args: self.fail("unexpected")}), patch.dict(os.environ, {"GETPROSPECT_API_KEY":"dummy"}):
            rows, calls = enrich.run([self.row], provider_names=["getprospect"], live=False, want_phone=False, max_calls=10, max_phone_credits=0)
        self.assertEqual(calls, 0)
        self.assertEqual(rows[0]["enrichment_status"], "dry_run_no_credits_used")

    def test_duplicate_skipped(self):
        rows, calls = enrich.run([self.row, self.row], provider_names=["hunter"], live=False, want_phone=False, max_calls=1, max_phone_credits=0)
        self.assertEqual(rows[1]["enrichment_status"], "duplicate_skipped")

    def test_prospeo_verified_response(self):
        response = {"error":False, "person":{"email":{"status":"VERIFIED", "revealed":True, "email":"jane@example.com"}, "mobile":{"status":"VERIFIED", "revealed":True, "mobile":"+15555550100"}}}
        with patch.dict(os.environ, {"PROSPEO_API_KEY":"dummy"}), patch("enrich.request", return_value=response) as mocked:
            result = enrich.prospeo(self.row, True)
            self.assertEqual(result.email, "jane@example.com")
            self.assertEqual(result.phone, "+15555550100")
            self.assertTrue(mocked.call_args.kwargs["body"]["enrich_mobile"])

    def test_getprospect_response(self):
        response={"success":True,"data":{"email":{"status":"valid","revealed":True,"address":"jane@example.com"},"mobile_phone":{"status":"found","revealed":True,"number":"+15555550100"}}}
        with patch.dict(os.environ,{"GETPROSPECT_API_KEY":"dummy"}), patch("enrich.request",return_value=response):
            result=enrich.getprospect(self.row,True)
        self.assertEqual(result.email, "jane@example.com")
        self.assertEqual(result.phone, "+15555550100")

    def test_phone_budget_blocks_request(self):
        seen=[]
        def fake(row, phone):
            seen.append(phone)
            return enrich.Contact(email="jane@example.com")
        with tempfile.TemporaryDirectory() as directory, enrich.CreditBudget(directory + "/quota.sqlite3", "2026-10", {"prospeo": {"email": 1, "phone": 1}}) as budget, patch.dict(os.environ, {"PROSPEO_API_KEY":"dummy"}),patch.dict(enrich.PROVIDERS,{"prospeo":fake}):
            rows, calls=enrich.run([self.row], provider_names=["prospeo"], live=True, want_phone=True, max_calls=1, max_phone_credits=1, budget=budget)
        self.assertEqual(seen,[False]); self.assertEqual(calls,1)

    def test_hunter_parsing(self):
        with patch.dict(os.environ,{"HUNTER_API_KEY":"dummy"}), patch("enrich.request",return_value={"data":{"email":"jane@example.com"}}):
            result=enrich.hunter(self.row,False)
        self.assertEqual(result.email,"jane@example.com")

if __name__=="__main__": unittest.main()
