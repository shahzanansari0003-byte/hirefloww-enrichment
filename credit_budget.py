"""Conservative, persistent credit reservations; no contact data is stored.

Use one ledger path across every enrichment run. Caps are pinned when a cycle
is first opened and can only decrease during that cycle. Reservations are never
refunded, including after failed or unsuccessful provider requests. This local
ledger cannot account for usage outside this application.
"""
from __future__ import annotations

import os
import re
import sqlite3
import stat
from pathlib import Path

from provider_registry import REGISTRY


_LEGACY_PROVIDERS = frozenset({"prospeo", "getprospect", "hunter"})
PROVIDERS = frozenset(REGISTRY)
BUCKETS = frozenset({"email", "phone"})
MAX_CREDITS = 2**63 - 1
_CYCLE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\Z")
_META_SQL = (
    "CREATE TABLE ledger_meta (id INTEGER PRIMARY KEY CHECK (id = 1), "
    "schema_version INTEGER NOT NULL CHECK (schema_version = 1))"
)
_META_V2_SQL = (
    "CREATE TABLE ledger_meta (id INTEGER PRIMARY KEY CHECK (id = 1), "
    "schema_version INTEGER NOT NULL CHECK (schema_version = 2))"
)
_CYCLES_SQL = "CREATE TABLE credit_cycles (cycle TEXT NOT NULL PRIMARY KEY)"
_BUDGET_SQL = (
    "CREATE TABLE credit_budgets (cycle TEXT NOT NULL, provider TEXT NOT NULL, "
    "email_limit INTEGER NOT NULL CHECK (typeof(email_limit) = 'integer' AND email_limit >= 0), "
    "phone_limit INTEGER NOT NULL CHECK (typeof(phone_limit) = 'integer' AND phone_limit >= 0), "
    "email_used INTEGER NOT NULL CHECK (typeof(email_used) = 'integer' AND email_used >= 0), "
    "phone_used INTEGER NOT NULL CHECK (typeof(phone_used) = 'integer' AND phone_used >= 0), "
    "PRIMARY KEY (cycle, provider))"
)


class CreditBudgetError(RuntimeError):
    """Ledger failure: do not initiate any provider request."""


def _valid_count(value: object) -> bool:
    # bool is an int subclass but is almost certainly a configuration mistake.
    return type(value) is int and 0 <= value <= MAX_CREDITS


def _valid_cycle(cycle: object) -> bool:
    return isinstance(cycle, str) and bool(_CYCLE.fullmatch(cycle)) and not cycle.startswith("0000-")


class CreditBudget:
    """Atomically reserve email and phone credits for an explicit billing cycle.

    Missing providers or buckets have zero allowance. Use the provider's actual
    free-credit reset cycle, rather than rotating cycle values to reset usage.
    The SQLite database must remain available and must not be deleted between
    runs. Invalid, corrupt, or inaccessible ledgers raise CreditBudgetError.
    Version-1 ledgers migrate atomically: existing usage and caps survive, while
    newly supported providers receive configured caps only for the opened cycle.
    Missing caps remain zero, and historical cycles receive zero new allowances.
    """

    def __init__(self, path: str | Path, cycle: str,
                 limits: dict[str, dict[str, int]]) -> None:
        if not _valid_cycle(cycle):
            raise ValueError("Credit cycle must be a valid YYYY-MM value")
        if not isinstance(limits, dict) or set(limits) - PROVIDERS:
            raise ValueError("Unknown credit provider or invalid credit limits")
        for buckets in limits.values():
            if (not isinstance(buckets, dict) or set(buckets) - BUCKETS
                    or any(not _valid_count(value) for value in buckets.values())):
                raise ValueError("Credit limits must contain nonnegative integer email/phone caps")
        self.cycle = cycle
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        created = False
        try:
            self._create_private_parents(self.path.parent)
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                if self.path.is_symlink():
                    raise CreditBudgetError("Credit ledger must not be a symbolic link")
                fd = os.open(self.path, flags)
            try:
                if not stat.S_ISREG(os.fstat(fd).st_mode):
                    raise CreditBudgetError("Credit ledger must be a regular file")
                os.fchmod(fd, 0o600)
            finally:
                os.close(fd)
            self._connection = sqlite3.connect(self.path, timeout=15, isolation_level=None)
            self._connection.execute("PRAGMA trusted_schema = OFF")
            self._connection.execute("BEGIN IMMEDIATE")
            if created:
                self._connection.execute(_META_V2_SQL)
                self._connection.execute("INSERT INTO ledger_meta VALUES (1, 2)")
                self._connection.execute(_CYCLES_SQL)
                self._connection.execute(_BUDGET_SQL)
            elif self._connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'ledger_meta'"
            ).fetchone() == (_META_SQL,):
                self._validate(version=1)
                self._migrate_v1(limits)
            self._validate(allow_empty=created)
            self._connection.execute("INSERT OR IGNORE INTO credit_cycles VALUES (?)", (cycle,))
            for provider in sorted(PROVIDERS):
                cap = limits.get(provider, {})
                self._connection.execute(
                    "INSERT INTO credit_budgets VALUES (?, ?, ?, ?, 0, 0) "
                    "ON CONFLICT(cycle, provider) DO UPDATE SET "
                    "email_limit = MIN(email_limit, excluded.email_limit), "
                    "phone_limit = MIN(phone_limit, excluded.phone_limit)",
                    (cycle, provider, cap.get("email", 0), cap.get("phone", 0)),
                )
            self._validate()
            self._connection.execute("COMMIT")
        except (OSError, sqlite3.Error, CreditBudgetError):
            self.close()
            # No underlying exception details: filesystem paths are deployment data.
            raise CreditBudgetError("Credit ledger unavailable or invalid; enrichment blocked") from None

    @staticmethod
    def _create_private_parents(directory: Path) -> None:
        """Create only missing parents with mode 0700; preserve existing modes."""
        missing = []
        current = directory
        while not current.exists():
            missing.append(current)
            if current == current.parent:
                break
            current = current.parent
        for parent in reversed(missing):
            try:
                parent.mkdir(mode=0o700)
            except FileExistsError:
                if not parent.is_dir():
                    raise CreditBudgetError("Credit ledger parent is not a directory")

    def _migrate_v1(self, limits: dict[str, dict[str, int]]) -> None:
        """Upgrade inside the constructor's write transaction without resetting usage."""
        connection = self._require_connection()
        cycles = [row[0] for row in connection.execute("SELECT cycle FROM credit_cycles")]
        for cycle in cycles:
            for provider in sorted(PROVIDERS - _LEGACY_PROVIDERS):
                cap = limits.get(provider, {}) if cycle == self.cycle else {}
                connection.execute(
                    "INSERT INTO credit_budgets VALUES (?, ?, ?, ?, 0, 0)",
                    (cycle, provider, cap.get("email", 0), cap.get("phone", 0)),
                )
        connection.execute("DROP TABLE ledger_meta")
        connection.execute(_META_V2_SQL)
        connection.execute("INSERT INTO ledger_meta VALUES (1, 2)")

    def _validate(self, *, allow_empty: bool = False, version: int = 2) -> None:
        connection = self._require_connection()
        expected_providers = _LEGACY_PROVIDERS if version == 1 else PROVIDERS
        schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if set(schema) != {
            ("table", "ledger_meta", _META_SQL if version == 1 else _META_V2_SQL),
            ("table", "credit_cycles", _CYCLES_SQL),
            ("table", "credit_budgets", _BUDGET_SQL),
        }:
            raise CreditBudgetError("Invalid credit ledger schema")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise CreditBudgetError("Invalid credit ledger database")
        if connection.execute("SELECT id, schema_version FROM ledger_meta").fetchall() != [(1, version)]:
            raise CreditBudgetError("Invalid credit ledger version")
        stored_cycles = {row[0] for row in connection.execute("SELECT cycle FROM credit_cycles")}
        if any(not _valid_cycle(cycle) for cycle in stored_cycles) or (not stored_cycles and not allow_empty):
            raise CreditBudgetError("Invalid credit ledger cycles")
        rows = connection.execute("SELECT * FROM credit_budgets").fetchall()
        cycles: dict[str, set[str]] = {}
        for cycle, provider, *counts in rows:
            if (not _valid_cycle(cycle) or provider not in expected_providers
                    or not all(_valid_count(value) for value in counts)):
                raise CreditBudgetError("Invalid credit ledger state")
            cycles.setdefault(cycle, set()).add(provider)
        # Every initialized cycle has all providers. Partial deletion must not reset caps.
        if set(cycles) != stored_cycles or any(providers != expected_providers for providers in cycles.values()):
            raise CreditBudgetError("Incomplete credit ledger state")

    def _require_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CreditBudgetError("Credit ledger is closed; enrichment blocked")
        return self._connection

    def reserve(self, provider: str, *, email: int = 0, phone: int = 0) -> bool:
        """Reserve both buckets together, returning False if either cap is exceeded."""
        if provider not in PROVIDERS:
            raise ValueError("Unknown credit provider")
        if not _valid_count(email) or not _valid_count(phone):
            raise ValueError("Credit reservations must be nonnegative integers")
        connection = self._require_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._validate()
            row = connection.execute(
                "SELECT email_limit, phone_limit, email_used, phone_used "
                "FROM credit_budgets WHERE cycle = ? AND provider = ?",
                (self.cycle, provider),
            ).fetchone()
            if row is None:
                raise CreditBudgetError("Missing credit ledger state")
            email_limit, phone_limit, email_used, phone_used = row
            if email > email_limit - email_used or phone > phone_limit - phone_used:
                connection.execute("ROLLBACK")
                return False
            connection.execute(
                "UPDATE credit_budgets SET email_used = email_used + ?, "
                "phone_used = phone_used + ? WHERE cycle = ? AND provider = ?",
                (email, phone, self.cycle, provider),
            )
            connection.execute("COMMIT")
            return True
        except (sqlite3.Error, CreditBudgetError):
            if connection.in_transaction:
                connection.rollback()
            raise CreditBudgetError("Credit ledger unavailable or invalid; enrichment blocked") from None

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> CreditBudget:
        self._require_connection()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
