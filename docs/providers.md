# Provider behavior and result interpretation

All adapters use official APIs. Tests use synthetic mocked responses, not live account calls. See [PROVIDERS.md](../PROVIDERS.md) for all 11 providers, source links and access restrictions. API availability and free plan eligibility remain account-dependent.

## Discovery and validation

Prospeo and GetProspect support email discovery and separately requested phones. Hunter, Tomba and Apollo supply email discovery only. Apollo requests business contact data without personal-email or phone reveal. Dropcontact and Snov return asynchronous tasks when results are not ready; only explicit `resume_pending.py --live` retrieval fetches an existing job.

Output includes input identifiers and contact fields, contact source/status, separate `verification_source`/`verification_status`, separate `risk_source`/`risk_status`, `pending_task`, `enrichment_status` and sanitized errors.

Prospeo accepts only revealed verified values, GetProspect distinguishes valid emails from found phones, and Hunter preserves valid/accept-all/unknown/invalid distinctions from its response. Other provider evidence is also retained without inventing verification. A `provider_found` result does not establish deliverability or ownership. Masked contacts and invalid formats are excluded.

Supplied emails/phones are preserved and marked `input_unverified`. Validation does not replace a candidate's email. Abstract, ZeroBounce.net and Emailable map their validation evidence to the separate verification fields. Unknown, catch-all, risky and invalid outcomes remain distinct from verified/deliverable results.

zerobounce.ai is a separate keyless risk service, not ZeroBounce.net. It never checks whether a mailbox exists, and its result is written only to risk fields. It is excluded from default validators and must be explicitly selected. Sending an email to it still discloses that email to an external service and needs the same live opt-in and privacy review.

## Deduplication, limits and export

Discovery stops after one usable email unless an explicitly requested phone is still missing. Existing contact fields are not overwritten. Within a run, normalized LinkedIn URLs and email addresses are strong deduplication signals. Name/company is a fallback only when no strong identifier exists, avoiding merges solely because people share a name. There is no cross-run candidate cache or external CRM deduplication.

All HTTP work, including OAuth and explicit job retrieval, is subject to per-run limits and persistent provider reservations. Snov has no guessed one-credit request cost: it is blocked unless a finite provider/account-enforced worst-case ceiling is confirmed and supplied through `--request-credit-cap snov=N` for both submission and retrieval. Retrieval reserves that ceiling again conservatively. Provider failures produce sanitized errors; authenticated URLs, raw provider bodies and exception details are not copied into CSV output. No request automatically retries. See [configuration](configuration.md) for costs, migration and ledger failures.

Formula-looking values receive a protective apostrophe on CSV export, including phone numbers beginning with `+`; in-memory values remain unchanged. Import the CSV as text where possible and account for that prefix in downstream systems. Pending task identifiers are private local data and must not be committed or published.

No scraping, mailbox SMTP probes, phone calls, messaging, webhook listener or CRM sync is implemented. Live behavior and account-specific access have not been tested as part of this update.
