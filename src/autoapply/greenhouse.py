"""Submit an application through the public Greenhouse job form endpoint."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path

import httpx

from autoapply.answers import build_application
from autoapply.cover import cover_letter
from autoapply.sources import BROWSER_UA

log = logging.getLogger(__name__)

REMIX_RE = re.compile(r"window\.__remixContext = (\{.*?\});</script>", re.S)


class ApplyError(Exception):
    def __init__(self, message: str, body: str = ""):
        super().__init__(message)
        self.body = body


def upload_resume(client: httpx.Client, resume_path: Path) -> tuple[str, str]:
    response = client.get(
        "https://boards.greenhouse.io/uncacheable_attributes/presigned_fields",
        params={"fields[]": "resume"},
    )
    response.raise_for_status()
    payload = response.json()
    slot = payload["resume"]
    key = (
        slot["key"]
        .replace("{timestamp}", str(int(time.time() * 1000)))
        .replace("{unique_id}", "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=12)))
    )
    fields = dict(slot["fields"])
    fields.update(
        {
            "utf8": "✓",
            "key": key,
            "authenticity_token": "1234",
            "Content-Type": "application/octet-stream",
        }
    )
    filename = resume_path.name
    file_bytes = resume_path.read_bytes()
    upload = client.post(
        payload["url"],
        data=fields,
        files={"file": (filename, file_bytes, "application/octet-stream")},
    )
    if upload.status_code not in (200, 201, 204):
        raise ApplyError(f"Resume upload failed ({upload.status_code})", upload.text[:500])
    resume_url = f"{payload['url']}/{key}"
    return resume_url, filename


def _load_form(client: httpx.Client, token: str, job_id: str) -> tuple[str | None, str | None, str | None]:
    """Return fingerprint, submit path, and a redirect URL when the form is not on Greenhouse."""
    url = f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}"
    page = client.get(url, headers={"Accept": "text/html"}, follow_redirects=False)
    if page.status_code in {301, 302, 303, 307, 308}:
        return None, None, page.headers.get("location") or url
    if page.status_code >= 400 or "__remixContext" not in page.text:
        return None, None, url
    match = REMIX_RE.search(page.text)
    if not match:
        return None, None, url
    context = json.loads(match.group(1))
    route = (
        context.get("state", {})
        .get("loaderData", {})
        .get("routes/$url_token_.jobs_.$job_post_id", {})
    )
    job_post = route.get("jobPost") or {}
    return job_post.get("fingerprint"), route.get("submitPath"), None


def _load_questions(client: httpx.Client, token: str, job_id: str) -> dict:
    response = client.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}",
        params={"questions": "true"},
    )
    response.raise_for_status()
    return response.json()


def submit_job(
    client: httpx.Client,
    *,
    token: str,
    job_id: str,
    profile: dict,
    resume_url: str,
    resume_filename: str,
    cover_text: str | None = None,
) -> dict:
    """Return a result dict with status submitted, needs_review, skipped, or failed."""
    detail = _load_questions(client, token, job_id)
    company = detail.get("company_name") or token
    title = detail.get("title") or ""
    absolute_url = detail.get("absolute_url") or f"https://job-boards.greenhouse.io/{token}/jobs/{job_id}"
    location = ((detail.get("location") or {}).get("name")) or ""
    letter = cover_text or cover_letter(profile, company, title)
    built = build_application(
        detail.get("questions") or [],
        profile,
        company,
        letter,
        resume_url,
        resume_filename,
        location=location,
    )
    if built.disqualify_reason:
        return {
            "status": "skipped",
            "company": company,
            "title": title,
            "error": built.disqualify_reason,
            "cover_letter": letter,
            "answers": built.answered,
        }
    if built.missing_required:
        return {
            "status": "needs_review",
            "company": company,
            "title": title,
            "error": "Unanswered required questions: " + "; ".join(built.missing_required),
            "cover_letter": letter,
            "answers": built.answered,
            "questions": built.missing_details,
        }

    compliance = detail.get("data_compliance") or []
    if isinstance(compliance, list):
        for item in compliance:
            if not isinstance(item, dict):
                continue
            if item.get("requires_processing_consent"):
                built.application["data_compliance"]["gdpr_processing_consent_given"] = True
            elif item.get("requires_consent"):
                built.application["data_compliance"]["gdpr_consent_given"] = True

    fingerprint, submit_path, redirected = _load_form(client, token, job_id)
    if redirected or not fingerprint or not submit_path:
        from autoapply.browser_apply import apply_with_browser

        company_url = redirected or absolute_url
        browser_result = apply_with_browser(
            url=company_url,
            profile=profile,
            resume_path=Path(profile["resume_path"]),
            fills=built.fills,
            title=title,
            cover_text=letter,
            company=company,
            location=location,
        )
        browser_result.setdefault("company", company)
        browser_result.setdefault("title", title)
        browser_result.setdefault("cover_letter", letter)
        browser_result.setdefault("answers", built.answered)
        browser_result.setdefault("url", company_url)
        return browser_result
    body = {"job_application": built.application, "fingerprint": fingerprint}
    log.info("submitting %s fingerprint=%s", submit_path, bool(fingerprint))
    response = client.post(
        submit_path,
        json=body,
        headers={
            "Accept": "application/json, text/html",
            "Origin": "https://job-boards.greenhouse.io",
            "Referer": absolute_url,
            "User-Agent": BROWSER_UA,
        },
    )
    excerpt = response.text[:800]
    captcha = (
        response.status_code == 428
        or "captcha" in excerpt.lower()
        or excerpt.strip() == "Bad Request"
    )
    if captcha:
        return {
            "status": "needs_review",
            "company": company,
            "title": title,
            "error": "Greenhouse asked for a captcha, so this one was left for you to submit.",
            "response": excerpt,
            "cover_letter": letter,
            "answers": built.answered,
            "url": absolute_url,
        }
    if response.status_code >= 400:
        return {
            "status": "failed",
            "company": company,
            "title": title,
            "error": f"Greenhouse responded {response.status_code}",
            "response": excerpt,
            "cover_letter": letter,
            "answers": built.answered,
            "url": absolute_url,
        }
    return {
        "status": "submitted",
        "company": company,
        "title": title,
        "response": excerpt,
        "cover_letter": letter,
        "answers": built.answered,
        "url": absolute_url,
    }
