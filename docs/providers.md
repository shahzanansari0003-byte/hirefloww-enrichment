# Provider integrations and result interpretation

The adapters use official APIs and no third-party Python packages. Tests supply synthetic mocked responses; project setup does not test with real credentials or candidate records. Check current official API documentation and your account's entitlement before any approved live run.

| Provider | Endpoint | Authentication | Capabilities |
| --- | --- | --- | --- |
| Prospeo | `POST https://api.prospeo.io/enrich-person` | `X-KEY` header | Email lookup and opt-in mobile lookup |
| GetProspect | `POST https://api.getprospect.com/v2/people/enrich` | `x-api-key` header | Email lookup and opt-in mobile lookup |
| Hunter | `GET https://api.hunter.io/v2/email-finder` | `api_key` query parameter | Email lookup by first name, last name and company domain |

Manual balance metadata endpoints are `GET https://api.prospeo.io/account-information` and `GET https://api.getprospect.com/v2/credits`. They are never called by the offline workflow.

## Contact and verification status

Each output row includes the original identifier fields, `email`, `phone`, `email_status`, `phone_status`, `email_source`, `phone_source`, `enrichment_status` and `errors`.

Prospeo is requested with verified-email filtering; masked or unrevealed values are rejected. GetProspect distinguishes a valid email from a found phone. Hunter's finder response can provide a verification status, including unknown, accept-all or invalid outcomes, but a finder result alone is not a separate verification request. The application records provider evidence without promising deliverability or phone ownership. `provider_found` is not equivalent to verified; a found phone must not be represented as independently verified.

Existing emails and phones are preserved and marked `input_unverified` rather than newly provider-verified. This project does not independently perform SMTP deliverability checks or place verification calls. Provider failures are summarized without logging raw response bodies or credentials.

## Deduplication and fallback

Duplicate rows are skipped within one run using normalized LinkedIn URLs or email addresses when available. Name and company information is a fallback only when neither strong identifier is available, to avoid merging different people who share a name at the same company. Output retains a row marked `duplicate_skipped` so users can reconcile the input. No cross-run candidate cache or external CRM deduplication is claimed.

Providers are attempted in the order selected by `--providers`. Available fields, eligible identifiers, configured keys, per-run limits and persistent per-provider limits determine whether another lookup is allowed. Successful enrichment fills missing contact fields and records the source; it does not overwrite supplied contacts. A result without a contact is distinct from a skipped or blocked request.

Formula-looking cells from input or provider responses are prefixed with an apostrophe when written to CSV, including phone numbers beginning with `+`; in-memory contact values remain unchanged. This reduces spreadsheet formula injection risk. Import the CSV as text when possible, and account for the protective prefix when another system reads exported values.

## Official documentation

- [Prospeo person enrichment](https://prospeo.io/api-docs/enrich-person)
- [GetProspect people enrichment](https://getprospect.com/api-docs/people-enrich)
- [GetProspect credits](https://getprospect.com/api-docs/credits)
- [Hunter API](https://hunter.io/api-documentation/v2)

These links identify sources to review; they are not a claim that live API schemas or current pricing were validated during offline setup. No Kaspr, SignalHire or Snov.io integration is included.
