#!/usr/bin/env python3
"""Retrieve existing asynchronous jobs with explicit live and credit limits.

This command never submits a new enrichment job or retries a request.
"""
from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import enrich
from credit_budget import CreditBudget, CreditBudgetError
from provider_registry import REGISTRY


def read_pending(task: str) -> enrich.Contact:
    import expanded_providers
    if not enrich.valid_pending(task):
        raise enrich.ProviderError("Invalid pending task identifier")
    return expanded_providers.read_pending(task, request=enrich.request)


def run(rows: list[dict[str, str]], *, live: bool, budget: CreditBudget | None,
        max_calls: int = 10, max_http_requests: int = 20,
        approved_providers: set[str] | None = None,
        stats: dict[str, int] | None = None,
        request_credit_caps: dict[str, int] | None = None) -> tuple[list[dict[str, str]], int]:
    if type(max_calls) is not int or max_calls < 0:
        raise ValueError("Job retrieval cap must be nonnegative")
    if live and budget is None:
        raise ValueError("Live job retrieval requires a persistent credit budget")
    approved_providers = set() if approved_providers is None else set(approved_providers)
    if approved_providers - REGISTRY.keys():
        raise ValueError("Unknown approved provider")
    request_credit_caps = enrich._request_caps(request_credit_caps)
    limits = enrich.ExecutionLimits(max_http_requests, stats=stats)
    output, seen = [], set()
    calls = 0
    for source in rows:
        row = {key: str(source.get(key, "") or "").strip() for key in enrich.FIELDS}
        # Do not propagate arbitrary exception text from old CSV exports.
        row["errors"] = ""
        for field in ("email", "phone"):
            row[field + "_source"] = "input" if row[field] else ""
            row[field + "_status"] = "input_unverified" if row[field] else ""
        for field in ("verification_source", "verification_status", "risk_source", "risk_status"):
            row[field] = ""
        task = row["pending_task"]
        if not task:
            output.append(row)
            continue
        if not enrich.valid_pending(task):
            row["enrichment_status"] = "invalid_pending_task"
            output.append(row)
            continue
        if task in seen:
            row["enrichment_status"] = "duplicate_pending_skipped"
            output.append(row)
            continue
        seen.add(task)
        if not live:
            row["enrichment_status"] = "dry_run_no_credits_used"
            output.append(row)
            continue
        provider = task.split(":", 1)[0]
        spec = REGISTRY[provider]
        if (calls >= max_calls or not limits.can_request(spec.requests) or not enrich.configured(provider)
                or (spec.requires_approval and provider not in approved_providers)):
            row["enrichment_status"] = "credit_ledger_error" if limits.ledger_failed else "pending"
            if limits.ledger_failed:
                row["errors"] = "credit_ledger:unavailable"
            output.append(row)
            continue
        assert budget is not None
        # Polls may be included in the original charge, but never assume a new
        # account's retrieval is free. Reserve a conservative local unit.
        cost = request_credit_caps.get(provider, 1 if provider == "dropcontact" else None)
        if cost is None:
            row["enrichment_status"] = "pending"
            row["errors"] = provider + ":request_credit_cap_required"
            output.append(row)
            continue
        if not limits.reserve(budget, provider, email=cost):
            row["enrichment_status"] = "credit_ledger_error" if limits.ledger_failed else "pending"
            if limits.ledger_failed:
                row["errors"] = "credit_ledger:unavailable"
            output.append(row)
            continue
        calls += 1
        limits.stats["calls"] = calls
        try:
            contact = limits.invoke(read_pending, task)
            if contact.pending_task:
                if contact.pending_task != task:
                    raise enrich.ProviderError("Unexpected pending task identifier")
                row["enrichment_status"] = "pending"
            else:
                row["pending_task"] = ""
                if contact.email and enrich.EMAIL.fullmatch(contact.email) and "*" not in contact.email and not row["email"]:
                    row.update(email=contact.email, email_source=provider,
                               email_status=contact.email_status or "unverified", enrichment_status="enriched")
                else:
                    row["enrichment_status"] = "already_present" if row["email"] else "attempted_no_match"
        except enrich.ProviderError:
            row["enrichment_status"] = "pending"
            row["errors"] = provider + ":request_failed"
        output.append(row)
    return output, calls


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--live", action="store_true", help="Explicitly retrieve existing jobs; default offline")
    parser.add_argument("--credit-cycle")
    parser.add_argument("--credit-ledger", default=".private/credits.sqlite3")
    parser.add_argument("--email-credits", action="append", default=[], metavar="PROVIDER=N")
    parser.add_argument("--phone-credits", action="append", default=[], metavar="PROVIDER=N")
    parser.add_argument("--max-calls", type=int, default=10)
    parser.add_argument("--max-http-requests", type=int, default=20)
    parser.add_argument("--request-credit-cap", action="append", default=[], metavar="PROVIDER=N")
    parser.add_argument("--approved-provider", action="append", default=[], choices=sorted(REGISTRY))
    args = parser.parse_args(argv)
    if args.live and not args.credit_cycle:
        parser.error("--live requires --credit-cycle YYYY-MM and explicit free-credit allocations")
    if min(args.max_calls, args.max_http_requests) < 0:
        parser.error("Request caps cannot be negative")
    try:
        email, phone = enrich._caps(args.email_credits), enrich._caps(args.phone_credits)
        request_credit_caps = enrich._request_caps(enrich._caps(args.request_credit_cap))
    except ValueError as exc:
        parser.error(str(exc))
    limits = {name: {"email": email.get(name, 0), "phone": phone.get(name, 0)} for name in REGISTRY}
    budget = None
    created = False
    dest = Path(args.output)
    stats: dict[str, int] = {}
    try:
        with open(args.input, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "pending_task" not in reader.fieldnames:
                parser.error("Input CSV needs a pending_task heading")
            rows = list(reader)
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            if args.live:
                budget = CreditBudget(args.credit_ledger, args.credit_cycle, limits)
            results, calls = run(rows, live=args.live, budget=budget, max_calls=args.max_calls,
                                 max_http_requests=args.max_http_requests,
                                 approved_providers=set(args.approved_provider), stats=stats,
                                 request_credit_caps=request_credit_caps)
            writer = csv.DictWriter(handle, fieldnames=enrich.FIELDS)
            writer.writeheader()
            writer.writerows({key: enrich._csv_safe(value) for key, value in row.items()} for row in results)
    except FileExistsError:
        parser.exit(2, "Output already exists; choose a new output path. No retrieval was started.\n")
    except (OSError, ValueError, CreditBudgetError):
        if created:
            dest.unlink(missing_ok=True)
        parser.exit(2, "Unable to process input/output or credit ledger; check configuration and permissions.\n")
    finally:
        if budget is not None:
            budget.close()
    print(f"Wrote {len(results)} rows; retrieved {calls} jobs; HTTP requests={stats.get('http_requests', 0)}; live={args.live}")
    return 2 if stats.get("ledger_errors") else 0


if __name__ == "__main__":
    raise SystemExit(main())
