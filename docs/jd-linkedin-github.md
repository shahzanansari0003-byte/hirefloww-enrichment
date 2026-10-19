# Hirefloww: JD → LinkedIn Boolean/X-ray → GitHub-only emails

Workflow file: .github/workflows/jd-linkedin-github.yml

## Automatic trigger through a GitHub Issue

Without opening the Actions form, create an issue in this **private** repository
with title beginning `[HIREFLOWW_JD]`. Put the JD directly in the issue body.
The workflow automatically runs a ZERO Brave-credit preflight and generates
Boolean/X-ray search links. It can also consume **optional explicitly paired**
candidate profiles from the same issue body in a fenced github-pairs section:

~~~github-pairs
name,linkedin_url,github_url
Example Person,https://www.linkedin.com/in/example-person,https://github.com/example-person
~~~

Pairs result in GitHub-only public profile/commit-email lookup; there is no
Brave X-ray API spending in the issue-triggered automatic mode.
To auto-discover indexed LinkedIn profiles, use the Actions manual mode with
the Brave secret and explicit confirmed-free flags (see section below).

## 1. Start

Go to GitHub Actions > JD -> LinkedIn Xray -> GitHub-only email > Run workflow.
Paste a text JD in job_description (e.g. ServiceNow Technical Lead with mandatory skills and experience).
The script generates Google/Bing Boolean X-ray search links as a CSV.

Optionally paste candidate_pairs_csv (names and professional profile URLs only):

~~~csv
name,linkedin_url,github_url
Example Person,https://www.linkedin.com/in/example-person,https://github.com/example-person
~~~

Use live_xray=false (default) to generate queries and optionally perform GITHUB-only contact attribution on paired candidates. This does not use a paid enrichment provider.

## 2. Automatic public LinkedIn X-ray search (optional Brave Search API)

This workflow does not scrape, log into, or bypass LinkedIn. It can optionally use the Brave Web Search API for publicly indexed LinkedIn /in/ URLs.
Brave is used for SEARCH only; GitHub is the only email source.
Brave advertises $5 monthly free credits but requires payment-card verification and does not offer a separate free plan. For zero spend, set prepay to $0 and verify no overage billing before using it.
Official pricing: https://api-dashboard.search.brave.com/documentation/pricing
Create a GitHub Actions Secret named BRAVE_SEARCH_API_KEY. Do not paste the key into a JD, issue body, source, logs or chat.
Enable live_xray=true and confirm_free_search=true only after verifying free credit eligibility. Start max_queries=1; cap is 3 queries per run.
Without a Brave key or explicit confirmation, the workflow generates search links but does NOT automatically discover LinkedIn profiles.

## 3. Only GitHub for email enrichment

The script tries to link the LinkedIn person to a GitHub account using an exact LinkedIn URL cross-linked from that person's public GitHub bio/blog.
Alternatively provide an explicit linkedin_url-to-github_url pair; result is marked manually_paired_unverified unless independently cross-linked.
GitHub public profile email and recent public commits from the matched exact Github login are the ONLY email lookup sources.
Unknown or ambiguous identity -> no email; it NEVER guesses email addresses or matches strangers just by names.
No Clay, Apollo, Prospeo, Lusha, Hunter or other email enrichment provider is called.
Public GitHub commit attribution is NOT independent mailbox verification, nor proof of employment or availability.

## 4. Output

Private GitHub Actions artifact (1 day retention):
- boolean_xray_queries.csv: search terms and browser links
- candidates.csv: indexed LinkedIn candidates or explicit input pairs, GitHub email and source evidence
- summary.json: counts; provider type; number of API calls

GitHub token is used only with read-only permissions. Private inputs and emails are not logged.
Not implemented: LinkedIn scraping, PDF/DOCX native parsing, bulk account billing controls, reliable LinkedIn-GitHub automatic match without cross-links, JD qualification guarantees, salary/notice confirmation, automatic outreach.

Offline tests: tests/test_jd_linkedin_github.py. Python standard library only; JD input as pasted text or .txt/.md file.

CLI: python scripts/jd_linkedin_github.py --jd-file private/jd.txt --pairs-file private/pairs.csv --output-dir results/jd-github
