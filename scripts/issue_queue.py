#!/usr/bin/env python3
"""Import candidate profiles from private GitHub issues. No provider requests."""
from __future__ import annotations
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

LABEL = "candidate-queue"
MAX_RECORDS = 50
CSV_BLOCK = re.compile(r"~~~csv\s*\n(.*?)\n~~~", re.IGNORECASE | re.DOTALL)
URL_PATH = re.compile(r"/in/[A-Za-z0-9_-]{2,120}/?\Z")


def canonical_url(value: str) -> str:
    if not value or len(value) > 400:
        raise ValueError("Invalid profile URL")
    parsed = urlsplit(value.strip())
    if (parsed.scheme != "https"
            or parsed.hostname not in ("linkedin.com", "www.linkedin.com")
            or parsed.username or parsed.password or parsed.port
            or not URL_PATH.fullmatch(parsed.path)):
        raise ValueError("Only HTTPS LinkedIn /in/ profile URLs are supported")
    return "https://www.linkedin.com" + parsed.path.rstrip("/")


def parse_issue(body: str) -> list[dict[str, str]]:
    if not isinstance(body, str) or len(body) > 64000:
        raise ValueError("Invalid issue body")
    blocks = CSV_BLOCK.findall(body)
    if len(blocks) != 1:
        raise ValueError("Issue needs one fenced ~~~csv block")
    reader = csv.DictReader(io.StringIO(blocks[0], newline=""))
    if not reader.fieldnames:
        raise ValueError("CSV headers missing")
    fieldnames = [name.strip() for name in reader.fieldnames]
    allowed = {"full_name", "linkedin_url", "company_name"}
    if (len(set(fieldnames)) != len(fieldnames) or "linkedin_url" not in fieldnames
            or any(name not in allowed for name in fieldnames)):
        raise ValueError("Issue supports only name, LinkedIn and company metadata")
    rows = []
    for row in reader:
        if None in row:
            raise ValueError("Malformed CSV row")
        name = (row.get("full_name") or "").strip()
        company = (row.get("company_name") or "").strip()
        if len(name) > 150 or len(company) > 150:
            raise ValueError("Candidate metadata too long")
        rows.append({"full_name": name, "linkedin_url": canonical_url(row.get("linkedin_url") or ""),
                     "company_name": company})
        if len(rows) > MAX_RECORDS:
            raise ValueError("Issue exceeds 50 candidates")
    return rows


def fetch_queue_issues(repo: str, token: str) -> list[dict[str, str]]:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo) or not token:
        raise ValueError("GitHub access unavailable")
    request = Request(
        f"https://api.github.com/repos/{repo}/issues?state=open&per_page=100",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": "Bearer " + token,
                 "X-GitHub-Api-Version": "2022-11-28"})
    with urlopen(request, timeout=20) as response:
        data = json.loads(response.read(2_000_000))
    if not isinstance(data, list):
        raise ValueError("Unexpected GitHub response")
    issues = [item for item in data if isinstance(item, dict) and "pull_request" not in item
              and (str(item.get("title", "")).startswith("[HIREFLOWW_QUEUE]")
                   or any(label.get("name") == LABEL
                          for label in item.get("labels", []) if isinstance(label, dict)))]
    if len(issues) >= 100:
        raise ValueError("Too many open queue issues")
    rows = []
    seen = set()
    for issue in issues:
        for candidate in parse_issue(issue.get("body") or ""):
            if candidate["linkedin_url"] in seen:
                continue
            seen.add(candidate["linkedin_url"])
            rows.append(candidate)
            if len(rows) > MAX_RECORDS:
                raise ValueError("Queue exceeds 50 unique candidates")
    return rows


def main() -> int:
    try:
        rows = fetch_queue_issues(os.environ.get("GITHUB_REPOSITORY", ""),
                                  os.environ.get("GITHUB_TOKEN", ""))
        output = os.environ.get("GITHUB_OUTPUT")
        if not output:
            raise ValueError("GitHub output path missing")
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"has_queue={'true' if rows else 'false'}\n")
            stream.write(f"candidate_count={len(rows)}\n")
        if not rows:
            print("No candidate-queue issue found. No external provider requests.")
            return 0
        private = Path("private")
        private.mkdir(mode=0o700, exist_ok=True)
        file = private / "github-candidates.csv"
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=["full_name", "linkedin_url", "company_name"])
            writer.writeheader()
            writer.writerows(rows)
        print(f"Prepared {len(rows)} profiles privately. Provider API calls: 0.")
        return 0
    except (ValueError, OSError, json.JSONDecodeError):
        print("Candidate queue access or validation failed; no provider requests.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
