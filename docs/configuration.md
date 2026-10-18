# Configuration and credit controls

`enrich.py` uses the Python standard library. It reads provider keys only from `PROSPEO_API_KEY`, `GETPROSPECT_API_KEY` and `HUNTER_API_KEY` in the process environment. It does not automatically read `.env` or `.env.example`.

## Defaults

| Setting | Default | Purpose |
| --- | --- | --- |
| Live requests | Disabled | `--live` explicitly permits credit-consuming provider requests |
| Provider order | `getprospect,prospeo,hunter` | `--providers` selects providers and fallback order |
| Per-provider email allocation | Zero | Repeat `--email-credits PROVIDER=N` for eligible providers |
| Per-provider phone allocation | Zero | Repeat `--phone-credits PROVIDER=N` for eligible providers |
| Credit cycle | None | `--credit-cycle YYYY-MM` is required for live mode |
| Credit ledger | `.private/credits.sqlite3` | `--credit-ledger PATH` chooses the shared persistent ledger |
| Per-run lookup bound | 10 | `--max-calls N` caps attempted provider lookups |
| Phone lookup | Disabled | `--phone` requires live mode and a phone request budget |
| Per-run phone credit bound | Zero | `--max-phone-credits N` caps estimated phone requests in the run |
| Output | `results/enriched.csv` | A new output path is required; existing files are protected |

Use `python enrich.py --help` for the full command-line reference. Limit options are CLI arguments, not environment variables. Unknown providers, malformed cycles and negative limits are rejected.

## Allocating only confirmed free credits

Before enabling live mode, check your provider dashboard for API eligibility, current credit costs, billing-cycle boundaries, unused free credits and any overage billing setting. Disable paid overages in the provider account when available. Do not assume that a free web plan includes free API use.

Allocate only credits you have confirmed remain free after accounting for other apps, manual activity and other users. Each email-only lookup reserves one email credit. A Prospeo phone-inclusive request reserves ten credits in the phone allocation, which includes any accompanying email result. GetProspect reserves one phone credit plus one email credit if an email lookup is needed; it disables email enrichment when an existing email is supplied. Hunter does not support phone lookup. These are local upper-bound assumptions, not current provider pricing guarantees.

Prospeo can charge email and phone work against a shared credit pool. Allocate email plus phone limits so their sum fits the actual remaining allowance; separate local counters do not create separate provider balances.

For example, **after verifying enough free credits**, one GetProspect phone-inclusive request is configured with:

```bash
python enrich.py --input private/candidates.csv --output results/phone-run.csv \
  --providers getprospect --live --phone --max-calls 1 --max-phone-credits 1 \
  --credit-cycle YYYY-MM --email-credits getprospect=1 --phone-credits getprospect=1
```

For a single Prospeo phone-inclusive request, the analogous phone allocation and per-run phone bound must each cover ten credits; that request does not additionally reserve an email credit. A separate email allocation is needed only if you also permit email-only requests. Do not execute these examples merely to validate configuration: even unsuccessful requests may use credits.

## Persistent ledger behavior

Credits are reserved atomically in SQLite before a provider request, so concurrent processes sharing one ledger cannot each spend the same local allowance. Reservations remain spent locally after provider errors, timeouts and no-match results, because a failed response does not prove the provider made no charge.

The initial per-provider allocations for a cycle are persisted. Subsequent runs can lower the limits but cannot raise them for the same cycle. Reuse the same ledger for every process using the same provider accounts. Keep it on persistent local storage with restricted permissions and a backup. Do not share it through git or place it on a filesystem that cannot safely support SQLite locking.

There is no automatic monthly reset. Change `--credit-cycle` only after the provider has actually granted a new allowance, and reconcile outside activity first. Selecting another cycle, moving or deleting the ledger, or using different ledgers bypasses local accounting; operators must avoid those actions while an allowance is still in use. Providers have different cycle start dates, so the month label must follow an explicitly agreed operational cycle.

The ledger is a local control, not a provider balance or billing authority. Never rely on it alone to ensure requests are free. Zero allocations and dry-run mode are the safe defaults until entitlement is confirmed.

## Manual balance checks and scheduling

`python enrich.py --balances` makes metadata requests to configured Prospeo/GetProspect accounts. It is not part of the offline checks and does not enable enrichment. Balance responses still require care and do not establish that future requests will be free. No balance request or live lookup was needed for repository setup.

No schedule or automated enrichment job is included. GitHub Actions only runs offline checks. Add any live or scheduled job only after explicit approval, a review of costs and privacy, a persistent shared ledger, and secure input/output storage.
