# Hirefloww Enrichment

CSV-based candidate contact enrichment, email validation and optional risk checks through 11 official provider APIs. Python 3.10+ is required, with no third-party Python packages.

Dry run is the default. Tests use synthetic records, mocked responses and blocked network connections. Live account access and provider billing have not been tested.

| Role | Providers |
| --- | --- |
| Email discovery, optional phone | GetProspect, Prospeo |
| Email discovery | Hunter, Tomba, Dropcontact, Apollo, Snov.io |
| Email validation | Abstract, ZeroBounce.net, Emailable |
| Optional risk checks; never mailbox verification | zerobounce.ai, a separate service from ZeroBounce.net |

Only GetProspect, Prospeo and Hunter are in the default discovery order. Added discovery providers require explicit selection. Validation is separately opt-in, and zerobounce.ai must be selected explicitly. [PROVIDERS.md](PROVIDERS.md) distinguishes recurring free offers, one-time trials, restricted access and unconfirmed account eligibility with official sources. Free website access does not establish free API entitlement.

## Offline quickstart

```bash
python enrich.py --list-providers
python enrich.py --input sample/leads.csv --output results/dryrun.csv
python scripts/test_offline.py
```

The sample contains synthetic identities at reserved example domains and a duplicate. Output must use a new path; existing files are never overwritten. The offline runner removes provider credentials, blocks DNS/socket connections, runs all unit tests, compiles root modules/tests/scripts and exercises the sample dry run.

[GitHub Actions](.github/workflows/tests.yml) runs offline checks on Python 3.10–3.13 for pushes, pull requests and manual test runs. It uses read-only repository permissions and no provider secrets. Live unattended enrichment is not enabled. A separate [automatic candidate queue](docs/auto-candidate-queue.md) runs on issue changes and a daily schedule, but only in **zero-credit offline preflight** mode.

## Live configuration

Live requests require `--live`, `--credit-cycle YYYY-MM` and explicit per-provider allocations. Every allocation defaults to zero. Set only the required keys through the process environment or secret manager; `.env` files are not loaded automatically. [Security](docs/security.md) documents all variable names and GitHub Secrets.

After confirming the account has enough free API allowance, a limited Hunter lookup can be configured as:

```bash
python enrich.py --input private/candidates.csv --output results/email-run.csv \
  --providers hunter --live --max-calls 1 --max-http-requests 1 \
  --credit-cycle YYYY-MM --email-credits hunter=1
```

Replace `YYYY-MM` with the applicable provider cycle. Do not execute a live example simply to test configuration. Local caps cannot determine your account plan, free entitlement or actual bill.

[Configuration](docs/configuration.md) covers phone lookup, validation, restricted provider access and asynchronous jobs. All processes using the same accounts must share the persistent ledger, default `.private/credits.sqlite3`. Preserve it between runs, repeat the original allocations, and account for activity outside this tool. Reservations are retained after errors and no-match results.

Apollo and Snov additionally require `--approved-provider apollo` or `--approved-provider snov` after confirming the account's endpoint entitlement. This flag records operator confirmation of provider access; it does not authorize paid spending. Snov also requires `--request-credit-cap snov=N`, where `N` is a provider/account-enforced confirmed worst-case credit ceiling for one request, and sufficient ledger allocation. Its API can charge per returned email without a documented finite result ceiling; an arbitrary estimate is unsafe. Without a confirmed finite ceiling, leave Snov submission and retrieval disabled.

## Asynchronous results and private data

Dropcontact and Snov submissions can return `pending_task`. Submission never automatically polls. Retrieve existing tasks through `resume_pending.py` only when explicitly ready; it defaults to offline dry run and needs the same ledger/cycle/allocations for live retrieval. Task identifiers and CSVs remain private. Retrieval is conservatively budgeted and can make authentication requests; it is not assumed free.

Input accepts names with company details, LinkedIn URLs, existing emails and phones. Strong identifiers take precedence for deduplication; name/company matching is a fallback when those are absent. Duplicate detection is within a run, not a cross-run candidate database. Existing contacts are preserved and marked unverified. Provider-reported validation and risk fields remain separate; a found contact or risk result does not establish deliverability or mailbox existence. See [provider behavior](docs/providers.md).

Keep real candidate CSVs under `private/`, `contacts/` or `data/`, results under `results/`, and the ledger under `.private/`. These locations are ignored by git. Review staged changes before committing; ignore rules do not protect already tracked files. Use appropriate permission, purpose and retention controls for professional contact data.

No provider keys or live enrichment requests are needed to operate the scheduled offline candidate queue. Real candidate records belong only in the restricted private GitHub issue queue, not in source files or commits. Live API behavior and account-specific eligibility remain configuration steps before any approved live run.
