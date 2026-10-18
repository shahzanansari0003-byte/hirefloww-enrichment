import os
import sqlite3
import stat
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from credit_budget import CreditBudget, CreditBudgetError


def _reserve_in_process(path, attempts):
    with CreditBudget(path, "2026-10", {"hunter": {"email": 23}}) as ledger:
        return sum(ledger.reserve("hunter", email=1) for _ in range(attempts))


class CreditBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "private" / "usage.sqlite3"

    def budget(self, limits=None, cycle="2026-10"):
        return CreditBudget(self.path, cycle, {} if limits is None else limits)

    def test_missing_allowance_blocks_spending(self):
        with self.budget() as ledger:
            for provider in ("prospeo", "getprospect", "hunter"):
                self.assertFalse(ledger.reserve(provider, email=1))
                self.assertFalse(ledger.reserve(provider, phone=1))
                self.assertTrue(ledger.reserve(provider))

    def test_invalid_configuration_is_rejected_without_creating_ledger(self):
        invalid = [
            {"unknown": {"email": 1}}, {"hunter": {"calls": 1}},
            {"hunter": {"email": -1}}, {"hunter": {"email": True}},
            {"hunter": {"email": 1.0}}, {"hunter": {"email": "1"}},
            {"hunter": {"phone": 2**63}}, {"hunter": None}, [],
        ]
        for limits in invalid:
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                CreditBudget(self.path, "2026-10", limits)
        for cycle in ("2026-00", "2026-13", "2026-1", "2026-10\n", "0000-01", "", None):
            with self.subTest(cycle=cycle), self.assertRaises(ValueError):
                CreditBudget(self.path, cycle, {})
        self.assertFalse(self.path.exists())

    def test_invalid_reservation_does_not_spend(self):
        with self.budget({"hunter": {"email": 1}}) as ledger:
            for value in (-1, True, 1.0, "1", None, 2**63):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    ledger.reserve("hunter", email=value)
            with self.assertRaises(ValueError):
                ledger.reserve("unknown", email=1)
            with self.assertRaises(TypeError):
                ledger.reserve("hunter", calls=1)
            self.assertTrue(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_restart_does_not_reset_reservations(self):
        limits = {"hunter": {"email": 2}}
        with self.budget(limits) as ledger:
            self.assertTrue(ledger.reserve("hunter", email=1))
        with self.budget(limits) as ledger:
            self.assertTrue(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_provider_buckets_and_cycles_are_separate(self):
        limits = {"prospeo": {"email": 2, "phone": 10}, "getprospect": {"email": 1}}
        with self.budget(limits) as ledger:
            self.assertTrue(ledger.reserve("prospeo", email=1, phone=10))
            self.assertFalse(ledger.reserve("prospeo", email=1, phone=1))
            # Rejected combined reservations consume neither bucket.
            self.assertTrue(ledger.reserve("prospeo", email=1))
            self.assertFalse(ledger.reserve("prospeo", email=1))
            self.assertTrue(ledger.reserve("getprospect", email=1))
            self.assertFalse(ledger.reserve("getprospect", phone=1))
        with self.budget(limits, cycle="2026-11") as ledger:
            self.assertTrue(ledger.reserve("prospeo", email=2, phone=10))
        with self.budget(limits) as ledger:
            self.assertFalse(ledger.reserve("prospeo", email=1))

    def test_caps_cannot_be_raised_within_cycle(self):
        with self.budget({"hunter": {"email": 2}}) as ledger:
            self.assertTrue(ledger.reserve("hunter", email=1))
        with self.budget({"hunter": {"email": 100}, "prospeo": {"email": 100}}) as ledger:
            self.assertTrue(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("prospeo", email=1))
        with self.budget({"hunter": {"email": 1}}) as ledger:
            self.assertFalse(ledger.reserve("hunter", email=1))
        with self.budget({"hunter": {"email": 100}}) as ledger:
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_lowering_caps_applies_to_an_already_open_connection(self):
        with self.budget({"hunter": {"email": 100}}) as first:
            with self.budget({"hunter": {"email": 1}}):
                self.assertTrue(first.reserve("hunter", email=1))
                self.assertFalse(first.reserve("hunter", email=1))

    def test_failed_operation_does_not_refund_reservation(self):
        with self.assertRaises(RuntimeError):
            with self.budget({"hunter": {"email": 1}}) as ledger:
                self.assertTrue(ledger.reserve("hunter", email=1))
                raise RuntimeError("simulated provider failure")
        with self.budget({"hunter": {"email": 1}}) as ledger:
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_concurrent_processes_cannot_exceed_shared_cap(self):
        with self.budget({"hunter": {"email": 23}}):
            pass
        with ProcessPoolExecutor(max_workers=4) as executor:
            counts = list(executor.map(_reserve_in_process, [str(self.path)] * 8, [10] * 8))
        self.assertEqual(sum(counts), 23)
        with self.budget({"hunter": {"email": 23}}) as ledger:
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_malformed_database_fails_closed(self):
        self.path.parent.mkdir()
        self.path.write_bytes(b"not a SQLite database")
        with self.assertRaises(CreditBudgetError):
            self.budget({"hunter": {"email": 1}})

    def test_existing_empty_database_fails_closed(self):
        self.path.parent.mkdir()
        self.path.touch()
        with self.assertRaises(CreditBudgetError):
            self.budget({"hunter": {"email": 1}})

    def test_unrecognized_schema_fails_closed(self):
        with self.budget():
            pass
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE unexpected (value TEXT)")
        with self.assertRaises(CreditBudgetError):
            self.budget()

    def test_incomplete_or_negative_state_fails_closed(self):
        with self.budget({"hunter": {"email": 1}}) as ledger:
            with sqlite3.connect(self.path) as connection:
                connection.execute("DELETE FROM credit_budgets WHERE provider = 'prospeo'")
            with self.assertRaises(CreditBudgetError):
                ledger.reserve("hunter", email=1)
        with self.assertRaises(CreditBudgetError):
            self.budget()

    def test_invalid_numeric_state_fails_closed(self):
        with self.budget({"hunter": {"email": 1}}) as ledger:
            with sqlite3.connect(self.path) as connection:
                connection.execute("PRAGMA ignore_check_constraints = ON")
                connection.execute("UPDATE credit_budgets SET email_used = -1 WHERE provider = 'hunter'")
            with self.assertRaises(CreditBudgetError):
                ledger.reserve("hunter", email=1)

    def test_deleted_cycle_usage_does_not_reset_allowance(self):
        limits = {"hunter": {"email": 1}}
        with self.budget(limits) as ledger:
            self.assertTrue(ledger.reserve("hunter", email=1))
        with self.budget(limits, cycle="2026-11"):
            pass
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM credit_budgets WHERE cycle = '2026-10'")
        with self.assertRaises(CreditBudgetError):
            self.budget(limits)

    def test_empty_initialized_state_does_not_reset_allowance(self):
        with self.budget({"hunter": {"email": 1}}):
            pass
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM credit_budgets")
            connection.execute("DELETE FROM credit_cycles")
        with self.assertRaises(CreditBudgetError):
            self.budget({"hunter": {"email": 1}})

    def test_closed_ledger_blocks_reservations(self):
        ledger = self.budget()
        ledger.close()
        ledger.close()
        with self.assertRaises(CreditBudgetError):
            ledger.reserve("hunter", email=1)

    @unittest.skipUnless(os.name == "posix", "POSIX permission modes")
    def test_private_file_and_new_directories_preserve_existing_parent_mode(self):
        parent = Path(self.directory.name)
        parent.chmod(0o755)
        with self.budget():
            pass
        self.assertEqual(stat.S_IMODE(parent.stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    @unittest.skipUnless(hasattr(os, "symlink"), "Symbolic links unavailable")
    def test_symlink_ledger_is_rejected(self):
        self.path.parent.mkdir()
        target = self.path.parent / "target"
        target.write_bytes(b"unchanged")
        self.path.symlink_to(target)
        with self.assertRaises(CreditBudgetError):
            self.budget()
        self.assertEqual(target.read_bytes(), b"unchanged")


if __name__ == "__main__":
    unittest.main()
