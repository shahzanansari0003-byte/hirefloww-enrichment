# Hirefloww: Automatic online candidate queue

GitHub Actions workflow: `.github/workflows/auto-candidate-queue.yml`.

## Using it

1. In this **private** GitHub repository, create an Issue with a title starting exactly `[HIREFLOWW_QUEUE]` (or apply the `candidate-queue` label if it exists).
2. Add one CSV block delimited by `~~~csv` and `~~~`, with headings `full_name,linkedin_url,company_name`.
3. Enter up to 50 candidate profiles total across all open queue issues. Add public LinkedIn `/in/` URLs and optional name/employer. Do not add email addresses, phone numbers, or resumes to the Issue.
4. GitHub Actions starts on issue creation/change, and checks automatically once per day. You can also run it manually from the Actions tab.
5. Find the result in the Action's **Artifacts**. Reports expire after 1 day.

## Example issue body (synthetic)

~~~csv
full_name,linkedin_url,company_name
Example Candidate,https://www.linkedin.com/in/example-profile,Example Company
~~~

## What is automated

- Read only eligible open queue issues in the private repository.
- Merge and deduplicate LinkedIn profiles without logging candidate names.
- Validate URLs and limit the entire queue to 50 unique people.
- Run `enrich.py` **offline** against the candidate list, generating candidate statuses.
- Upload an ephemeral report (1-day retention); no credentials or candidate input is committed to git.
- Run automatically on issue events and daily scheduling (actual GitHub scheduled start may be delayed).

## What is NOT yet automated

- Candidate discovery from a JD: there is no LinkedIn scraping or recruiting platform search integration.
- Real contact enrichment: this scheduled workflow makes zero third-party API requests and spends zero provider credits.
- Email verification, phone reveal, or contacting candidates: not performed.

Use the existing guarded manual single-candidate workflows only when free API eligibility has been confirmed and paid overages disabled. Unattended live enrichment additionally requires a durable external credit ledger (GitHub runners are ephemeral), vendor-side spending caps and account-specific API entitlement; adding keys or GitHub Actions secrets by itself does not establish these safeguards.

Only authorized repository collaborators can read private issue bodies and their Action artifacts, so limit collaborator access and never include sensitive candidate details.
