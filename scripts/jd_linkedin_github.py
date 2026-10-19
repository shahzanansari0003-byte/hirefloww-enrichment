#!/usr/bin/env python3
"""JD -> LinkedIn Boolean/X-ray search -> GitHub-only publicly attributed email.

No LinkedIn scraping. X-ray live search uses a separately configured Brave Search
API (search only); emails come exclusively from public GitHub profile/commits.
Python standard library only. The output is private and never printed to logs.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlencode, urlsplit, unquote
from urllib.request import Request, urlopen

SKILLS = (
    "ServiceNow", "CSM", "Virtual Agent", "CMDB", "Now Assist", "IntegrationHub",
    "Integrations", "REST", "SOAP", "ITSM", "ITOM", "Discovery", "Flow Designer",
    "JavaScript", "GlideRecord", "Script Includes", "Service Catalog",
    "Python", "Java", "React", "Salesforce", "AWS", "Azure", "Kubernetes",
)
LOCATIONS = ("Pune", "Bengaluru", "Bangalore", "Hyderabad", "Chennai", "Delhi", "Mumbai", "India")
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
LINKEDIN = re.compile(r"^/in/([A-Za-z0-9_-]{2,120})/?$")
GITHUB_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
FIELDS = ("name", "linkedin_url", "github_url", "email", "email_source", "identity_status",
          "email_status", "jd_keywords_seen", "matching_status", "search_source", "evidence_url")


def public_linkedin(url: str) -> str:
    p = urlsplit(url.strip())
    if (p.scheme != "https" or p.hostname not in ("www.linkedin.com", "linkedin.com")
            or p.username or p.password or p.port or not LINKEDIN.fullmatch(p.path)):
        raise ValueError("LinkedIn /in/ URL required")
    return "https://www.linkedin.com" + p.path.rstrip("/")


def github_login(url: str) -> str:
    p = urlsplit(url.strip())
    if (p.scheme != "https" or p.hostname != "github.com" or p.username or p.password or p.port
            or not GITHUB_LOGIN.fullmatch(p.path.strip("/"))):
        raise ValueError("Individual GitHub profile URL required")
    return p.path.strip("/")


def read_jd(jd: str) -> dict[str, object]:
    jd = jd.strip()
    if not 12 <= len(jd) <= 16000:
        raise ValueError("Provide a JD between 12 and 16000 characters")
    line = next((x.strip() for x in jd.splitlines() if x.strip()), "Technical Lead")
    m = re.search(r"(?:job\s*title|position|role)\s*:\s*(.+)", jd, re.I)
    role = (m.group(1) if m else line).strip(" -#*")[:95]
    if len(role) < 3:
        role = "ServiceNow Developer"
    skills = [skill for skill in SKILLS if re.search(r"(?<![A-Za-z])" + re.escape(skill) + r"(?![A-Za-z])", jd, re.I)]
    loc = [name for name in LOCATIONS if re.search(r"\b" + re.escape(name) + r"\b", jd, re.I)]
    exp = re.search(r"\b(\d{1,2})\s*\+?\s*(?:years|yrs)\b", jd, re.I)
    return {"role": role, "skills": skills, "locations": loc,
            "min_years_mentioned": int(exp.group(1)) if exp else None}


def boolean_queries(spec: dict[str, object]) -> list[dict[str, str]]:
    skills = list(spec["skills"]) or ["ServiceNow"]
    role = str(spec["role"])
    # X-ray uses indexed public profiles; it does not log in or scrape LinkedIn.
    lead = " OR ".join('"' + x + '"' for x in skills[:3])
    alt = " OR ".join('"' + x + '"' for x in skills[1:5]) or lead
    title = '("Technical Lead" OR "Architect" OR "Developer")'
    area = '("Pune" OR "Bengaluru" OR "Hyderabad" OR "India")' if ("India" in spec["locations"] or "Pune" in spec["locations"] or "Bengaluru" in spec["locations"]) else ""
    qs = [
        f'site:linkedin.com/in/ {title} ({lead}) {area} -jobs -recruiter -hiring',
        f'site:linkedin.com/in/ ({alt}) ("ServiceNow" OR "Technical Architect") -jobs -recruiter',
        f'site:linkedin.com/in/ "{role}" ("India" OR "Bengaluru" OR "Hyderabad" OR "Pune")',
    ]
    unique = list(dict.fromkeys(q[:550].strip() for q in qs))
    return [{"query": q,
             "google_xray_url": "https://www.google.com/search?" + urlencode({"q": q}),
             "bing_xray_url": "https://www.bing.com/search?" + urlencode({"q": q})}
            for q in unique]


def request_json(url: str, *, headers: dict[str, str], limit: int = 900000) -> dict:
    if urlsplit(url).scheme != "https":
        raise ValueError("HTTPS required")
    req = Request(url, headers=headers)
    with urlopen(req, timeout=18) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("API response too large")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object")
    return value


def web_xray(queries: list[dict[str, str]], *, api_key: str,
             max_queries: int, max_profiles: int) -> list[dict[str, str]]:
    if not api_key:
        raise ValueError("BRAVE_SEARCH_API_KEY missing; search stays manual")
    found = {}
    for item in queries[:max_queries]:
        url = "https://api.search.brave.com/res/v1/web/search?" + urlencode(
            {"q": item["query"], "count": 20, "country": "IN", "search_lang": "en"})
        data = request_json(url, headers={"Accept": "application/json",
                                           "X-Subscription-Token": api_key})
        web = data.get("web") or {}
        for hit in web.get("results", [])[:20]:
            if not isinstance(hit, dict):
                continue
            try:
                linkedin = public_linkedin(str(hit.get("url") or "").split("?")[0])
            except ValueError:
                continue
            if linkedin not in found:
                raw_title = re.split(r"\s*(?:[|–—]|\s-\s)\s*", str(hit.get("title") or ""))[0].strip()
                name = raw_title if re.fullmatch(r"[\w .'-]{5,80}", raw_title) and len(raw_title.split()) >= 2 else ""
                found[linkedin] = {"name": name, "linkedin_url": linkedin,
                                    "github_url": "", "snippet": str(hit.get("description") or "")[:450],
                                    "search_source": "Brave indexed LinkedIn X-ray"}
            if len(found) >= max_profiles:
                return list(found.values())
    return list(found.values())


def manual_pairs(source: str) -> list[dict[str, str]]:
    if not source.strip():
        return []
    reader = csv.DictReader(io.StringIO(source, newline=""))
    if not reader.fieldnames or not {"linkedin_url", "github_url"}.issubset(set(reader.fieldnames)):
        raise ValueError("Pairs CSV requires linkedin_url,github_url; name optional")
    if any(x not in ("name", "linkedin_url", "github_url") for x in reader.fieldnames):
        raise ValueError("Pairs CSV allows only name,linkedin_url,github_url")
    rows = []
    for row in reader:
        if None in row:
            raise ValueError("Malformed pairs CSV")
        name = (row.get("name") or "").strip()
        if len(name) > 120:
            raise ValueError("Candidate name too long")
        linkedin = public_linkedin(row.get("linkedin_url") or "")
        github = "https://github.com/" + github_login(row.get("github_url") or "")
        rows.append({"name": name, "linkedin_url": linkedin, "github_url": github,
                     "snippet": "", "search_source": "User-supplied profile pair"})
        if len(rows) > 50:
            raise ValueError("Maximum 50 profile pairs")
    return rows


def email_ok(value: str) -> bool:
    email = value.strip()
    if not EMAIL.fullmatch(email) or len(email) > 254:
        return False
    lower = email.lower()
    return not (lower.endswith(".local") or "noreply" in lower
                or lower.endswith("@example.com") or lower.startswith("admin@")
                or lower.startswith("test@") or lower.startswith("p16574@"))


def profile_backlink(profile: dict, linkedin_url: str) -> bool:
    for field in ("blog", "bio"):
        s = str(profile.get(field) or "")
        links = re.findall(r"https?://(?:www\.)?linkedin\.com/in/[A-Za-z0-9_-]+", s, re.I)
        for match in links:
            try:
                if public_linkedin(match.replace("http://", "https://")) == linkedin_url:
                    return True
            except ValueError:
                pass
    return False


class Github:
    def __init__(self, token: str, max_calls: int = 140):
        self.token = token
        self.max_calls = max_calls
        self.calls = 0

    def get(self, route: str) -> object:
        if self.calls >= self.max_calls:
            raise ValueError("GitHub request budget exceeded")
        if not route.startswith("/") or "//" in route:
            raise ValueError("Invalid GitHub API route")
        self.calls += 1
        data = request_json("https://api.github.com" + route,
                            headers={"Accept": "application/vnd.github+json",
                                     "User-Agent": "hirefloww-github-only-enrichment",
                                     **({"Authorization": "Bearer " + self.token} if self.token else {})})
        return data

    def get_list(self, route: str) -> list:
        # List response is not a JSON object, so decode explicitly here.
        if self.calls >= self.max_calls:
            raise ValueError("GitHub request budget exceeded")
        if not route.startswith("/") or "//" in route:
            raise ValueError("Invalid GitHub API route")
        self.calls += 1
        req = Request("https://api.github.com" + route, headers={
            "Accept": "application/vnd.github+json", "User-Agent": "hirefloww-github-only-enrichment",
            **({"Authorization": "Bearer " + self.token} if self.token else {})})
        with urlopen(req, timeout=18) as response:
            raw = response.read(900001)
        if len(raw) > 900000:
            raise ValueError("GitHub response too large")
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError("Expected GitHub list")
        return data


def auto_link_github(client: Github, name: str, linkedin_url: str) -> str:
    if not name or not re.fullmatch(r"[\w .'-]{5,80}", name):
        return ""
    q = urlencode({"q": name + " in:name", "per_page": 5})
    matches = client.get("/search/users?" + q).get("items", [])[:5]
    for item in matches:
        login = str(item.get("login") or "")
        if not GITHUB_LOGIN.fullmatch(login):
            continue
        profile = client.get("/users/" + login)
        if profile.get("type") == "User" and profile_backlink(profile, linkedin_url):
            return "https://github.com/" + login
    return ""


def github_email(client: Github, login: str) -> tuple[str, str]:
    profile = client.get("/users/" + login)
    if profile.get("type") != "User" or profile.get("login", "").lower() != login.lower():
        return "", ""
    direct = profile.get("email")
    if isinstance(direct, str) and email_ok(direct):
        return direct, "https://github.com/" + login
    repos = client.get_list("/users/" + login + "/repos?per_page=4&sort=pushed&type=owner")
    for repo in repos[:4]:
        full_name = str(repo.get("full_name") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name):
            continue
        commits = client.get_list("/repos/" + full_name + "/commits?" + urlencode(
            {"author": login, "per_page": 15}))
        for commit in commits:
            author = commit.get("author") or {}
            c = commit.get("commit") or {}
            meta = c.get("author") or {}
            email = str(meta.get("email") or "")
            # GitHub account attribution is mandatory; bare commit name is NOT enough.
            if str(author.get("login") or "").lower() == login.lower() and email_ok(email):
                return email, str(commit.get("html_url") or "")
    return "", ""


def candidate_report(spec: dict, profiles: list[dict[str, str]],
                     client: Github, max_profiles: int) -> list[dict[str, str]]:
    results = []
    seen = set()
    for profile in profiles:
        linkedin = profile["linkedin_url"]
        if linkedin in seen:
            continue
        seen.add(linkedin)
        if len(results) >= max_profiles:
            break
        name = profile.get("name", "")
        github = profile.get("github_url", "")
        source = profile.get("search_source", "")
        status = "no_cross_link_confirmed"
        email, evidence = "", ""
        if not github:
            github = auto_link_github(client, name, linkedin)
            if github:
                status = "github_links_to_linkedin"
        if github:
            login = github_login(github)
            public = client.get("/users/" + login)
            if profile_backlink(public, linkedin):
                status = "github_links_to_linkedin"
            elif source == "User-supplied profile pair":
                status = "manually_paired_unverified"
            else:
                status = "no_cross_link_confirmed"
            if status in ("github_links_to_linkedin", "manually_paired_unverified"):
                email, evidence = github_email(client, login)
        snippet = profile.get("snippet", "")
        matched = [s for s in spec["skills"] if re.search(
            r"(?<![A-Za-z])" + re.escape(s) + r"(?![A-Za-z])", snippet, re.I)]
        results.append({"name": name, "linkedin_url": linkedin, "github_url": github,
                        "email": email, "email_source": "GitHub public commit/profile" if email else "",
                        "identity_status": status,
                        "email_status": "publicly_attributed_not_mailbox_verified" if email else "not_found",
                        "jd_keywords_seen": "; ".join(matched),
                        "matching_status": "unverified_experience_skills_notice_ctc",
                        "search_source": source, "evidence_url": evidence})
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--jd-text")
    group.add_argument("--jd-file")
    parser.add_argument("--pairs-file", help="Private CSV with linkedin_url,github_url,name")
    parser.add_argument("--pairs-env", action="store_true", help="Read private pairs from CANDIDATE_PAIRS_CSV")
    parser.add_argument("--live-xray", action="store_true", help="Use optional Brave web-search API for indexed public LinkedIn search")
    parser.add_argument("--confirm-free-search", action="store_true", help="You confirmed prepaid balance 0 and available free Brave API credits")
    parser.add_argument("--max-queries", type=int, default=2)
    parser.add_argument("--max-profiles", type=int, default=25)
    parser.add_argument("--output-dir", default="results/jd-github")
    args = parser.parse_args(argv)
    try:
        if args.max_queries not in (1, 2, 3) or not 1 <= args.max_profiles <= 50:
            raise ValueError("Use 1–3 searches and 1–50 profiles")
        if args.live_xray and not args.confirm_free_search:
            raise ValueError("Free search credit confirmation required")
        jd = args.jd_text if args.jd_text is not None else Path(args.jd_file).read_text(encoding="utf-8")
        spec = read_jd(jd)
        queries = boolean_queries(spec)
        src = ""
        if args.pairs_file:
            src = Path(args.pairs_file).read_text(encoding="utf-8")
        if args.pairs_env:
            src += ("\n" if src else "") + os.environ.get("CANDIDATE_PAIRS_CSV", "")
        profiles = manual_pairs(src) if src.strip() else []
        if args.live_xray:
            profiles += web_xray(queries, api_key=os.environ.get("BRAVE_SEARCH_API_KEY", ""),
                                 max_queries=args.max_queries, max_profiles=args.max_profiles)
        output = Path(args.output_dir)
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
        qpath = output / "boolean_xray_queries.csv"
        with qpath.open("x", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=("query", "google_xray_url", "bing_xray_url"))
            writer.writeheader()
            writer.writerows(queries)
        client = Github(os.environ.get("GITHUB_TOKEN", ""))
        records = candidate_report(spec, profiles, client, args.max_profiles)
        with (output / "candidates.csv").open("x", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(records)
        summary = {"role": spec["role"], "skills": spec["skills"],
                   "profiles_found": len(records), "github_emails_found": sum(bool(x["email"]) for x in records),
                   "identity_verified": sum(x["identity_status"] == "github_links_to_linkedin" for x in records),
                   "github_requests": client.calls, "xray_queries_made": min(args.max_queries, len(queries)) if args.live_xray else 0,
                   "linkedin_scraping": False, "email_provider": "GitHub public profile/commits only"}
        (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        # Never include names, profiles or emails in CI logs.
        print("JD queries generated; candidates:", len(records), "GitHub-only public emails:",
              summary["github_emails_found"], "X-ray live:", args.live_xray)
        return 0
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        # Fail closed without leaking PII, URLs or tokens in exceptions.
        print("JD/X-ray/GitHub enrichment stopped: invalid inputs, permission, or provider configuration.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
