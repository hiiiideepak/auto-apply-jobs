"""Questions the agent could not answer, and the replies saved for later."""

from __future__ import annotations

import json
import re

from autoapply.db import now_iso
from autoapply.profile import load_profile, save_profile

UNANSWERED_PREFIX = "Unanswered required questions: "


def question_key(label: str) -> str:
    text = re.sub(r"\s+", " ", (label or "").strip().lower())
    return text.rstrip(" ?.!")


def labels_from_error(error: str | None) -> list[str]:
    if not error or not str(error).startswith(UNANSWERED_PREFIX):
        return []
    body = str(error)[len(UNANSWERED_PREFIX) :]
    return [part.strip() for part in body.split("; ") if part.strip()]


def _answered_keys(profile: dict) -> set[str]:
    keys = set()
    for item in profile.get("saved_answers") or []:
        if not str(item.get("answer") or "").strip():
            continue
        key = question_key(str(item.get("question") or ""))
        if key:
            keys.add(key)
    return keys


def _load_json(raw: str | None) -> list:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return value if isinstance(value, list) else []


def record_open_questions(conn, *, questions: list[dict], company: str, title: str, profile: dict) -> None:
    known = _answered_keys(profile or {})
    now = now_iso()
    asked_item = {"company": company or "", "title": title or ""}
    for item in questions or []:
        label = str(item.get("label") or "").strip()
        key = question_key(label)
        if not key or key in known:
            continue
        field_type = str(item.get("field_type") or "textarea")
        options = [str(option) for option in (item.get("options") or []) if str(option).strip()]
        existing = conn.execute(
            "SELECT field_type, options_json, asked_json FROM open_questions WHERE question_key = ?",
            (key,),
        ).fetchone()
        if existing:
            asked = _load_json(existing["asked_json"])
            if asked_item not in asked and len(asked) < 6:
                asked.append(asked_item)
            stored_options = _load_json(existing["options_json"])
            new_type = existing["field_type"]
            new_options = stored_options
            if options and not stored_options:
                new_type = field_type
                new_options = options
            conn.execute(
                """
                UPDATE open_questions
                SET asked_json = ?, field_type = ?, options_json = ?, updated_at = ?
                WHERE question_key = ?
                """,
                (json.dumps(asked), new_type, json.dumps(new_options), now, key),
            )
            continue
        conn.execute(
            """
            INSERT INTO open_questions (
                question_key, label, field_type, options_json, asked_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key,
                label,
                field_type,
                json.dumps(options),
                json.dumps([asked_item]),
                now,
                now,
            ),
        )


def sync_open_questions(conn, profile: dict | None = None) -> None:
    """Pull questions already stored on needs-review jobs into the prompt list."""
    profile = load_profile() if profile is None else profile
    known = _answered_keys(profile)
    rows = conn.execute(
        """
        SELECT company, title, skip_reason FROM jobs
        WHERE status = 'needs_review'
          AND skip_reason LIKE 'Unanswered required questions:%'
        """
    ).fetchall()
    grouped: dict[str, dict] = {}
    for row in rows:
        for label in labels_from_error(row["skip_reason"]):
            key = question_key(label)
            if not key or key in known:
                continue
            bucket = grouped.setdefault(key, {"label": label, "asked": []})
            asked = {"company": row["company"] or "", "title": row["title"] or ""}
            if asked not in bucket["asked"] and len(bucket["asked"]) < 6:
                bucket["asked"].append(asked)
    now = now_iso()
    for key, bucket in grouped.items():
        existing = conn.execute(
            "SELECT asked_json FROM open_questions WHERE question_key = ?",
            (key,),
        ).fetchone()
        if existing:
            asked = _load_json(existing["asked_json"])
            for item in bucket["asked"]:
                if item not in asked and len(asked) < 6:
                    asked.append(item)
            conn.execute(
                "UPDATE open_questions SET asked_json = ?, updated_at = ? WHERE question_key = ?",
                (json.dumps(asked), now, key),
            )
            continue
        conn.execute(
            """
            INSERT INTO open_questions (
                question_key, label, field_type, options_json, asked_json, created_at, updated_at
            ) VALUES (?, ?, 'textarea', '[]', ?, ?, ?)
            """,
            (key, bucket["label"], json.dumps(bucket["asked"]), now, now),
        )
    for key in known:
        conn.execute("DELETE FROM open_questions WHERE question_key = ?", (key,))
    conn.commit()


def list_open_questions(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT question_key, label, field_type, options_json, asked_json FROM open_questions ORDER BY label"
    ).fetchall()
    return [
        {
            "key": row["question_key"],
            "label": row["label"],
            "field_type": row["field_type"],
            "options": _load_json(row["options_json"]),
            "asked": _load_json(row["asked_json"]),
        }
        for row in rows
    ]


def choice_questions(detail: dict) -> list[dict]:
    """Choice lists from a Greenhouse job payload."""
    found = []
    for question in detail.get("questions") or []:
        fields = question.get("fields") or []
        if not fields:
            continue
        spec = fields[0]
        qtype = spec.get("type") or ""
        if qtype not in {"multi_value_single_select", "multi_value_multi_select"}:
            continue
        options: list[str] = []
        for value in spec.get("values") or []:
            text = str(value.get("label") or "").strip()
            if text and text not in options:
                options.append(text)
            if len(options) >= 40:
                break
        if not options:
            continue
        found.append(
            {
                "label": str(question.get("label") or "").strip(),
                "field_type": "multi" if qtype == "multi_value_multi_select" else "select",
                "options": options,
            }
        )
    return found


def attach_choice_options(conn, choices: list[dict]) -> int:
    updated = 0
    now = now_iso()
    for item in choices:
        key = question_key(str(item.get("label") or ""))
        options = [str(option) for option in (item.get("options") or []) if str(option).strip()]
        if not key or not options:
            continue
        row = conn.execute(
            "SELECT options_json FROM open_questions WHERE question_key = ?",
            (key,),
        ).fetchone()
        if row is None or _load_json(row["options_json"]):
            continue
        conn.execute(
            """
            UPDATE open_questions
            SET field_type = ?, options_json = ?, updated_at = ?
            WHERE question_key = ?
            """,
            (item.get("field_type") or "select", json.dumps(options[:40]), now, key),
        )
        updated += 1
    return updated


def enrich_question_options(conn) -> int:
    """Fill choice lists for questions already pulled from stuck applications."""
    pending = [
        row["external_id"]
        for row in conn.execute(
            """
            SELECT external_id FROM jobs
            WHERE status = 'needs_review'
              AND source = 'greenhouse'
              AND instr(external_id, ':') > 0
              AND skip_reason LIKE 'Unanswered required questions:%'
              AND external_id NOT IN (SELECT external_id FROM option_fetches)
            """
        )
    ]
    if not pending:
        return 0
    from concurrent.futures import ThreadPoolExecutor

    import httpx

    from autoapply.sources import BROWSER_UA

    def fetch(external_id: str):
        token, job_id = external_id.split(":", 1)
        try:
            with httpx.Client(timeout=20, headers={"User-Agent": BROWSER_UA}) as client:
                response = client.get(
                    f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{job_id}",
                    params={"questions": "true"},
                )
            if response.status_code >= 400:
                return external_id, []
            return external_id, choice_questions(response.json())
        except Exception:  # noqa: BLE001 — one board must not hide the questions already stored
            return external_id, None

    updated = 0
    now = now_iso()
    with ThreadPoolExecutor(max_workers=8) as pool:
        for external_id, choices in pool.map(fetch, pending):
            if choices is None:
                continue
            updated += attach_choice_options(conn, choices)
            conn.execute(
                "INSERT OR REPLACE INTO option_fetches (external_id, fetched_at) VALUES (?, ?)",
                (external_id, now),
            )
    conn.commit()
    return updated


def save_custom_answer(question: str, answer: str) -> dict:
    question = (question or "").strip()
    answer = (answer or "").strip()
    if not question or not answer:
        raise ValueError("Question and answer are required")
    profile = load_profile()
    key = question_key(question)
    kept = []
    for item in profile.get("saved_answers") or []:
        if question_key(str(item.get("question") or "")) == key:
            continue
        text = str(item.get("question") or "").strip()
        reply = str(item.get("answer") or "").strip()
        if text and reply:
            kept.append({"question": text, "answer": reply})
    kept.append({"question": question, "answer": answer})
    profile["saved_answers"] = kept
    return save_profile(profile)


def release_answered_jobs(conn, profile: dict) -> int:
    keys = _answered_keys(profile)
    rows = conn.execute(
        "SELECT id, skip_reason FROM jobs WHERE status = 'needs_review'"
    ).fetchall()
    released = 0
    now = now_iso()
    for row in rows:
        labels = labels_from_error(row["skip_reason"])
        if not labels:
            continue
        if all(question_key(label) in keys for label in labels):
            conn.execute(
                "UPDATE jobs SET status = 'new', skip_reason = NULL, updated_at = ? WHERE id = ?",
                (now, row["id"]),
            )
            released += 1
    conn.commit()
    return released
