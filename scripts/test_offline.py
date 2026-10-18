#!/usr/bin/env python3
"""Run the complete local check suite with network access blocked."""
from __future__ import annotations

import csv
import os
from pathlib import Path
import py_compile
import runpy
import socket
import sys
import tempfile
import unittest


def deny_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Network access is disabled during offline checks")


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    for variable in ("PROSPEO_API_KEY", "GETPROSPECT_API_KEY", "HUNTER_API_KEY"):
        os.environ.pop(variable, None)
    socket.create_connection = deny_network
    socket.getaddrinfo = deny_network
    socket.socket.connect = deny_network
    socket.socket.connect_ex = deny_network

    suite = unittest.defaultTestLoader.discover(str(root / "tests"))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    sources = [*sorted(root.glob("*.py")),
               *sorted((root / "tests").rglob("*.py")),
               *sorted((root / "scripts").rglob("*.py"))]
    for source in sources:
        py_compile.compile(str(source), doraise=True)

    with tempfile.TemporaryDirectory(prefix="hirefloww-offline-") as temporary:
        output = Path(temporary) / "dryrun.csv"
        sys.argv = ["enrich.py", "--input", "sample/leads.csv", "--output", str(output)]
        try:
            runpy.run_path(str(root / "enrich.py"), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                raise
        with output.open(newline="", encoding="utf-8") as source:
            rows = list(csv.DictReader(source))
        assert rows, "Sample dry run produced no rows"
        assert all(row["enrichment_status"] in {
            "dry_run_no_credits_used", "duplicate_skipped"
        } for row in rows), "Sample dry run unexpectedly attempted enrichment"
        assert any(row["enrichment_status"] == "duplicate_skipped" for row in rows), (
            "Sample dry run did not detect the synthetic duplicate"
        )
    print("Offline checks passed: unit tests, compilation and synthetic sample dry run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
