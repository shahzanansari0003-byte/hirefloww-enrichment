"""Offline tests for JD to X-ray to GitHub-only email pipeline."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import jd_linkedin_github as job


class JDToGithubTests(unittest.TestCase):
    def setUp(self):
        self.jd = ("Job Title: ServiceNow Technical Lead\n11+ years overall "
                   "and 8 years ServiceNow. CSM, Virtual Agent, CMDB, "
                   "Now Assist, Integrations. Bengaluru, Hyderabad, Pune.")

    def test_reads_jd_and_generates_boolean_and_xray(self):
        spec = job.read_jd(self.jd)
        self.assertEqual("ServiceNow Technical Lead", spec["role"])
        self.assertIn("CMDB", spec["skills"])
        queries = job.boolean_queries(spec)
        self.assertEqual(3, len(queries))
        self.assertTrue(queries[0]["query"].startswith("site:linkedin.com/in/"))
        self.assertIn("google.com/search", queries[0]["google_xray_url"])

    def test_bad_jd_and_urls_fail_closed(self):
        with self.assertRaises(ValueError):
            job.read_jd("too short")
        with self.assertRaises(ValueError):
            job.public_linkedin("https://linkedin.com.attacker.example/in/test")
        with self.assertRaises(ValueError):
            job.github_login("https://github.com/owner/repo")

    def test_manual_pair_and_reject_embedded_email(self):
        text = ("name,linkedin_url,github_url\n"
                "Test Person,https://linkedin.com/in/test-person,https://github.com/testperson\n")
        rows = job.manual_pairs(text)
        self.assertEqual(rows[0]["github_url"], "https://github.com/testperson")
        with self.assertRaises(ValueError):
            job.manual_pairs(text.replace("github_url", "email"))

    def test_no_bogus_emails(self):
        for value in ("bot@users.noreply.github.com", "fake@example.com",
                      "user@localhost.local", "test@company.com"):
            self.assertFalse(job.email_ok(value))
        self.assertTrue(job.email_ok("public.technical@company.com"))

    def test_backlink_requires_same_linkedin_profile(self):
        p = {"blog": "https://www.linkedin.com/in/person-a?trk=test"}
        self.assertTrue(job.profile_backlink(p, "https://www.linkedin.com/in/person-a"))
        self.assertFalse(job.profile_backlink(p, "https://www.linkedin.com/in/person-b"))

    def test_mocked_verified_author_email(self):
        class FakeGitHub:
            def get(self, path):
                return {"type": "User", "login": "exampleperson", "email": None}
            def get_list(self, path):
                if "/users/" in path:
                    return [{"full_name": "exampleperson/demo"}]
                return [{"author": {"login": "exampleperson"},
                         "commit": {"author": {"email": "real@company.dev"}},
                         "html_url": "https://github.com/exampleperson/demo/commit/123"}]
        result = job.github_email(FakeGitHub(), "exampleperson")
        self.assertEqual("real@company.dev", result[0])

    def test_wrong_attribution_does_not_leak_email(self):
        class FakeGitHub:
            def get(self, path):
                return {"type": "User", "login": "correctperson"}
            def get_list(self, path):
                if "/users/" in path:
                    return [{"full_name": "correctperson/repo"}]
                return [{"author": {"login": "differentperson"},
                         "commit": {"author": {"email": "wrong@company.dev"}},
                         "html_url": "https://github.com/some/repo/commit/123"}]
        self.assertEqual(("", ""), job.github_email(FakeGitHub(), "correctperson"))

    def test_no_live_network_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            output = str(Path(directory) / "results")
            with patch.object(job.Github, "get", side_effect=AssertionError("Unexpected call")):
                with patch.object(job.Github, "get_list", side_effect=AssertionError("Unexpected call")):
                    result = job.main(["--jd-text", self.jd, "--output-dir", output])
            self.assertEqual(0, result)
            summary = json.loads(Path(output, "summary.json").read_text())
            self.assertEqual(0, summary["profiles_found"])
            self.assertEqual(0, summary["github_emails_found"])
            self.assertEqual(0, summary["xray_queries_made"])
            self.assertTrue(Path(output, "boolean_xray_queries.csv").exists())

    def test_free_credits_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            rc = job.main(["--jd-text", self.jd, "--live-xray",
                           "--output-dir", str(Path(directory) / "results")])
        self.assertEqual(2, rc)


if __name__ == "__main__":
    unittest.main()
