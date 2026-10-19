# Secrets and private candidate data

## Environment variables

Entrypoints read credentials only from the process environment; `.env` is not automatically loaded. Use an operating-system or deployment secret manager. `.env.example` contains empty placeholders, never credentials.

| Provider | Required variable names |
| --- | --- |
| GetProspect | `GETPROSPECT_API_KEY` |
| Prospeo | `PROSPEO_API_KEY` |
| Hunter | `HUNTER_API_KEY` |
| Tomba | `TOMBA_API_KEY`, `TOMBA_SECRET_KEY` |
| Dropcontact | `DROPCONTACT_API_KEY` |
| Apollo | `APOLLO_API_KEY` |
| Snov.io | `SNOV_CLIENT_ID`, `SNOV_CLIENT_SECRET` |
| Abstract | `ABSTRACT_EMAIL_API_KEY` |
| ZeroBounce.net | `ZEROBOUNCE_API_KEY` |
| Emailable | `EMAILABLE_API_KEY` |
| zerobounce.ai | No key; separate explicit risk-only selection |

Configure only providers you use. Keep development and production keys separate and restrict provider scopes when available. Do not paste keys into chat, source, shell history, CSVs, tests, workflow YAML or logs. Do not enable shell tracing around secrets. Some official APIs require keys in query strings, so authenticated URL/debug/proxy logging must be avoided. Fixed official hosts and sanitized errors reduce exposure; no test needs real keys.

## GitHub Secrets

The workflow runs offline checks only. It does not reference provider secrets, has read-only repository permissions and does not persist checkout credentials. Keep provider secrets out of the test job.

For a separately approved future live workflow:

1. Open repository **Settings → Secrets and variables → Actions**; prefer a protected GitHub Environment with required reviewers.
2. Add only required secrets, using the exact variable names above. Map them to runtime environment variables only in the approved live job; never print them or pass them as command-line arguments.
3. Keep untrusted pull-request code away from provider credentials. Do not execute such changes through `pull_request_target` with secrets.
4. Store candidate input/results privately outside git and preserve one restricted credit ledger across every run. Ephemeral Actions files alone cannot maintain quotas.
5. Review plan entitlements, caps, request limits, overages and pending-job handling before authorizing live work. Snov additionally requires a confirmed finite provider/account-enforced credit ceiling per request, supplied as `--request-credit-cap snov=N`; leave it disabled if no such ceiling is confirmed. Adding secrets or access-confirmation flags does not authorize paid spending.

No live workflow or schedule is present. Scheduling requires the repository owner's explicit approval.

## Candidate data and state

Only synthetic sample data belongs in git. Real inputs belong under `private/`, `contacts/` or `data/`; outputs under `results/`; the ledger under `.private/`. Pending task IDs and validation/risk results are private data too. Apply restricted permissions, protected backups and appropriate retention. Data sent to any selected provider, including keyless zerobounce.ai, leaves your system; confirm purpose and authority first.

Before every commit inspect `git status` and `git diff --cached`. Ignore rules do not remove tracked information and can be overridden; never force-add private inputs, outputs, ledger files or populated secrets. Rotate exposed keys immediately and follow the organization's privacy incident process for exposed candidate data. Deleting the latest version does not remove repository history.
