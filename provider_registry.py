"""Explicit capabilities, credentials, access gates and conservative costs.

No entry establishes an account's free API entitlement. All live budgets default
to zero. Costs must be reconciled with the provider dashboard before live use.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    kind: str
    required_keys: tuple[str, ...]
    email_cost: int | None = 1
    phone_cost: int = 0
    requests: int = 1
    requires_approval: bool = False
    access: str = "Account-dependent free API allocation; confirm entitlement"


REGISTRY = {
    "getprospect": ProviderSpec("discovery", ("GETPROSPECT_API_KEY",), phone_cost=1),
    "prospeo": ProviderSpec("discovery", ("PROSPEO_API_KEY",), phone_cost=10),
    "hunter": ProviderSpec("discovery", ("HUNTER_API_KEY",)),
    "tomba": ProviderSpec("discovery", ("TOMBA_API_KEY", "TOMBA_SECRET_KEY")),
    "dropcontact": ProviderSpec("discovery", ("DROPCONTACT_API_KEY",), access="One-time free trial; paid thereafter"),
    "apollo": ProviderSpec("discovery", ("APOLLO_API_KEY",), email_cost=9, requires_approval=True,
                           access="Account/endpoint entitlement must be confirmed; restricted access gate"),
    "snov": ProviderSpec("discovery", ("SNOV_CLIENT_ID", "SNOV_CLIENT_SECRET"), email_cost=None, requests=2,
                         requires_approval=True, access="Free API testing requires provider approval; Sales Suite plans include API; submit requires a provider-confirmed credit ceiling"),
    "abstract": ProviderSpec("verification", ("ABSTRACT_EMAIL_API_KEY",)),
    "zerobounce": ProviderSpec("verification", ("ZEROBOUNCE_API_KEY",), access="Free validation allocation for qualifying accounts; confirm eligibility"),
    "emailable": ProviderSpec("verification", ("EMAILABLE_API_KEY",), access="One-time free trial; paid thereafter"),
    "zerobounce_ai": ProviderSpec("risk", (), access="Keyless shared daily/IP limit; risk checks only, no mailbox verification"),
}

DEFAULT_PROVIDERS = ("getprospect", "prospeo", "hunter")
DEFAULT_VALIDATORS = ("abstract", "zerobounce", "emailable")
