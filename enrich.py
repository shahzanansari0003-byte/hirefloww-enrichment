#!/usr/bin/env python3
"""Hirefloww: opt-in B2B contact enrichment via official provider APIs.

Python 3.10+. No third-party dependencies. LIVE calls can consume provider credits.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from credit_budget import CreditBudget, CreditBudgetError
from provider_registry import DEFAULT_PROVIDERS, DEFAULT_VALIDATORS, REGISTRY
from provider_types import Contact, ProviderError

FIELDS = ["full_name", "first_name", "last_name", "linkedin_url", "company_name", "company_domain", "email", "phone", "email_status", "phone_status", "email_source", "phone_source", "verification_source", "verification_status", "risk_source", "risk_status", "pending_task", "enrichment_status", "errors"]
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Provider credentials must never follow a redirect to another destination.
urlopen = build_opener(_NoRedirect).open

_HTTP_GUARD: ContextVar[Any] = ContextVar("hirefloww_http_guard", default=None)


def request(method: str, url: str, *, headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None, body: dict[str, Any] | None = None,
            form: dict[str, str] | None = None, timeout: int = 20) -> dict[str, Any]:
    guard = _HTTP_GUARD.get()
    if guard is not None:
        guard()
    if urlsplit(url).scheme != "https":
        raise ProviderError("Provider requests require HTTPS")
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    if body is not None and form is not None:
        raise ProviderError("Choose JSON or form request body")
    data = json.dumps(body).encode() if body is not None else urlencode(form).encode() if form is not None else None
    content_type = "application/x-www-form-urlencoded" if form is not None else "application/json"
    req = Request(url, data=data, method=method, headers={"Accept":"application/json", "Content-Type":content_type, **(headers or {})})
    try:
        with urlopen(req, timeout=timeout) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ProviderError("API response too large")
            result = json.loads(raw)
            if not isinstance(result, dict):
                raise ProviderError("API response must be a JSON object")
            return result
    except HTTPError as exc:
        # Do not log provider response bodies, to prevent leaking contact details.
        if exc.code == 404:
            return {"_not_found": True}
        if exc.code == 400 and urlsplit(url).hostname == "api.prospeo.io":
            try:
                error = json.loads(exc.read(65536))
                if isinstance(error, dict) and error.get("error_code") == "NO_MATCH":
                    return {"error": True, "error_code": "NO_MATCH"}
            except (ValueError, OSError):
                pass
        raise ProviderError(f"API returned HTTP {exc.code} (check credentials, quota and input)") from None
    except (URLError, TimeoutError, ValueError, OSError) as exc:
        raise ProviderError(f"Network/JSON error: {type(exc).__name__}") from None


def _object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ProviderError("Unexpected provider response format")
    return value


def _visible(value: Any) -> str:
    return value.strip() if isinstance(value, str) and "*" not in value else ""


def name_parts(row: dict[str, str]) -> tuple[str, str]:
    a, b = row.get("first_name", "").strip(), row.get("last_name", "").strip()
    if not a and not b:
        words = row.get("full_name", "").strip().split()
        if len(words) >= 2:
            a, b = words[0], " ".join(words[1:])
    return a, b


def lookup(row: dict[str, str], *, prospeo: bool) -> dict[str, str]:
    first, last = name_parts(row)
    d: dict[str, str] = {}
    mappings = {"linkedin_url":"linkedin_url", "email":"email", "company_name":"company_name", "company_domain": "company_website" if prospeo else "company_domain"}
    for src, dst in mappings.items():
        if row.get(src, "").strip():
            d[dst] = row[src].strip()
    if first and last:
        d["first_name"] = first
        d["last_name"] = last
    return d


def has_identity(row: dict[str, str]) -> bool:
    first, last = name_parts(row)
    return bool(row.get("linkedin_url") or row.get("email") or (first and last and (row.get("company_domain") or row.get("company_name"))))


def prospeo(row: dict[str, str], phone: bool) -> Contact:
    key = os.getenv("PROSPEO_API_KEY", "")
    if not key:
        raise ProviderError("PROSPEO_API_KEY missing")
    p = lookup(row, prospeo=True)
    if row.get("full_name") and not (p.get("first_name") and p.get("last_name")):
        p["full_name"] = row["full_name"]
    if not (p.get("linkedin_url") or p.get("email") or ((p.get("first_name") and p.get("last_name") or p.get("full_name")) and (p.get("company_name") or p.get("company_website")))):
        return Contact()
    resp = request("POST", "https://api.prospeo.io/enrich-person", headers={"X-KEY": key}, body={"data": p, "only_verified_email": True, "enrich_mobile": phone})
    if resp.get("error"):
        if resp.get("error_code") == "NO_MATCH":
            return Contact()
        raise ProviderError("Prospeo API rejected request")
    person = _object(resp.get("person"))
    em, ph = _object(person.get("email")), _object(person.get("mobile"))
    email = _visible(em.get("email")) if em.get("revealed") is True and em.get("status") == "VERIFIED" else ""
    mobile = _visible(ph.get("mobile")) if phone and ph.get("revealed") is True and ph.get("status") == "VERIFIED" else ""
    return Contact(email=email, phone=mobile, email_status="provider_verified" if email else "", phone_status="provider_verified" if mobile else "")


def getprospect(row: dict[str, str], phone: bool) -> Contact:
    key = os.getenv("GETPROSPECT_API_KEY", "")
    if not key: raise ProviderError("GETPROSPECT_API_KEY missing")
    p = lookup(row, prospeo=False)
    if not (p.get("linkedin_url") or p.get("email") or (p.get("first_name") and p.get("last_name") and (p.get("company_domain") or p.get("company_name")))):
        return Contact()
    resp = request("POST", "https://api.getprospect.com/v2/people/enrich", headers={"x-api-key":key}, body={"data":p, "enrich_email":not bool(row.get("email")), "enrich_phone":phone}, timeout=95)
    if resp.get("_not_found"): return Contact()
    if resp.get("success") is False:
        errors = resp.get("errors")
        if isinstance(errors, list) and any(isinstance(e, dict) and e.get("name") == "NOT_FOUND" for e in errors):
            return Contact()
        raise ProviderError("GetProspect API rejected request")
    person = _object(resp.get("data"))
    em, ph = _object(person.get("email")), _object(person.get("mobile_phone"))
    email = _visible(em.get("address")) if em.get("revealed") is True and em.get("status") == "valid" else ""
    mobile = _visible(ph.get("number")) if phone and ph.get("revealed") is True and ph.get("status") == "found" else ""
    return Contact(email=email, phone=mobile, email_status="provider_valid" if email else "", phone_status="provider_found" if mobile else "")


def hunter(row: dict[str, str], phone: bool) -> Contact:
    key = os.getenv("HUNTER_API_KEY", "")
    if not key: raise ProviderError("HUNTER_API_KEY missing")
    domain = row.get("company_domain", "").strip()
    first, last = name_parts(row)
    if not (domain and first and last): return Contact()
    resp = request("GET", "https://api.hunter.io/v2/email-finder", params={"domain":domain,"first_name":first,"last_name":last,"api_key":key})
    if resp.get("errors"):
        raise ProviderError("Hunter API rejected request")
    data = _object(resp.get("data"))
    email = _visible(data.get("email"))
    status = _object(data.get("verification")).get("status")
    verification = {"valid": "provider_verified", "accept_all": "accept_all", "invalid": "invalid", "unknown": "unknown"}.get(status if isinstance(status, str) else "", "unverified")
    return Contact(email=email, email_status=verification if email else "")

PROVIDERS = {"prospeo": prospeo, "getprospect": getprospect, "hunter":hunter}


# Wrappers keep the original transport patch point and adapter call signatures.
def _extra(name: str, row: dict[str, str], phone: bool) -> Contact:
    import expanded_providers
    return getattr(expanded_providers, name)(row, phone, request=request)


def tomba(row: dict[str, str], phone: bool) -> Contact:
    return _extra("tomba", row, phone)


def apollo(row: dict[str, str], phone: bool) -> Contact:
    return _extra("apollo", row, phone)


def dropcontact(row: dict[str, str], phone: bool) -> Contact:
    return _extra("dropcontact", row, phone)


def snov(row: dict[str, str], phone: bool) -> Contact:
    return _extra("snov", row, phone)


def _verify(name: str, email: str) -> str:
    import expanded_providers
    return getattr(expanded_providers, "verify_" + name)(email, request=request)


def verify_abstract(email: str) -> str:
    return _verify("abstract", email)


def verify_zerobounce(email: str) -> str:
    return _verify("zerobounce", email)


def verify_emailable(email: str) -> str:
    return _verify("emailable", email)


def verify_zerobounce_ai(email: str) -> str:
    return _verify("zerobounce_ai", email)


PROVIDERS.update(tomba=tomba, apollo=apollo, dropcontact=dropcontact, snov=snov)
VALIDATORS = {"abstract": verify_abstract, "zerobounce": verify_zerobounce,
              "emailable": verify_emailable, "zerobounce_ai": verify_zerobounce_ai}


def valid_pending(task: str) -> bool:
    return bool(re.fullmatch(r"(?:dropcontact|snov):[A-Za-z0-9_-]{1,200}", task))


def _request_caps(values: dict[str, int] | None) -> dict[str, int]:
    values = {} if values is None else values
    unbounded = {name for name, spec in REGISTRY.items() if spec.kind == "discovery" and spec.email_cost is None}
    if (not isinstance(values, dict) or values.keys() - unbounded
            or any(type(value) is not int or not 0 < value <= 2**63 - 1 for value in values.values())):
        raise ValueError("Request credit caps require positive provider-confirmed ceilings for unbounded discovery providers (currently Snov)")
    return values


class ExecutionLimits:
    """Per-run HTTP and overall credit limits, shared by discovery and validation."""
    def __init__(self, max_http_requests: int, max_credit_units: int | None = None,
                 stats: dict[str, int] | None = None):
        if type(max_http_requests) is not int or max_http_requests < 0:
            raise ValueError("HTTP request cap must be nonnegative")
        if max_credit_units is not None and (type(max_credit_units) is not int or max_credit_units < 0):
            raise ValueError("Overall credit cap must be nonnegative")
        self.max_http_requests = max_http_requests
        self.max_credit_units = max_credit_units
        self.ledger_failed = False
        self.stats = {} if stats is None else stats
        self.stats.update(calls=0, http_requests=0, verifications=0,
                          reserved_credit_units=0, reserved_phone_units=0, ledger_errors=0)

    def can_request(self, amount: int) -> bool:
        return not self.ledger_failed and self.stats["http_requests"] + amount <= self.max_http_requests

    def on_http(self) -> None:
        if not self.can_request(1):
            raise ProviderError("HTTP request cap exhausted")
        self.stats["http_requests"] += 1

    def reserve(self, budget: CreditBudget, provider: str, *, email: int = 0, phone: int = 0) -> bool:
        if self.ledger_failed:
            return False
        if self.max_credit_units is not None and self.stats["reserved_credit_units"] + email + phone > self.max_credit_units:
            return False
        try:
            reserved = budget.reserve(provider, email=email, phone=phone)
        except CreditBudgetError:
            self.ledger_failed = True
            self.stats["ledger_errors"] = 1
            return False
        if not reserved:
            return False
        self.stats["reserved_credit_units"] += email + phone
        self.stats["reserved_phone_units"] += phone
        return True

    def invoke(self, fn, *args):
        token = _HTTP_GUARD.set(self.on_http)
        try:
            return fn(*args)
        finally:
            _HTTP_GUARD.reset(token)


def _domain(value: str) -> str:
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().removeprefix("www.")
        return host if re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9][a-z0-9-]{0,62}", host) else ""
    except (ValueError, UnicodeError):
        return ""


def _linkedin(value: str) -> str:
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        host = (parsed.hostname or "").lower().removeprefix("www.")
        if parsed.scheme not in ("http", "https") or host != "linkedin.com" or not parsed.path.startswith("/in/") or not parsed.path[4:].strip("/"):
            return ""
        return host + parsed.path.rstrip("/").lower()
    except ValueError:
        return ""


def identity_keys(row: dict[str, str]) -> set[str]:
    """Exact normalized identities; never merge on a name alone."""
    keys: set[str] = set()
    linkedin = row.get("linkedin_url", "").strip()
    if linkedin and _linkedin(linkedin):
        keys.add("li:" + _linkedin(linkedin))
    email = row.get("email", "").strip().lower()
    if email:
        keys.add("em:" + email)
    first, last = name_parts(row)
    company = _domain(row["company_domain"]) if row.get("company_domain") else " ".join(row.get("company_name", "").lower().split())
    if not keys and first and last and company:
        keys.add("name:" + " ".join((first + " " + last).lower().split()) + "|" + company)
    return keys


def stable_key(row: dict[str, str]) -> str:
    return sorted(identity_keys(row))[0] if identity_keys(row) else ""


def configured(name: str) -> bool:
    return all(os.getenv(key, "").strip() for key in REGISTRY[name].required_keys)


def _usable(provider: str, row: dict[str, str]) -> bool:
    if provider in {"hunter", "tomba", "snov", "dropcontact"}:
        first, last = name_parts(row)
        return bool(first and last and row["company_domain"]) or bool(provider == "dropcontact" and row["email"])
    return has_identity(row)


def run(rows: list[dict[str, str]], *, provider_names: list[str], live: bool, want_phone: bool,
        max_calls: int, max_phone_credits: int, budget: CreditBudget | None = None,
        verify: bool = False, validators: list[str] | None = None, max_verifications: int = 0,
        approved_providers: set[str] | None = None, max_http_requests: int = 20,
        max_credit_units: int | None = None, stats: dict[str, int] | None = None,
        request_credit_caps: dict[str, int] | None = None) -> tuple[list[dict[str, str]], int]:
    """Enrich with durable reservations; validation and risk checks are opt-in.

    Asynchronous jobs are submitted once. Pending rows require the explicit
    resume command, which never creates new jobs. No automatic retries occur.
    """
    if any(type(value) is not int or value < 0 for value in (max_calls, max_phone_credits, max_verifications)):
        raise ValueError("Credit/call caps cannot be negative")
    if not provider_names or len(set(provider_names)) != len(provider_names) or any(p not in PROVIDERS for p in provider_names):
        raise ValueError("Choose each supported discovery provider at most once")
    validators = list(DEFAULT_VALIDATORS) if validators is None else validators
    if len(set(validators)) != len(validators) or any(p not in VALIDATORS for p in validators) or (verify and not validators):
        raise ValueError("Choose each supported validator at most once")
    approved_providers = set() if approved_providers is None else set(approved_providers)
    if approved_providers - REGISTRY.keys():
        raise ValueError("Unknown approved provider")
    request_credit_caps = _request_caps(request_credit_caps)
    if live and budget is None:
        raise ValueError("Live enrichment requires a persistent free-credit budget")
    limits = ExecutionLimits(max_http_requests, max_credit_units, stats)
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    pending_identities: set[str] = set()
    for source in rows:
        prepared = {key: str(source.get(key, "") or "").strip() for key in FIELDS}
        if valid_pending(prepared["pending_task"]):
            pending_identities.update(identity_keys(prepared))
    calls, phone_budget = 0, 0
    for source in rows:
        row = {k: str(source.get(k, "") or "").strip() for k in FIELDS}
        for field in ("email", "phone"):
            row[field + "_source"] = "input" if row[field] else ""
            row[field + "_status"] = "input_unverified" if row[field] else ""
        for field in ("errors", "verification_source", "verification_status", "risk_source", "risk_status"):
            row[field] = ""
        if ((row["company_domain"] and not _domain(row["company_domain"])) or
                (row["linkedin_url"] and not _linkedin(row["linkedin_url"])) or
                (row["email"] and not EMAIL.fullmatch(row["email"]))):
            row["enrichment_status"] = "invalid_identifiers"
            output.append(row)
            continue
        if row["company_domain"]:
            row["company_domain"] = _domain(row["company_domain"])
        if row["pending_task"]:
            row["enrichment_status"] = "pending" if valid_pending(row["pending_task"]) else "invalid_pending_task"
            if valid_pending(row["pending_task"]):
                seen.update(identity_keys(row))
            output.append(row)
            continue
        keys = identity_keys(row)
        if not has_identity(row):
            row["enrichment_status"] = "insufficient_identifiers"
            output.append(row)
            continue
        duplicate = bool(keys & (seen | pending_identities))
        seen.update(keys)
        if duplicate:
            row["enrichment_status"] = "duplicate_skipped"
            output.append(row)
            continue
        if not live:
            row["enrichment_status"] = "dry_run_no_credits_used"
            output.append(row)
            continue
        errors = []
        attempted, changed = False, False
        complete = bool(row["email"] and (not want_phone or row["phone"]))
        for provider in provider_names:
            if row["email"] and (not want_phone or row["phone"]):
                break
            if calls >= max_calls:
                break
            spec = REGISTRY[provider]
            if (not configured(provider) or not _usable(provider, row) or not limits.can_request(spec.requests)
                    or (spec.requires_approval and provider not in approved_providers)):
                continue
            base_email_cost = spec.email_cost
            if provider in request_credit_caps:
                base_email_cost = max(base_email_cost or 0, request_credit_caps[provider])
            if base_email_cost is None:
                errors.append(provider + ":request_credit_cap_required")
                continue
            phone_cost = spec.phone_cost
            ask_phone = bool(want_phone and not row["phone"] and phone_cost and phone_budget + phone_cost <= max_phone_credits)
            if row["email"] and not ask_phone:
                continue
            assert budget is not None
            email_cost = 0 if ask_phone and (provider == "prospeo" or (provider == "getprospect" and row["email"])) else base_email_cost
            reserved = limits.reserve(budget, provider, email=email_cost, phone=phone_cost if ask_phone else 0)
            if not reserved and ask_phone and not row["email"]:
                ask_phone = False
                reserved = limits.reserve(budget, provider, email=base_email_cost)
            if not reserved:
                continue
            attempted = True
            calls += 1
            limits.stats["calls"] = calls
            if ask_phone:
                phone_budget += phone_cost
            try:
                result = limits.invoke(PROVIDERS[provider], row, ask_phone)
                if result.email and EMAIL.fullmatch(result.email) and "*" not in result.email and not row["email"]:
                    row["email"] = result.email
                    row["email_status"] = result.email_status or "unverified"
                    row["email_source"] = provider
                    changed = True
                if (result.phone and re.fullmatch(r"\+?[0-9(). \-]{7,30}", result.phone)
                        and 7 <= len(re.sub(r"\D", "", result.phone)) <= 15 and not row["phone"]):
                    row["phone"] = result.phone
                    row["phone_status"] = result.phone_status or "unverified"
                    row["phone_source"] = provider
                    changed = True
                if result.pending_task:
                    if not valid_pending(result.pending_task) or not result.pending_task.startswith(provider + ":"):
                        raise ProviderError("Invalid pending result")
                    row["pending_task"] = result.pending_task
                    break
            except ProviderError:
                errors.append(f"{provider}:request_failed")
        if verify and row["email"] and not row["pending_task"]:
            for validator in validators:
                if limits.stats["verifications"] >= max_verifications:
                    break
                spec = REGISTRY[validator]
                if spec.kind != "risk" and row["verification_source"]:
                    continue
                if (not configured(validator) or not limits.can_request(spec.requests)
                        or (spec.requires_approval and validator not in approved_providers)):
                    continue
                assert budget is not None
                if not limits.reserve(budget, validator, email=spec.email_cost):
                    continue
                limits.stats["verifications"] += 1
                try:
                    status = limits.invoke(VALIDATORS[validator], row["email"])
                    if spec.kind == "risk":
                        row["risk_source"], row["risk_status"] = validator, "risk_report_only"
                    else:
                        allowed = {"valid", "invalid", "deliverable", "undeliverable", "risky", "unknown", "disposable", "invalid_format", "catch-all", "spamtrap", "abuse", "do_not_mail", "duplicate"}
                        row["verification_source"] = validator
                        row["verification_status"] = status if isinstance(status, str) and status in allowed else "unknown"
                except ProviderError:
                    errors.append(f"{validator}:request_failed")
        if limits.ledger_failed:
            errors.append("credit_ledger:unavailable")
        row["enrichment_status"] = ("credit_ledger_error" if limits.ledger_failed else "pending" if row["pending_task"] else "enriched" if changed else
                                    "already_present" if complete else "provider_error" if errors else
                                    "attempted_no_match" if attempted else "not_attempted_missing_keys_or_budget")
        row["errors"] = "; ".join(errors)
        seen.update(identity_keys(row))
        output.append(row)
    return output, calls


def balances() -> dict[str, Any]:
    res: dict[str, Any] = {}
    if configured("getprospect"):
        try: res["getprospect"] = request("GET", "https://api.getprospect.com/v2/credits", headers={"x-api-key":os.environ["GETPROSPECT_API_KEY"]}).get("data")
        except ProviderError as e: res["getprospect"] = {"error":str(e)}
    if configured("prospeo"):
        try: res["prospeo"] = request("GET", "https://api.prospeo.io/account-information", headers={"X-KEY":os.environ["PROSPEO_API_KEY"]}).get("response")
        except ProviderError as e: res["prospeo"] = {"error":str(e)}
    if not res: res["message"] = "No provider API keys found in environment"
    return res


def _caps(values: list[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        provider, separator, amount = value.partition("=")
        if not separator or provider not in REGISTRY or not amount.isdigit() or provider in result:
            raise ValueError("Credit caps must be unique PROVIDER=NONNEGATIVE_INTEGER values")
        result[provider] = int(amount)
    return result


def _csv_safe(value: str) -> str:
    # Quoting alone does not prevent spreadsheet formula execution.
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", help="CSV with candidate identifiers; keep real data outside Git")
    parser.add_argument("--output", default="results/enriched.csv", help="New private CSV; refuses to overwrite")
    parser.add_argument("--providers", default=",".join(DEFAULT_PROVIDERS), help="Comma-separated discovery order; added providers are opt-in")
    parser.add_argument("--list-providers", action="store_true", help="Offline provider capabilities and access requirements")
    parser.add_argument("--verify", action="store_true", help="Explicitly allow email validation/risk checks with separate budgets")
    parser.add_argument("--validators", default=",".join(DEFAULT_VALIDATORS), help="Comma-separated validators; zerobounce_ai is opt-in")
    parser.add_argument("--max-verifications", type=int, default=0, help="Per-run validation/risk attempts; default 0")
    parser.add_argument("--max-http-requests", type=int, default=20, help="Total HTTP requests, including authentication, validation and job retrieval")
    parser.add_argument("--max-credit-units", type=int, default=None, help="Optional additional per-run credit cap across all providers")
    parser.add_argument("--request-credit-cap", action="append", default=[], metavar="PROVIDER=N", help="Provider-confirmed worst-case credits per discovery request; required for Snov (no assumed result count)")
    parser.add_argument("--approved-provider", action="append", default=[], choices=sorted(REGISTRY), help="Confirm provider-approved/account entitlement for a restricted adapter; does not authorize paid spending")
    parser.add_argument("--live", action="store_true", help="Explicitly allow quota-consuming requests within configured free-credit caps")
    parser.add_argument("--phone", action="store_true", help="Also request phones (requires --live and phone credit caps)")
    parser.add_argument("--max-phone-credits", type=int, default=0, help="Per-run upper bound on requested phone credits (0=disabled)")
    parser.add_argument("--max-calls", type=int, default=10, help="Maximum attempted API lookups in this run")
    parser.add_argument("--email-credits", action="append", default=[], metavar="PROVIDER=N", help="Free email credit allocation per provider/cycle; repeatable, default 0")
    parser.add_argument("--phone-credits", action="append", default=[], metavar="PROVIDER=N", help="Free phone credit allocation per provider/cycle; repeatable, default 0")
    parser.add_argument("--credit-cycle", help="Provider billing cycle YYYY-MM; required for live calls")
    parser.add_argument("--credit-ledger", default=".private/credits.sqlite3", help="Persistent shared local credit ledger; never delete to reset quota")
    parser.add_argument("--balances", action="store_true", help="Explicit metadata-only network check of Prospeo/GetProspect balances")
    args = parser.parse_args(argv)
    if args.list_providers:
        for name, spec in REGISTRY.items():
            gate = " | explicit access confirmation required" if spec.requires_approval else ""
            credentials = ("configured" if configured(name) else "missing") if spec.required_keys else "not_required"
            print(f"{name}: {spec.kind} | {spec.access} | credentials={credentials}{gate}")
        return 0
    if args.balances:
        print(json.dumps(balances(), indent=2))
        return 0
    if not args.input:
        parser.error("--input required unless --balances")
    if args.phone and (not args.live or args.max_phone_credits < 1):
        parser.error("--phone needs --live and --max-phone-credits >= 1")
    if args.verify and (not args.live or args.max_verifications < 1):
        parser.error("--verify needs --live and --max-verifications >= 1")
    if min(args.max_calls, args.max_phone_credits, args.max_verifications, args.max_http_requests) < 0 or (args.max_credit_units is not None and args.max_credit_units < 0):
        parser.error("Credit/call caps cannot be negative")
    if args.live and not args.credit_cycle:
        parser.error("--live requires --credit-cycle YYYY-MM and explicit free-credit allocations")
    providers = [s.strip().lower() for s in args.providers.split(",") if s.strip()]
    if not providers or len(set(providers)) != len(providers) or any(p not in PROVIDERS for p in providers):
        parser.error("Choose each supported discovery provider at most once; use --list-providers")
    validators = [s.strip().lower() for s in args.validators.split(",") if s.strip()]
    if not validators or len(set(validators)) != len(validators) or any(p not in VALIDATORS for p in validators):
        parser.error("Choose each supported validator at most once; use --list-providers")
    try:
        email_caps, phone_caps = _caps(args.email_credits), _caps(args.phone_credits)
        request_credit_caps = _request_caps(_caps(args.request_credit_cap))
    except ValueError as exc:
        parser.error(str(exc))
    limits = {p: {"email": email_caps.get(p, 0), "phone": phone_caps.get(p, 0)} for p in REGISTRY}
    budget = None
    dest = Path(args.output)
    created = False
    stats: dict[str, int] = {}
    try:
        with open(args.input, newline="", encoding="utf-8-sig") as source:
            reader = csv.DictReader(source)
            if not reader.fieldnames or not any(k in reader.fieldnames for k in ("full_name", "first_name", "linkedin_url", "email")):
                parser.error("Input CSV needs candidate identifier headings")
            rows = list(reader)
        # Reserve the output before any provider request, with private permissions
        # and exclusive creation (also refuses existing files and symlinks).
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as output:
            if args.live:
                budget = CreditBudget(args.credit_ledger, args.credit_cycle, limits)
            enriched, calls = run(rows, provider_names=providers, live=args.live, want_phone=args.phone,
                                  max_calls=args.max_calls, max_phone_credits=args.max_phone_credits, budget=budget,
                                  verify=args.verify, validators=validators, max_verifications=args.max_verifications,
                                  max_http_requests=args.max_http_requests, max_credit_units=args.max_credit_units,
                                  approved_providers=set(args.approved_provider), stats=stats,
                                  request_credit_caps=request_credit_caps)
            writer = csv.DictWriter(output, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows({k: _csv_safe(v) for k, v in row.items()} for row in enriched)
    except FileExistsError:
        parser.exit(2, "Output already exists; choose a new output path. No enrichment was started.\n")
    except (OSError, ValueError, CreditBudgetError):
        if created:
            dest.unlink(missing_ok=True)
        parser.exit(2, "Unable to process input/output or credit ledger; check configuration and file permissions.\n")
    finally:
        if budget is not None:
            budget.close()
    print(f"Wrote {len(enriched)} rows; initiated {calls} provider lookups; HTTP requests={stats.get('http_requests', 0)}; live={args.live}")
    return 2 if stats.get("ledger_errors") else 0


if __name__ == "__main__":
    sys.exit(main())
