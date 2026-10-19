# Configuration and credit controls

Python 3.10+ and the standard library are sufficient. Credentials are process environment variables; see [security](security.md). `.env` files are not loaded automatically.

## Defaults and limits

| Setting | Default and behavior |
| --- | --- |
| Live requests | Disabled; `--live` explicitly permits budgeted provider requests |
| Discovery | `--providers getprospect,prospeo,hunter`; select added providers explicitly |
| All providers | `--list-providers` lists all 11 offline |
| Provider allocations | Zero; repeat `--email-credits PROVIDER=N` and `--phone-credits PROVIDER=N` |
| Cycle | `--credit-cycle YYYY-MM` required for live operations |
| Persistent ledger | `--credit-ledger .private/credits.sqlite3` |
| Discovery attempts | `--max-calls 10` per run |
| HTTP requests | `--max-http-requests 20`, including authentication and retrieval requests |
| Optional total credit bound | `--max-credit-units N` bounds the current enrichment run, including validation |
| Phone | Off; needs `--phone`, positive `--max-phone-credits N` and provider phone allocation |
| Validation | Off; `--verify` needs live mode and positive `--max-verifications N` |
| Validators | Abstract, ZeroBounce.net and Emailable when verification is enabled; explicit `--validators` selects others |
| Restricted account access | Apollo and Snov require repeatable `--approved-provider NAME` after confirming endpoint entitlement |
| Variable-cost request ceiling | Snov requires `--request-credit-cap snov=N`; no default or guessed credit cost |
| Output | `results/enriched.csv`; new private path, no overwriting |

`--max-calls` does not replace the HTTP bound: OAuth can need another HTTP request. Metadata-only `--balances` remains a manual network action, not an offline check or proof of free entitlement. Use `--help` for both entrypoints.

## Allocating confirmed free credits

Confirm API eligibility, actual free balance, billing-cycle boundaries, current operation costs and disabled paid overages in each provider account before live use. A free website plan may not include free API access. [Provider access notes](../PROVIDERS.md) distinguish verified offers from unconfirmed policies.

Email-only discovery ordinarily reserves one local email unit, except Snov's variable-cost work. Apollo reserves nine. Prospeo phone-inclusive discovery reserves ten phone units total, including any accompanying email result. GetProspect reserves one phone unit plus one email unit when an email lookup is needed; an existing email disables that email lookup. Hunter and added discovery adapters do not reveal phones. Email validation/risk checks each reserve one unit from that provider's email allocation. These assumptions do not guarantee actual billing.

Prospeo can use a shared provider balance: allocate the sum of email and phone allowances within its confirmed free remainder. Reserve enough for asynchronous submission and later retrieval; Dropcontact retrieval conservatively reserves another email unit, even if the provider may include it in the original charge. Failed/no-match requests keep their reservations, and no request is automatically retried.

Snov charges one credit per returned valid/unknown email, and its documented result array has no confirmed finite per-input upper bound. Snov submission and retrieval therefore require `--request-credit-cap snov=N` in addition to the access-confirmation flag and sufficient persistent email allocation. `N` must be a provider/account-enforced, confirmed worst-case ceiling for one request, not an arbitrary estimate or expected match count. The tool does not invent an API parameter to enforce it. If no finite ceiling is confirmed, Snov remains blocked before any HTTP request. Retrieval conservatively reserves that ceiling again because a standalone job's charge timing cannot safely be assumed.

For example, only after confirming sufficient free GetProspect API allowance:

```bash
python enrich.py --input private/candidates.csv --output results/phone-run.csv \
  --providers getprospect --live --phone --max-calls 1 --max-http-requests 1 \
  --max-phone-credits 1 --credit-cycle YYYY-MM \
  --email-credits getprospect=1 --phone-credits getprospect=1
```

For validation only, after confirming the Abstract allowance:

```bash
python enrich.py --input private/candidates.csv --output results/validated.csv \
  --live --verify --validators abstract --max-verifications 1 \
  --max-calls 0 --max-http-requests 1 --credit-cycle YYYY-MM \
  --email-credits abstract=1
```

`zerobounce_ai` must be explicitly selected under `--validators`; it returns risk evidence, never mailbox verification. Keyless access still requires `--live`, a provider allocation and request limits.

## Persistent accounting

The SQLite ledger reserves credits atomically before network work. All processes using the same accounts must share it on persistent storage supporting SQLite locks. Keep restricted permissions and a backup; never delete, move or replace it to recover a budget. Outside account activity must be deducted yourself.

Initial cycle allocations are persisted and can only be lowered, not raised in the same cycle. Repeat every prior provider allocation on later commands, including retrieval: omitted allocations are zero and may lower persisted caps. Changing `--credit-cycle` is an explicit operator decision, not an automatic monthly reset; do so only after providers grant new allowances.

Existing three-provider ledgers migrate transactionally when opened by this version. Original usage/caps remain. Added providers can receive their initial caps during that first migration for the selected cycle; omitted added-provider caps become zero, and historic cycles receive zero for added providers. Back up the ledger and include all intended allocations before the first upgraded live run.

If ledger access fails during a run, all later provider requests stop. Completed rows and pending task IDs are retained in the private output, affected rows show `credit_ledger_error` / `credit_ledger:unavailable`, and the CLI exits with status 2. Investigate the ledger before rerunning; do not reset it.

## Asynchronous retrieval

Dropcontact and Snov may return `pending_task`; submission does not poll automatically. Keep the resulting CSV private. `resume_pending.py` accepts only existing task identifiers and never submits new jobs. Without `--live`, it performs an offline dry run.

An authorized retrieval uses the same ledger, cycle and all prior allocations, with explicit limits. For example, if the original Dropcontact cycle allocation was two units and only one was consumed by submission:

```bash
python resume_pending.py --input results/pending.csv --output results/resumed.csv \
  --live --credit-cycle YYYY-MM --credit-ledger .private/credits.sqlite3 \
  --email-credits dropcontact=2 --max-calls 1 --max-http-requests 1
```

Include all other allocations originally configured in that ledger as well. Snov retrieval also requires `--approved-provider snov`, the confirmed `--request-credit-cap snov=N`, ledger allocation covering that ceiling, and enough HTTP allowance for OAuth plus retrieval. Do not retrieve Snov jobs when a finite provider/account-enforced ceiling cannot be confirmed. A still-pending job is retained for a later explicit retrieval. There is no automatic polling loop or schedule.
