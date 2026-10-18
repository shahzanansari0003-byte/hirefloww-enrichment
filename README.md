# Hirefloww Enrichment

CSV-based candidate email and phone enrichment through the official Prospeo, GetProspect and Hunter APIs. Python 3.10 or newer is required; there are no third-party Python dependencies.

Dry run is the default. Tests use synthetic contacts and mocked provider responses. Running the quickstart or test workflow does not contact enrichment providers or use credits.

| Provider | Email | Phone | Status recorded when returned |
| --- | --- | --- | --- |
| Prospeo | Verified email only | Optional verified mobile | Provider verification status |
| GetProspect | Valid email only | Optional found mobile | Provider valid/found status |
| Hunter | Email finder | Unsupported | Provider finder/verification status |

Provider status is evidence reported by the provider at lookup time; it is not a guarantee of current deliverability or phone ownership. Supplied contact fields are preserved rather than silently reverified. See [provider and data behavior](docs/providers.md).

## Offline quickstart

```bash
python enrich.py --input sample/leads.csv --output results/dryrun.csv
python scripts/test_offline.py
```

The sample contains clearly synthetic identities at reserved example domains, including a duplicate. Output files must use a new path; existing output files are not overwritten. The offline runner blocks outbound DNS and socket connections, runs all unit tests, compiles the Python files, and exercises the sample dry run.

The [GitHub Actions workflow](.github/workflows/tests.yml) runs the same checks on Python 3.10, 3.11, 3.12 and 3.13 for pushes, pull requests and manual test runs. It has read-only repository permissions and does not receive provider secrets. There is no scheduled enrichment workflow.

## Enabling live enrichment later

API access requires your own eligible provider accounts. A free account does not necessarily include free API access or free phone lookups. Confirm the account's current API entitlement, billing cycle, remaining free credits and credit prices before enabling live mode. Local limits cannot establish whether a request is free or prevent a provider changing its charges.

Live mode requires `--live`, an explicit `--credit-cycle YYYY-MM`, and a credit allocation for the operations you intend to use. All provider allocations default to zero. Email-only lookup needs an email allocation. Phone lookup also needs `--phone`, a provider phone allocation and the per-run `--max-phone-credits` bound.

For example, **only after confirming that one Hunter email lookup fits your remaining free allowance**, an email-only run would use:

```bash
python enrich.py --input private/candidates.csv --output results/email-run.csv \
  --providers hunter --live --max-calls 1 \
  --credit-cycle YYYY-MM --email-credits hunter=1
```

Replace `YYYY-MM` with your provider account's applicable cycle. Supply `HUNTER_API_KEY` through the process environment; do not put a key in the command, CSV, source code or git. `.env` files are not loaded automatically. [Configuration](docs/configuration.md) explains each guard and the phone settings. [Security](docs/security.md) documents local secrets and GitHub Secrets.

The persistent ledger defaults to `.private/credits.sqlite3`. It reserves worst-case local credits before each request, including attempts that fail or return no match. All processes using the same provider accounts must share the same ledger. Keep the ledger between runs and do not delete it to recover a budget. Provider activity outside this tool must be deducted from the allowances yourself.

## Input and privacy

CSV identifiers can include `first_name`, `last_name`, `full_name`, `company_name`, `company_domain`, `linkedin_url` and existing `email`/`phone`. At least a LinkedIn person URL, existing email, or a full name with company information is required; Hunter specifically needs first name, last name and company domain. Duplicate contacts are skipped within each input run. This is not a cross-run candidate database.

Store real inputs under `private/` or `data/` and outputs under `results/`. These locations and the quota ledger are ignored by git. Only synthetic sample data belongs in this repository. Review staged files before every commit: `.gitignore` does not remove information already tracked by git.

Use professional contact data only with an appropriate purpose and permission, respect opt-outs and provider terms, and keep access to inputs and results restricted. This tool does not send messages or emails.

Live provider requests have not been executed as part of project setup. API behavior, account entitlement and pricing still need to be checked before any authorized live run. No enrichment schedule is enabled.
