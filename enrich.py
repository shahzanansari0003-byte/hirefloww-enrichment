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
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from credit_budget import CreditBudget, CreditBudgetError

FIELDS = ["full_name", "first_name", "last_name", "linkedin_url", "company_name", "company_domain", "email", "phone", "email_status", "phone_status", "email_source", "phone_source", "enrichment_status", "errors"]
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


# Provider credentials must never follow a redirect to another destination.
urlopen = build_opener(_NoRedirect).open

class ProviderError(RuntimeError):
    pass

@dataclass
class Contact:
    email: str = ""
    phone: str = ""
    email_status: str = ""
    phone_status: str = ""


def request(method: str, url: str, *, headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None, body: dict[str, Any] | None = None,
            timeout: int = 20) -> dict[str, Any]:
    if params:
        url += ("&" if "?" in url else "?") + urlencode(params)
    data = json.dumps(body).encode() if body is not None else None
    req = Request(url, data=data, method=method, headers={"Accept":"application/json", "Content-Type":"application/json", **(headers or {})})
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
    verification = {"valid": "provider_verified", "accept_all": "accept_all", "invalid": "invalid", "unknown": "unknown"}.get(status, "unverified")
    return Contact(email=email, email_status=verification if email else "")

PROVIDERS = {"prospeo": prospeo, "getprospect": getprospect, "hunter":hunter}


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
    return bool(os.getenv({"hunter":"HUNTER_API_KEY","prospeo":"PROSPEO_API_KEY","getprospect":"GETPROSPECT_API_KEY"}[name], "").strip())


def run(rows: list[dict[str, str]], *, provider_names: list[str], live: bool, want_phone: bool,
        max_calls: int, max_phone_credits: int, budget: CreditBudget | None = None) -> tuple[list[dict[str, str]], int]:
    """Reserve conservative credits before every request, without automatic retries.

    Live requests require a persistent budget. This local budget cannot establish
    provider billing entitlements; operators must allocate only available free credits.
    """
    if max_calls < 0 or max_phone_credits < 0:
        raise ValueError("Credit/call caps cannot be negative")
    if not provider_names or len(set(provider_names)) != len(provider_names) or any(p not in PROVIDERS for p in provider_names):
        raise ValueError("Choose each supported provider at most once")
    if live and budget is None:
        raise ValueError("Live enrichment requires a persistent free-credit budget")
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    calls, phone_budget = 0, 0
    for source in rows:
        row = {k: str(source.get(k, "") or "").strip() for k in FIELDS}
        # Input contact fields have no independently established verification.
        for field in ("email", "phone"):
            row[field + "_source"] = "input" if row[field] else ""
            row[field + "_status"] = "input_unverified" if row[field] else ""
        row["errors"] = ""
        if ((row["company_domain"] and not _domain(row["company_domain"])) or
                (row["linkedin_url"] and not _linkedin(row["linkedin_url"])) or
                (row["email"] and not EMAIL.fullmatch(row["email"]))):
            row["enrichment_status"] = "invalid_identifiers"
            output.append(row)
            continue
        if row["company_domain"]:
            row["company_domain"] = _domain(row["company_domain"])
        keys = identity_keys(row)
        if not has_identity(row):
            row["enrichment_status"] = "insufficient_identifiers"
            output.append(row)
            continue
        duplicate = bool(keys & seen)
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
            if not configured(provider):
                continue
            if provider == "hunter":
                first, last = name_parts(row)
                if not (row["company_domain"] and first and last) or row["email"]:
                    continue
            phone_cost = {"prospeo": 10, "getprospect": 1, "hunter": 0}[provider]
            ask_phone = bool(want_phone and not row["phone"] and phone_cost and phone_budget + phone_cost <= max_phone_credits)
            if row["email"] and not ask_phone:
                continue
            assert budget is not None
            # Reserve both buckets atomically. If phone is out of budget, an
            # email-only lookup may still be allowed when the email is missing.
            email_cost = 0 if ask_phone and (provider == "prospeo" or (provider == "getprospect" and row["email"])) else 1
            reserved = budget.reserve(provider, email=email_cost, phone=phone_cost if ask_phone else 0)
            if not reserved and ask_phone and not row["email"]:
                ask_phone = False
                reserved = budget.reserve(provider, email=1)
            if not reserved:
                continue
            attempted = True
            calls += 1
            if ask_phone:
                phone_budget += phone_cost
            try:
                result = PROVIDERS[provider](row, ask_phone)
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
            except ProviderError:
                # Errors can contain provider-controlled text; store no raw text.
                errors.append(f"{provider}:request_failed")
        row["enrichment_status"] = ("already_present" if complete else "enriched" if changed else
                                    "provider_error" if errors else "attempted_no_match" if attempted else
                                    "not_attempted_missing_keys_or_budget")
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
        if not separator or provider not in PROVIDERS or not amount.isdigit() or provider in result:
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
    parser.add_argument("--providers", default="getprospect,prospeo,hunter", help="Comma-separated order")
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
    if args.balances:
        print(json.dumps(balances(), indent=2))
        return 0
    if not args.input:
        parser.error("--input required unless --balances")
    if args.phone and (not args.live or args.max_phone_credits < 1):
        parser.error("--phone needs --live and --max-phone-credits >= 1")
    if args.max_calls < 0 or args.max_phone_credits < 0:
        parser.error("Credit/call caps cannot be negative")
    if args.live and not args.credit_cycle:
        parser.error("--live requires --credit-cycle YYYY-MM and explicit free-credit allocations")
    providers = [s.strip().lower() for s in args.providers.split(",") if s.strip()]
    if not providers or len(set(providers)) != len(providers) or any(p not in PROVIDERS for p in providers):
        parser.error("Choose each supported provider at most once: getprospect,prospeo,hunter")
    try:
        email_caps, phone_caps = _caps(args.email_credits), _caps(args.phone_credits)
    except ValueError as exc:
        parser.error(str(exc))
    limits = {p: {"email": email_caps.get(p, 0), "phone": phone_caps.get(p, 0)} for p in PROVIDERS}
    budget = None
    dest = Path(args.output)
    created = False
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
                                  max_calls=args.max_calls, max_phone_credits=args.max_phone_credits, budget=budget)
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
    print(f"Wrote {len(enriched)} rows; initiated {calls} provider lookups; live={args.live}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
