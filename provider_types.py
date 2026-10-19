"""Shared adapter result and redacted exception types."""
from dataclasses import dataclass


class ProviderError(RuntimeError):
    """A safe provider failure; never attach credentials or response bodies."""


@dataclass
class Contact:
    email: str = ""
    phone: str = ""
    email_status: str = ""
    phone_status: str = ""
    pending_task: str = ""
