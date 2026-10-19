"""Expanded official-API adapters. All transport is injected and quota guarded.

No adapter retries, polls, schedules work, or sends outreach. Asynchronous
finders submit once; explicit ``read_pending`` retrieves an existing task once.
Access entitlements and credit reservations belong to the caller.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable

from provider_types import Contact, ProviderError

_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TASK = re.compile(r"[A-Za-z0-9_-]{1,200}", re.ASCII)


def _email(value: Any) -> str:
    value = value.strip().lower() if isinstance(value, str) else ""
    # API masks and placeholders must never become candidate contacts.
    if any(mark in value for mark in ("*", "\u2022", "\u2026")):
        return ""
    if value in {"email_not_unlocked@domain.com", "not_available@domain.com"}:
        return ""
    return value if _EMAIL.fullmatch(value) else ""


def _mapping(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _response(value: Any, provider: str) -> dict:
    if not isinstance(value, dict):
        raise ProviderError(provider + " returned an invalid response")
    if value.get("error") or value.get("errors"):
        raise ProviderError(provider + " request rejected")
    return value


def _key(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value or "\r" in value or "\n" in value:
        raise ProviderError("Provider credentials are missing or invalid")
    return value


def _names(row: dict[str, str]) -> tuple[str, str]:
    first, last = row.get("first_name", "").strip(), row.get("last_name", "").strip()
    if not first or not last:
        parts = row.get("full_name", "").strip().split()
        if len(parts) >= 2:
            return parts[0], " ".join(parts[1:])
    return first, last


def _name_domain(row: dict[str, str]) -> bool:
    first, last = _names(row)
    return bool(first and last and row.get("company_domain", "").strip())


def _no_phone(phone: bool, provider: str) -> None:
    if phone:
        raise ProviderError(provider + " phone enrichment is not enabled")


def tomba(row: dict[str, str], phone: bool = False, *, request: Callable) -> Contact:
    _no_phone(phone, "Tomba")
    if not _name_domain(row):
        return Contact()
    first, last = _names(row)
    result = _response(request("GET", "https://api.tomba.io/v1/email-finder", params={
        "domain": row["company_domain"].strip(), "first_name": first, "last_name": last,
    }, headers={"X-Tomba-Key": _key("TOMBA_API_KEY"), "X-Tomba-Secret": _key("TOMBA_SECRET_KEY")}), "Tomba")
    person = _mapping(result.get("data"))
    email = _email(person.get("email"))
    status = str(_mapping(person.get("verification")).get("status") or "").lower()
    statuses = {"valid": "provider_valid", "invalid": "provider_invalid", "accept_all": "provider_accept_all",
                "unknown": "provider_unknown", "webmail": "provider_webmail", "disposable": "provider_disposable"}
    return Contact(email=email, email_status=statuses.get(status, "provider_found") if email else "")


def apollo(row: dict[str, str], phone: bool = False, *, request: Callable) -> Contact:
    # Revealing phones requires a webhook; waterfall may incur separate charges.
    _no_phone(phone, "Apollo")
    first, last = _names(row)
    if not (row.get("linkedin_url") or row.get("email") or (first and last and (row.get("company_domain") or row.get("company_name")))):
        return Contact()
    body: dict[str, Any] = {"reveal_personal_emails": False, "reveal_phone_number": False,
                            "run_waterfall_email": False, "run_waterfall_phone": False}
    for source, target in (("linkedin_url", "linkedin_url"), ("email", "email"),
                           ("company_domain", "domain"), ("company_name", "organization_name")):
        if row.get(source, "").strip():
            body[target] = row[source].strip()
    if first and last:
        body.update(first_name=first, last_name=last)
    result = _response(request("POST", "https://api.apollo.io/api/v1/people/match",
                               headers={"x-api-key": _key("APOLLO_API_KEY")}, body=body), "Apollo")
    if result.get("success") is False:
        raise ProviderError("Apollo request rejected")
    person = _mapping(result.get("person"))
    email = _email(person.get("email"))
    status = str(person.get("email_status") or "").lower()
    statuses = {"verified": "provider_verified", "unverified": "provider_unverified", "unavailable": "provider_unavailable"}
    return Contact(email=email, email_status=statuses.get(status, "provider_found") if email else "")


def _task_id(value: Any) -> str:
    if not isinstance(value, str) or not _TASK.fullmatch(value):
        raise ProviderError("Invalid pending task identifier")
    return value


def validate_task(task: str) -> tuple[str, str]:
    if not isinstance(task, str) or task.count(":") != 1:
        raise ProviderError("Invalid pending task")
    provider, identifier = task.split(":", 1)
    if provider not in {"dropcontact", "snov"}:
        raise ProviderError("Unsupported pending task provider")
    return provider, _task_id(identifier)


def dropcontact(row: dict[str, str], phone: bool = False, *, request: Callable) -> Contact:
    _no_phone(phone, "Dropcontact")
    if not (_name_domain(row) or _email(row.get("email"))):
        return Contact()
    first, last = _names(row)
    fields: dict[str, str] = {}
    if first and last:
        fields.update(first_name=first, last_name=last)
    for source, target in (("company_domain", "website"), ("company_name", "company"), ("email", "email")):
        if row.get(source, "").strip():
            fields[target] = row[source].strip()
    result = _response(request("POST", "https://api.dropcontact.com/v1/enrich/all",
                               headers={"X-Access-Token": _key("DROPCONTACT_API_KEY")},
                               body={"data": [fields], "language": "en"}), "Dropcontact")
    if result.get("success") is False:
        raise ProviderError("Dropcontact did not create a task")
    return Contact(pending_task="dropcontact:" + _task_id(result.get("request_id")))


def _snov_auth(request: Callable) -> dict[str, str]:
    auth = _response(request("POST", "https://api.snov.io/v1/oauth/access_token", form={
        "grant_type": "client_credentials", "client_id": _key("SNOV_CLIENT_ID"),
        "client_secret": _key("SNOV_CLIENT_SECRET"),
    }), "Snov.io")
    token = auth.get("access_token")
    if not isinstance(token, str) or not token.strip() or any(char in token for char in "\r\n"):
        raise ProviderError("Snov.io authentication failed")
    return {"Authorization": "Bearer " + token}


def snov(row: dict[str, str], phone: bool = False, *, request: Callable) -> Contact:
    _no_phone(phone, "Snov.io")
    if not _name_domain(row):
        return Contact()
    headers = _snov_auth(request)
    first, last = _names(row)
    result = _response(request("POST", "https://api.snov.io/v2/emails-by-domain-by-name/start",
                               headers=headers, body={"rows": [{"first_name": first, "last_name": last,
                                                                "domain": row["company_domain"].strip()}]}), "Snov.io")
    if result.get("success") is False:
        raise ProviderError("Snov.io did not create a task")
    return Contact(pending_task="snov:" + _task_id(_mapping(result.get("data")).get("task_hash")))


def read_pending(task: str, *, request: Callable) -> Contact:
    provider, identifier = validate_task(task)
    if provider == "dropcontact":
        result = _response(request("GET", "https://api.dropcontact.com/v1/enrich/all/" + identifier,
                                   headers={"X-Access-Token": _key("DROPCONTACT_API_KEY")}), "Dropcontact")
        if result.get("success") is False:
            return Contact(pending_task=task)
        people = result.get("data")
        if not isinstance(people, list):
            raise ProviderError("Dropcontact returned an invalid task result")
        if not people:
            return Contact()
        emails = _mapping(people[0]).get("email") or []
        if not isinstance(emails, list):
            raise ProviderError("Dropcontact returned an invalid email result")
        # Generic mailboxes are not candidate addresses; catch-all is not verified.
        for choice in emails:
            choice = _mapping(choice)
            email = _email(choice.get("email"))
            qualification = choice.get("qualification")
            if qualification is not None and not isinstance(qualification, str):
                raise ProviderError("Dropcontact returned an invalid qualification")
            if email and qualification in ("nominative@pro", "catch_all@pro"):
                return Contact(email=email, email_status="provider_found")
        return Contact()
    headers = _snov_auth(request)
    result = _response(request("GET", "https://api.snov.io/v2/emails-by-domain-by-name/result",
                               headers=headers, params={"task_hash": identifier}), "Snov.io")
    status = result.get("status")
    if not isinstance(status, str):
        raise ProviderError("Snov.io returned an invalid task status")
    if status in {"in_progress", "in progress", "pending", "processing", "queued"}:
        return Contact(pending_task=task)
    if status != "completed":
        raise ProviderError("Snov.io returned an unknown task status")
    people = result.get("data")
    if not isinstance(people, list):
        raise ProviderError("Snov.io returned an invalid task result")
    for person in people:
        choices = _mapping(person).get("result") or []
        if not isinstance(choices, list):
            raise ProviderError("Snov.io returned an invalid email result")
        for choice in choices:
            choice = _mapping(choice)
            if (choice.get("smtp_status") == "valid" and choice.get("is_valid_format") is True
                    and not choice.get("is_disposable")):
                email = _email(choice.get("email"))
                if email:
                    return Contact(email=email, email_status="provider_smtp_valid")
    return Contact()


def _valid_email(email: str) -> str:
    normalized = _email(email)
    if not normalized:
        raise ProviderError("Invalid email for verification")
    return normalized


def verify_abstract(email: str, *, request: Callable) -> str:
    result = _response(request("GET", "https://emailvalidation.abstractapi.com/v1/", params={
        "api_key": _key("ABSTRACT_EMAIL_API_KEY"), "email": _valid_email(email),
    }), "Abstract")
    format_value = result.get("is_valid_format")
    if format_value is False or _mapping(format_value).get("value") is False:
        return "invalid_format"
    disposable = result.get("is_disposable_email")
    if disposable is True or _mapping(disposable).get("value") is True:
        return "disposable"
    return {"DELIVERABLE": "deliverable", "UNDELIVERABLE": "undeliverable", "RISKY": "risky", "UNKNOWN": "unknown"}.get(
        str(result.get("deliverability") or "").upper(), "unknown")


def verify_zerobounce(email: str, *, request: Callable) -> str:
    result = _response(request("POST", "https://api.zerobounce.net/v2/validate", form={
        "api_key": _key("ZEROBOUNCE_API_KEY"), "email": _valid_email(email), "timeout": "15",
    }), "ZeroBounce")
    status = str(result.get("status") or "").lower()
    return status if status in {"valid", "invalid", "catch-all", "unknown", "spamtrap", "abuse", "do_not_mail"} else "unknown"


def verify_emailable(email: str, *, request: Callable) -> str:
    # Official API authentication uses api_key in query parameters, not Bearer.
    result = _response(request("GET", "https://api.emailable.com/v1/verify", params={
        "api_key": _key("EMAILABLE_API_KEY"), "email": _valid_email(email),
    }), "Emailable")
    if result.get("disposable") is True:
        return "disposable"
    status = str(result.get("state") or "").lower()
    return status if status in {"deliverable", "undeliverable", "risky", "unknown", "duplicate"} else "unknown"


def verify_zerobounce_ai(email: str, *, request: Callable) -> str:
    # Independent from zerobounce.net. Heuristics cannot verify mailbox existence.
    _response(request("POST", "https://zerobounce.ai/developers/api/email",
                      body={"email": _valid_email(email)}), "zerobounce.ai")
    return "risk_report_only"
