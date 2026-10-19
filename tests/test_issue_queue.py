"""Offline checks for private GitHub candidate-queue intake."""
import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import issue_queue

class CandidateQueueTests(unittest.TestCase):
    def test_parser_canonicalizes_profiles(self):
        issue = ("~~~csv\nfull_name,linkedin_url,company_name\n"
                 "Candidate A,https://www.linkedin.com/in/person-a/?trk=foo,Example\n"
                 "Candidate B,https://linkedin.com/in/person-b,Another\n~~~")
        rows = issue_queue.parse_issue(issue)
        self.assertEqual(2, len(rows))
        self.assertEqual("https://www.linkedin.com/in/person-a", rows[0]["linkedin_url"])

    def test_rejects_non_profile_urls(self):
        for url in ["http://www.linkedin.com/in/name",
                    "https://www.linkedin.com.evil.test/in/name",
                    "https://www.linkedin.com/company/name",
                    "https://www.linkedin.com/in/foo/../../etc"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                issue_queue.canonical_url(url)

    def test_does_not_accept_emails_in_issue(self):
        with self.assertRaises(ValueError):
            issue_queue.parse_issue("~~~csv\nlinkedin_url,email\nhttps://www.linkedin.com/in/person,foo@example.com\n~~~")

    def test_queue_limit(self):
        data = "\n".join(f"Candidate {i},https://www.linkedin.com/in/person-{i},Company" for i in range(51))
        with self.assertRaises(ValueError):
            issue_queue.parse_issue("~~~csv\nfull_name,linkedin_url,company_name\n" + data + "\n~~~")

    def test_requires_single_csv_block(self):
        with self.assertRaises(ValueError):
            issue_queue.parse_issue("No candidate CSV")

if __name__ == "__main__":
    unittest.main()
