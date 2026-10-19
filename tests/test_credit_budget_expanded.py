"""Migration and reservation coverage for the expanded provider credit ledger."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

import credit_budget
from credit_budget import CreditBudget, CreditBudgetError


LEGACY_PROVIDERS = {"prospeo", "getprospect", "hunter"}
NEW_PROVIDERS = {
    "tomba", "dropcontact", "apollo", "snov", "abstract", "zerobounce",
    "emailable", "zerobounce_ai",
}
LIMITS = {
    "prospeo": {"email": 4, "phone": 10},
    "getprospect": {"email": 5, "phone": 2},
    "hunter": {"email": 3},
}


def _migration_worker(path):
    limits = {**LIMITS, "tomba": {"email": 7}}
    with CreditBudget(path, "2026-10", limits) as ledger:
        return sum(ledger.reserve("tomba", email=1) for _ in range(5))


class ExpandedCreditBudgetTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "credits.sqlite3"

    def create_legacy(self, cycles=("2026-09", "2026-10")):
        """Build an actual version-1 ledger, without using the new initializer."""
        with sqlite3.connect(self.path) as connection:
            connection.execute(credit_budget._META_SQL)
            connection.execute("INSERT INTO ledger_meta VALUES (1, 1)")
            connection.execute(credit_budget._CYCLES_SQL)
            connection.execute(credit_budget._BUDGET_SQL)
            for cycle in cycles:
                connection.execute("INSERT INTO credit_cycles VALUES (?)", (cycle,))
                for provider in sorted(LEGACY_PROVIDERS):
                    caps = LIMITS[provider]
                    connection.execute(
                        "INSERT INTO credit_budgets VALUES (?, ?, ?, ?, ?, ?)",
                        (cycle, provider, caps["email"], caps.get("phone", 0),
                         2, 1 if caps.get("phone") else 0),
                    )

    def snapshot(self):
        with sqlite3.connect(self.path) as connection:
            return (
                connection.execute("SELECT * FROM ledger_meta").fetchall(),
                connection.execute("SELECT * FROM credit_cycles ORDER BY cycle").fetchall(),
                connection.execute("SELECT * FROM credit_budgets ORDER BY cycle, provider").fetchall(),
                connection.execute("SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall(),
            )

    def test_fresh_ledger_supports_all_eleven_providers(self):
        self.assertEqual(credit_budget.PROVIDERS, LEGACY_PROVIDERS | NEW_PROVIDERS)
        caps = {provider: {"email": 1, "phone": 1} for provider in NEW_PROVIDERS}
        with CreditBudget(self.path, "2026-10", caps) as ledger:
            for provider in sorted(NEW_PROVIDERS):
                with self.subTest(provider=provider):
                    self.assertTrue(ledger.reserve(provider, email=1, phone=1))
                    self.assertFalse(ledger.reserve(provider, email=1))
                    self.assertFalse(ledger.reserve(provider, phone=1))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT schema_version FROM ledger_meta").fetchone(), (2,))

    def test_migration_preserves_every_old_cap_and_usage(self):
        self.create_legacy()
        old_rows = self.snapshot()[2]
        with CreditBudget(self.path, "2026-10", LIMITS):
            pass
        with sqlite3.connect(self.path) as connection:
            old_after = connection.execute(
                "SELECT * FROM credit_budgets WHERE provider IN ('hunter', 'prospeo', 'getprospect') "
                "ORDER BY cycle, provider"
            ).fetchall()
            new_rows = connection.execute(
                "SELECT * FROM credit_budgets WHERE provider NOT IN ('hunter', 'prospeo', 'getprospect')"
            ).fetchall()
            self.assertEqual(connection.execute("SELECT * FROM ledger_meta").fetchall(), [(1, 2)])
        self.assertEqual(old_after, old_rows)
        self.assertEqual(len(new_rows), 16)
        self.assertTrue(all(row[2:] == (0, 0, 0, 0) for row in new_rows))

    def test_existing_caps_cannot_be_raised_during_migration(self):
        self.create_legacy()
        with CreditBudget(self.path, "2026-10", {**LIMITS, "hunter": {"email": 100}}) as ledger:
            self.assertTrue(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("hunter", email=1))
        with CreditBudget(self.path, "2026-10", {**LIMITS, "hunter": {"email": 200}}) as ledger:
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_new_caps_initialize_once_for_opened_cycle(self):
        self.create_legacy()
        limits = {**LIMITS, "tomba": {"email": 2}, "apollo": {"phone": 1}}
        with CreditBudget(self.path, "2026-10", limits) as ledger:
            self.assertTrue(ledger.reserve("tomba", email=1))
            self.assertTrue(ledger.reserve("apollo", phone=1))
            self.assertFalse(ledger.reserve("emailable", email=1))
        raised = {**limits, "tomba": {"email": 100}, "apollo": {"phone": 100},
                  "emailable": {"email": 100}}
        with CreditBudget(self.path, "2026-10", raised) as ledger:
            self.assertTrue(ledger.reserve("tomba", email=1))
            self.assertFalse(ledger.reserve("tomba", email=1))
            self.assertFalse(ledger.reserve("apollo", phone=1))
            self.assertFalse(ledger.reserve("emailable", email=1))
        with CreditBudget(self.path, "2026-09", raised) as ledger:
            # Migration does not enable new providers in historical cycles.
            self.assertFalse(ledger.reserve("tomba", email=1))
            self.assertFalse(ledger.reserve("apollo", phone=1))

    def test_migration_to_new_cycle_keeps_old_cycles_and_allows_new_caps(self):
        self.create_legacy(cycles=("2026-09",))
        old_rows = self.snapshot()[2]
        with CreditBudget(self.path, "2026-10", {**LIMITS, "snov": {"email": 2}}) as ledger:
            self.assertTrue(ledger.reserve("snov", email=2))
            self.assertFalse(ledger.reserve("snov", email=1))
            self.assertTrue(ledger.reserve("hunter", email=3))
        with sqlite3.connect(self.path) as connection:
            retained = connection.execute(
                "SELECT * FROM credit_budgets WHERE cycle = '2026-09' "
                "AND provider IN ('hunter', 'prospeo', 'getprospect') ORDER BY cycle, provider"
            ).fetchall()
        self.assertEqual(retained, old_rows)

    def test_partial_legacy_cycle_is_rejected_without_changes(self):
        self.create_legacy()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM credit_budgets WHERE cycle = '2026-09' AND provider = 'hunter'")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", {**LIMITS, "tomba": {"email": 2}})
        self.assertEqual(self.snapshot(), before)

    def test_completely_deleted_legacy_cycle_is_rejected(self):
        self.create_legacy()
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM credit_budgets WHERE cycle = '2026-09'")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", LIMITS)
        self.assertEqual(self.snapshot(), before)

    def test_corrupt_legacy_counts_are_rejected_without_migration(self):
        self.create_legacy()
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE credit_budgets SET email_used = -1 WHERE provider = 'hunter'")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", LIMITS)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_unknown_provider_is_not_adopted(self):
        self.create_legacy()
        with sqlite3.connect(self.path) as connection:
            connection.execute("UPDATE credit_budgets SET provider = 'tomba' WHERE provider = 'hunter'")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", LIMITS)
        self.assertEqual(self.snapshot(), before)

    def test_legacy_modified_schema_is_rejected(self):
        self.create_legacy()
        with sqlite3.connect(self.path) as connection:
            connection.execute("ALTER TABLE credit_budgets ADD COLUMN extra TEXT")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", LIMITS)
        self.assertEqual(self.snapshot(), before)

    def test_unknown_schema_version_is_rejected(self):
        self.create_legacy()
        with sqlite3.connect(self.path) as connection:
            connection.execute("PRAGMA ignore_check_constraints = ON")
            connection.execute("UPDATE ledger_meta SET schema_version = 9")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", LIMITS)
        self.assertEqual(self.snapshot(), before)

    def test_failed_migration_rolls_back_schema_rows_and_limits(self):
        self.create_legacy()
        before = self.snapshot()
        migrate = CreditBudget._migrate_v1

        def fail_after_changes(ledger, limits):
            migrate(ledger, limits)
            raise CreditBudgetError("simulated interrupted migration")

        with patch.object(CreditBudget, "_migrate_v1", fail_after_changes):
            with self.assertRaises(CreditBudgetError):
                CreditBudget(self.path, "2026-10", {**LIMITS, "tomba": {"email": 2}})
        self.assertEqual(self.snapshot(), before)
        # A subsequent clean opening can complete the same migration safely.
        with CreditBudget(self.path, "2026-10", {**LIMITS, "tomba": {"email": 2}}) as ledger:
            self.assertTrue(ledger.reserve("tomba", email=2))
            self.assertTrue(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_concurrent_migrations_and_reservations_share_new_provider_cap(self):
        self.create_legacy()
        with ProcessPoolExecutor(max_workers=4) as executor:
            counts = list(executor.map(_migration_worker, [str(self.path)] * 8))
        self.assertEqual(sum(counts), 7)
        with CreditBudget(self.path, "2026-10", {**LIMITS, "tomba": {"email": 7}}) as ledger:
            self.assertFalse(ledger.reserve("tomba", email=1))
            self.assertTrue(ledger.reserve("hunter", email=1))
            self.assertFalse(ledger.reserve("hunter", email=1))

    def test_partial_expanded_cycle_is_rejected_without_repair(self):
        with CreditBudget(self.path, "2026-10", {"tomba": {"email": 2}}):
            pass
        with sqlite3.connect(self.path) as connection:
            connection.execute("DELETE FROM credit_budgets WHERE provider = 'abstract'")
        before = self.snapshot()
        with self.assertRaises(CreditBudgetError):
            CreditBudget(self.path, "2026-10", {"tomba": {"email": 2}})
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
