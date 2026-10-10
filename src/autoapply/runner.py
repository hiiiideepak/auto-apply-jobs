"""Discover matching jobs and submit up to the daily cap."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

import httpx

from autoapply.agent import agent_will_run, run_apply_agent
from autoapply.db import connect, now_iso, today_prefix
from autoapply.greenhouse import submit_job, upload_resume
from autoapply.matching import company_is_skipped
from autoapply.profile import board_token_from_external_id, load_board_tokens, load_profile
from autoapply.questions import record_open_questions
from autoapply.sources import BROWSER_UA, discover_all

log = logging.getLogger(__name__)
RUN_LOCK = threading.Lock()


def _apply_enabled() -> bool:
    return os.environ.get("APPLY_ENABLED", "1") not in {"0", "false", "False"}


def _counts(conn) -> dict:
    today = today_prefix()
    submitted_today = conn.execute(
        """
        SELECT COUNT(*) AS n FROM applications
        WHERE status = 'submitted' AND applied_at LIKE ?
        """,
        (today + "%",),
    ).fetchone()["n"]
    by_company = {
        row["company"]: row["n"]
        for row in conn.execute(
            """
            SELECT j.company AS company, COUNT(*) AS n
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            WHERE a.status = 'submitted' AND a.applied_at LIKE ?
            GROUP BY j.company
            """,
            (today + "%",),
        )
    }
    return {"submitted_today": submitted_today, "by_company": by_company}


def _upsert(conn, opening, profile: dict) -> None:
    if company_is_skipped(opening.company, profile.get("skip_companies") or []):
        status = "skipped"
        reason = "Current employer"
    elif opening.auto_apply:
        status = "new"
        reason = None
    else:
        status = "manual"
        reason = "No public submit endpoint for this board"
    conn.execute(
        """
        INSERT INTO jobs (
            dedupe_key, source, external_id, title, company, location, url,
            score, status, skip_reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dedupe_key) DO UPDATE SET
            title = excluded.title,
            location = excluded.location,
            url = excluded.url,
            score = excluded.score,
            updated_at = excluded.updated_at
        """,
        (
            opening.dedupe_key,
            opening.source,
            opening.external_id,
            opening.title,
            opening.company,
            opening.location,
            opening.url,
            opening.score,
            status,
            reason,
            now_iso(),
            now_iso(),
        ),
    )


def _apply_without_agent(
    *,
    conn,
    client,
    profile: dict,
    rows,
    resume_url: str,
    resume_filename: str,
    budget: int,
    per_company: int,
    company_counts: dict,
    notes: list[str],
) -> dict:
    """Used only when no LLM API key is configured."""
    notes.append("No ANTHROPIC_API_KEY; applied with the fixed questionnaire")
    submitted = 0
    needs_review = 0
    skipped = 0
    failed = 0
    examined = 0
    consecutive_failures = 0
    for row in rows:
        if submitted >= budget or examined >= budget * 3:
            break
        token, job_id = row["external_id"].split(":", 1)
        company_key = token.lower()
        if company_counts.get(company_key, 0) >= per_company:
            continue
        examined += 1
        try:
            result = submit_job(
                client,
                token=token,
                job_id=job_id,
                profile=profile,
                resume_url=resume_url,
                resume_filename=resume_filename,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("submit failed for %s", row["dedupe_key"])
            result = {
                "status": "failed",
                "error": str(exc),
                "company": row["company"],
                "title": row["title"],
                "answers": [],
                "cover_letter": "",
                "response": "",
            }
        status = result["status"]
        if status == "submitted":
            submitted += 1
            consecutive_failures = 0
            company_counts[company_key] = company_counts.get(company_key, 0) + 1
        elif status == "manual":
            consecutive_failures = 0
            examined -= 1
        elif status == "needs_review":
            needs_review += 1
            consecutive_failures = 0
        elif status == "skipped":
            skipped += 1
            consecutive_failures = 0
        else:
            failed += 1
            consecutive_failures += 1
        conn.execute(
            "UPDATE jobs SET status = ?, skip_reason = ?, company = ?, updated_at = ? WHERE id = ?",
            (
                status,
                result.get("error"),
                result.get("company") or row["company"],
                now_iso(),
                row["id"],
            ),
        )
        conn.execute(
            """
            INSERT INTO applications (
                job_id, method, status, answers_json, cover_letter,
                response_excerpt, error, applied_at
            ) VALUES (?, 'greenhouse', ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                status,
                json.dumps(result.get("answers") or []),
                result.get("cover_letter") or "",
                (result.get("response") or "")[:800],
                result.get("error"),
                now_iso(),
            ),
        )
        if result.get("questions"):
            record_open_questions(
                conn,
                questions=result.get("questions") or [],
                company=result.get("company") or row["company"],
                title=result.get("title") or row["title"],
                profile=profile,
            )
        conn.commit()
        if consecutive_failures >= 3:
            notes.append("Stopped after 3 consecutive Greenhouse errors")
            break
        time.sleep(2)
    return {
        "submitted": submitted,
        "needs_review": needs_review,
        "skipped": skipped,
        "failed": failed,
    }


def run_cycle(trigger: str = "manual", limit: int | None = None, dry_run: bool = False) -> dict:
    if not RUN_LOCK.acquire(blocking=False):
        return {"ok": False, "error": "A run is already in progress"}
    try:
        return _run_cycle(trigger, limit, dry_run)
    finally:
        RUN_LOCK.release()


def _run_cycle(trigger: str, limit: int | None, dry_run: bool) -> dict:
    profile = load_profile()
    tokens = load_board_tokens()
    per_run = int(limit if limit is not None else profile.get("per_run_limit") or 20)
    daily_target = int(profile.get("daily_target") or 80)
    per_company = int(profile.get("max_per_company_per_day") or 5)
    conn = connect()
    started = now_iso()
    run_id = conn.execute(
        "INSERT INTO runs (trigger, started_at) VALUES (?, ?)",
        (trigger, started),
    ).lastrowid
    conn.commit()

    discovered = 0
    eligible = 0
    submitted = 0
    needs_review = 0
    skipped = 0
    failed = 0
    notes: list[str] = []

    try:
        from autoapply.browser_apply import browser_available
        from autoapply.inbox import inbox_configured

        if inbox_configured():
            conn.execute(
                """
                UPDATE jobs
                SET status = 'new', skip_reason = NULL, updated_at = ?
                WHERE status = 'needs_review'
                  AND skip_reason LIKE 'The company emailed a verification code%'
                """,
                (now_iso(),),
            )
            conn.commit()
        if browser_available():
            conn.execute(
                """
                UPDATE jobs
                SET status = 'new', skip_reason = NULL, updated_at = ?
                WHERE status = 'manual'
                  AND source = 'greenhouse'
                  AND skip_reason = 'Apply on the company site'
                """,
                (now_iso(),),
            )
            conn.commit()
        openings = discover_all(tokens)
        discovered = len(openings)
        for opening in openings:
            _upsert(conn, opening, profile)
        conn.commit()
        eligible = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'new'"
        ).fetchone()["n"]

        counts = _counts(conn)
        remaining = max(0, daily_target - counts["submitted_today"])
        budget = min(per_run, remaining)
        if not _apply_enabled():
            notes.append("APPLY_ENABLED is off; discovery only")
            budget = 0
        if dry_run:
            notes.append("Dry run; nothing was submitted")
            budget = 0

        resume_path = Path(profile["resume_path"])
        if budget and not resume_path.is_file():
            notes.append(f"Resume not found at {resume_path}")
            budget = 0

        if budget:
            allowed = set(tokens)
            rows = [
                row
                for row in conn.execute(
                    """
                    SELECT * FROM jobs
                    WHERE status = 'new' AND source = 'greenhouse'
                    ORDER BY score DESC, id ASC
                    """
                ).fetchall()
                if board_token_from_external_id(row["external_id"]) in allowed
            ]
            with httpx.Client(
                timeout=45,
                headers={"User-Agent": BROWSER_UA},
                follow_redirects=True,
            ) as client:
                resume_url, resume_filename = upload_resume(client, resume_path)
                if agent_will_run():
                    summary = run_apply_agent(
                        conn=conn,
                        profile=profile,
                        client=client,
                        resume_url=resume_url,
                        resume_filename=resume_filename,
                        budget=budget,
                        per_company=per_company,
                        company_counts={
                            (name or "").lower(): count
                            for name, count in counts["by_company"].items()
                        },
                        run_id=run_id,
                    )
                    submitted = summary["submitted"]
                    needs_review = summary["needs_review"]
                    skipped = summary["skipped"]
                    failed = summary["failed"]
                    notes.append(f"Agent used {summary['model']} with resume retrieval and tool calls")
                    if failed >= 3:
                        notes.append("Stopped after 3 consecutive Greenhouse errors")
                else:
                    summary = _apply_without_agent(
                        conn=conn,
                        client=client,
                        profile=profile,
                        rows=rows,
                        resume_url=resume_url,
                        resume_filename=resume_filename,
                        budget=budget,
                        per_company=per_company,
                        company_counts={
                            (name or "").lower(): count
                            for name, count in counts["by_company"].items()
                        },
                        notes=notes,
                    )
                    submitted = summary["submitted"]
                    needs_review = summary["needs_review"]
                    skipped = summary["skipped"]
                    failed = summary["failed"]
    except Exception as exc:  # noqa: BLE001
        notes.append(str(exc))
        log.exception("run failed")
    finally:
        conn.execute(
            """
            UPDATE runs SET
                finished_at = ?, discovered = ?, eligible = ?, submitted = ?,
                needs_review = ?, skipped = ?, failed = ?, notes = ?
            WHERE id = ?
            """,
            (
                now_iso(),
                discovered,
                eligible,
                submitted,
                needs_review,
                skipped,
                failed,
                "\n".join(notes),
                run_id,
            ),
        )
        conn.commit()
        conn.close()

    return {
        "ok": True,
        "run_id": run_id,
        "discovered": discovered,
        "eligible": eligible,
        "submitted": submitted,
        "needs_review": needs_review,
        "skipped": skipped,
        "failed": failed,
        "notes": notes,
    }
