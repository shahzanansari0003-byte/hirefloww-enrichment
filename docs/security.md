# Secrets and private candidate data

## Local credentials

The only credential variables are:

| Variable | Provider |
| --- | --- |
| `PROSPEO_API_KEY` | Prospeo |
| `GETPROSPECT_API_KEY` | GetProspect |
| `HUNTER_API_KEY` | Hunter |

Use an operating-system secret store or your deployment platform's secret manager to inject these variables into the enrichment process. Configure only providers you intend to use, and use least-privilege keys when the provider supports them. Keep separate development and production keys.

`.env.example` contains empty placeholders and is safe to track. If you maintain a private `.env` file, restrict its permissions and load it through a trusted launcher; the application does not load it automatically. Do not paste keys into chat, commands saved in shell history, source code, CSV files, output files, tests or issue descriptions. Do not enable shell tracing around secrets.

Provider keys are sent only to the fixed official provider endpoints implemented in the source. Hunter requires its key as a query parameter; avoid HTTP debug logging, URL logging and proxies that retain query strings. Error output must never include provider response bodies or complete authenticated request URLs.

## GitHub Secrets

The test workflow does not need provider keys and intentionally does not reference GitHub Secrets. Leave it that way. GitHub's automatically issued repository token is limited to read-only access, and checkout does not persist it in local git configuration.

For a separately approved future runtime workflow:

1. Open the repository's **Settings → Secrets and variables → Actions**.
2. Prefer a protected GitHub Environment with required reviewers for live enrichment.
3. Add only the necessary secrets, named exactly `PROSPEO_API_KEY`, `GETPROSPECT_API_KEY` and/or `HUNTER_API_KEY`.
4. Map each secret to its matching environment variable only within the approved live job. Never embed secret values in YAML, pass them as command-line arguments or print them.
5. Keep live jobs away from untrusted pull-request code. Do not use `pull_request_target` to run changes with provider credentials. Review changes to scripts and workflows before any live job.
6. Supply private input data outside git and persist the credit ledger in a restricted store shared by all runs. An ephemeral Actions runner's filesystem alone does not preserve quotas between runs. Protect results and avoid broadly visible logs or artifacts.

Creating secrets alone does not activate enrichment. There is no live workflow or schedule in this project. Scheduling requires the repository owner's explicit approval.

## Data handling and repository review

Only synthetic example data belongs in git. Real candidate inputs, results, caches and quota databases must stay private. Recommended locations are `private/` or `data/` for inputs, `results/` for outputs, and `.private/` for the ledger. These are ignored by git, along with CSVs other than the synthetic sample.

Before committing, inspect `git status` and `git diff --cached` and check that no keys, candidate records or generated outputs are staged. Ignore patterns are an aid, not an access control: they do not remove tracked data and can be overridden. Do not force-add private files.

Restrict local filesystem access, protect storage and backups, and apply your organization's retention policy to candidate data. Do not use a shared output location that unauthorized users can read. Deleting an output does not undo its exposure if it was already committed or uploaded.

If a key is exposed, revoke and rotate it immediately, then remove it from files and repository history as appropriate. If candidate data is exposed, follow your organization's incident and privacy process; deleting the latest file version is not sufficient.
