"""Public job feeds. Failures in one source do not stop the others."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import httpx

from autoapply.matching import is_eligible_location, is_target_role, score_job

log = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class Opening:
    dedupe_key: str
    source: str
    external_id: str
    title: str
    company: str
    location: str
    url: str
    score: int
    auto_apply: bool


def _client() -> httpx.Client:
    return httpx.Client(
        timeout=40,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
        follow_redirects=True,
    )


def _keep(title: str, location: str) -> bool:
    return is_target_role(title) and is_eligible_location(location)


def fetch_greenhouse(tokens: list[str]) -> list[Opening]:
    openings: list[Opening] = []
    with _client() as client:
        for token in tokens:
            url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            try:
                response = client.get(url)
                response.raise_for_status()
                jobs = response.json().get("jobs") or []
            except Exception as exc:  # noqa: BLE001 — one board must not abort the run
                log.warning("greenhouse %s failed: %s", token, exc)
                continue
            for job in jobs:
                title = job.get("title") or ""
                location = ((job.get("location") or {}).get("name")) or ""
                if not _keep(title, location):
                    continue
                job_id = str(job.get("id"))
                openings.append(
                    Opening(
                        dedupe_key=f"greenhouse:{token}:{job_id}",
                        source="greenhouse",
                        external_id=f"{token}:{job_id}",
                        title=title,
                        company=(job.get("company_name") or token),
                        location=location,
                        url=job.get("absolute_url") or "",
                        score=score_job(title, location),
                        auto_apply=True,
                    )
                )
    return openings


def fetch_remotive() -> list[Opening]:
    openings: list[Opening] = []
    with _client() as client:
        response = client.get(
            "https://remotive.com/api/remote-jobs",
            params={"category": "software-dev"},
        )
        response.raise_for_status()
        for job in response.json().get("jobs") or []:
            title = job.get("title") or ""
            location = job.get("candidate_required_location") or ""
            if not _keep(title, location):
                continue
            openings.append(
                Opening(
                    dedupe_key=f"remotive:{job.get('id')}",
                    source="remotive",
                    external_id=str(job.get("id")),
                    title=title,
                    company=job.get("company_name") or "",
                    location=location,
                    url=job.get("url") or "",
                    score=score_job(title, location),
                    auto_apply=False,
                )
            )
    return openings


def fetch_jobicy() -> list[Opening]:
    openings: list[Opening] = []
    with _client() as client:
        for tag in ("backend", "software engineer"):
            response = client.get(
                "https://jobicy.com/api/v2/remote-jobs",
                params={"count": 50, "tag": tag},
            )
            response.raise_for_status()
            for job in response.json().get("jobs") or []:
                title = job.get("jobTitle") or ""
                location = job.get("jobGeo") or ""
                if location.lower() in {"anywhere", "worldwide", "apac", "asia"}:
                    location = "Remote"
                if not _keep(title, location):
                    continue
                openings.append(
                    Opening(
                        dedupe_key=f"jobicy:{job.get('id')}",
                        source="jobicy",
                        external_id=str(job.get("id")),
                        title=title,
                        company=job.get("companyName") or "",
                        location=location,
                        url=job.get("url") or "",
                        score=score_job(title, location),
                        auto_apply=False,
                    )
                )
    return openings


def fetch_remoteok() -> list[Opening]:
    openings: list[Opening] = []
    with _client() as client:
        response = client.get("https://remoteok.com/api")
        response.raise_for_status()
        payload = response.json()
    for job in payload:
        if not isinstance(job, dict) or not job.get("position"):
            continue
        title = job.get("position") or ""
        location = job.get("location") or "Remote"
        if str(location).lower() in {"", "remote", "worldwide"}:
            location = "Remote"
        if not _keep(title, location):
            continue
        openings.append(
            Opening(
                dedupe_key=f"remoteok:{job.get('id')}",
                source="remoteok",
                external_id=str(job.get("id")),
                title=title,
                company=(job.get("company") or "").strip(),
                location=location,
                url=job.get("url") or job.get("apply_url") or "",
                score=score_job(title, location),
                auto_apply=False,
            )
        )
    return openings


def fetch_himalayas() -> list[Opening]:
    openings: list[Opening] = []
    queries = [
        "backend engineer",
        "senior software engineer",
        "staff engineer",
        "full stack engineer",
    ]
    with _client() as client:
        for query in queries:
            response = client.get(
                "https://himalayas.app/jobs/api/search",
                params={"q": query, "worldwide": "true", "page": 1},
            )
            response.raise_for_status()
            for job in response.json().get("jobs") or []:
                title = job.get("title") or ""
                restrictions = job.get("locationRestrictions") or []
                if restrictions:
                    location = ", ".join(str(item) for item in restrictions)
                else:
                    location = "Remote"
                if not _keep(title, location):
                    continue
                url = job.get("applicationLink") or job.get("guid") or ""
                openings.append(
                    Opening(
                        dedupe_key=f"himalayas:{url}",
                        source="himalayas",
                        external_id=url,
                        title=title,
                        company=job.get("companyName") or "",
                        location=location,
                        url=url,
                        score=score_job(title, location),
                        auto_apply=False,
                    )
                )
    return openings


def fetch_weworkremotely() -> list[Opening]:
    openings: list[Opening] = []
    rss_url = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    with _client() as client:
        response = client.get(rss_url, headers={"Accept": "application/rss+xml"})
        response.raise_for_status()
        root = ET.fromstring(response.text)
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        region = (item.findtext("region") or "").strip()
        location = region or "Remote"
        if location.lower() in {"anywhere", "worldwide", ""}:
            location = "Remote"
        company = (item.findtext("company") or "").strip() or _company_from_title(title)
        role = _role_from_title(title)
        if not _keep(role, location):
            continue
        url = (item.findtext("link") or "").strip()
        openings.append(
            Opening(
                dedupe_key=f"weworkremotely:{url}",
                source="weworkremotely",
                external_id=url,
                title=role,
                company=company,
                location=location,
                url=url,
                score=score_job(role, location),
                auto_apply=False,
            )
        )
    return openings


def _company_from_title(title: str) -> str:
    if ":" in title:
        return title.split(":", 1)[0].strip()
    return ""


def _role_from_title(title: str) -> str:
    if ":" in title:
        return title.split(":", 1)[1].strip()
    return title


def discover_all(tokens: list[str]) -> list[Opening]:
    """Collect openings. Greenhouse can be submitted; other feeds are saved to review."""
    found: list[Opening] = []
    fetchers = [
        ("greenhouse", lambda: fetch_greenhouse(tokens)),
        ("remotive", fetch_remotive),
        ("jobicy", fetch_jobicy),
        ("remoteok", fetch_remoteok),
        ("himalayas", fetch_himalayas),
        ("weworkremotely", fetch_weworkremotely),
    ]
    for name, fetcher in fetchers:
        try:
            batch = fetcher()
            log.info("%s returned %s matching openings", name, len(batch))
            found.extend(batch)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s feed failed: %s", name, exc)
    return found


EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
