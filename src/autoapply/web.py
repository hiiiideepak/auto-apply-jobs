"""Dashboard and the on-demand run endpoint."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from autoapply.db import connect, now_iso, today_prefix
from autoapply.profile import (
    load_board_tokens,
    load_profile,
    normalize_board_token,
    save_board_tokens,
    save_profile,
)
from autoapply.questions import (
    enrich_question_options,
    list_open_questions,
    release_answered_jobs,
    save_custom_answer,
    sync_open_questions,
)
from autoapply.runner import run_cycle

PAGE = (Path(__file__).resolve().parent / "dashboard.html").read_text()
REMOVED_REASON = "Removed from company list"

app = FastAPI(title="Auto Apply", version="0.2.0")


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    conn = connect()
    try:
        return [dict(row) for row in conn.execute(sql, params)]
    finally:
        conn.close()


def _company_payload(added: str | None = None) -> dict:
    tokens = load_board_tokens()
    counts = {
        row["token"]: row
        for row in _rows(
            """
            SELECT substr(external_id, 1, instr(external_id, ':') - 1) AS token,
                   MAX(company) AS company,
                   COUNT(*) AS open_roles
            FROM jobs
            WHERE source = 'greenhouse'
              AND status = 'new'
              AND instr(external_id, ':') > 0
            GROUP BY token
            """
        )
    }
    companies = []
    for token in tokens:
        row = counts.get(token) or {}
        companies.append(
            {
                "token": token,
                "company": row.get("company") or token,
                "open_roles": row.get("open_roles") or 0,
            }
        )
    companies.sort(key=lambda item: (-item["open_roles"], item["token"]))
    payload = {"companies": companies}
    if added:
        payload["added"] = added
    return payload


def _mark_board(token: str, removed: bool) -> None:
    conn = connect()
    try:
        if removed:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'skipped', skip_reason = ?, updated_at = ?
                WHERE source = 'greenhouse' AND status = 'new' AND external_id LIKE ?
                """,
                (REMOVED_REASON, now_iso(), token + ":%"),
            )
        else:
            conn.execute(
                """
                UPDATE jobs
                SET status = 'new', skip_reason = NULL, updated_at = ?
                WHERE source = 'greenhouse'
                  AND status = 'skipped'
                  AND skip_reason = ?
                  AND external_id LIKE ?
                """,
                (now_iso(), REMOVED_REASON, token + ":%"),
            )
        conn.commit()
    finally:
        conn.close()


@app.get("/api/health")
def health() -> dict:
    from autoapply.llm import DEFAULT_MODEL, llm_configured

    return {"ok": True, "agent": llm_configured(), "model": DEFAULT_MODEL}


@app.get("/api/agent-events")
def agent_events(limit: int = Query(40, le=100)) -> list[dict]:
    return _rows(
        """
        SELECT id, run_id, kind, name, payload, created_at
        FROM agent_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )


@app.get("/api/stats")
def stats() -> dict:
    profile = load_profile()
    today = today_prefix()
    submitted = _rows(
        "SELECT COUNT(*) AS n FROM applications WHERE status = 'submitted' AND applied_at LIKE ?",
        (today + "%",),
    )[0]["n"]
    counts = {
        row["status"]: row["n"]
        for row in _rows("SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    }
    last = _rows("SELECT * FROM runs ORDER BY id DESC LIMIT 1")
    return {
        "name": profile["name"],
        "submitted_today": submitted,
        "daily_target": profile.get("daily_target"),
        "jobs": counts,
        "last_run": last[0] if last else None,
    }


@app.get("/api/jobs")
def jobs(status: str | None = None, limit: int = Query(50, le=200)) -> list[dict]:
    if status:
        return _rows(
            """
            SELECT id, source, title, company, location, url, score, status, skip_reason, updated_at
            FROM jobs WHERE status = ? ORDER BY score DESC, id DESC LIMIT ?
            """,
            (status, limit),
        )
    return _rows(
        """
        SELECT id, source, title, company, location, url, score, status, skip_reason, updated_at
        FROM jobs ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    )


@app.get("/api/applications")
def applications(
    status: str | None = None,
    limit: int = Query(100, le=500),
) -> list[dict]:
    if status:
        return _rows(
            """
            SELECT a.id, a.status, a.method, a.error, a.applied_at, a.answers_json,
                   j.title, j.company, j.location, j.url, j.source
            FROM applications a
            JOIN jobs j ON j.id = a.job_id
            WHERE a.status = ?
            ORDER BY a.id DESC
            LIMIT ?
            """,
            (status, limit),
        )
    return _rows(
        """
        SELECT a.id, a.status, a.method, a.error, a.applied_at, a.answers_json,
               j.title, j.company, j.location, j.url, j.source
        FROM applications a
        JOIN jobs j ON j.id = a.job_id
        ORDER BY a.id DESC
        LIMIT ?
        """,
        (limit,),
    )


@app.get("/api/companies")
def list_companies() -> dict:
    return _company_payload()


@app.post("/api/companies")
def add_company(payload: dict = Body(...)) -> dict:
    try:
        token = normalize_board_token(str(payload.get("token") or ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    tokens = load_board_tokens()
    if token not in tokens:
        tokens.append(token)
        save_board_tokens(tokens)
    _mark_board(token, removed=False)
    body = _company_payload(added=token)
    return body


@app.delete("/api/companies/{token}")
def remove_company(token: str) -> dict:
    try:
        token = normalize_board_token(token)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    tokens = [item for item in load_board_tokens() if item != token]
    save_board_tokens(tokens)
    _mark_board(token, removed=True)
    return _company_payload()


@app.get("/api/profile")
def profile() -> dict:
    return load_profile()


@app.put("/api/profile")
def update_profile(payload: dict = Body(...)) -> dict:
    try:
        current = load_profile()
        payload = dict(payload)
        payload["saved_answers"] = current.get("saved_answers") or []
        return save_profile(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/questions")
def questions() -> dict:
    conn = connect()
    try:
        profile = load_profile()
        sync_open_questions(conn, profile)
        enrich_question_options(conn)
        return {
            "open": list_open_questions(conn),
            "saved": profile.get("saved_answers") or [],
        }
    finally:
        conn.close()


@app.post("/api/questions")
def answer_question(payload: dict = Body(...)) -> dict:
    try:
        profile = save_custom_answer(
            str(payload.get("question") or ""),
            str(payload.get("answer") or ""),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    conn = connect()
    try:
        sync_open_questions(conn, profile)
        released = release_answered_jobs(conn, profile)
        return {
            "open": list_open_questions(conn),
            "saved": profile.get("saved_answers") or [],
            "released_jobs": released,
        }
    finally:
        conn.close()


@app.post("/api/runs")
def trigger_run(
    limit: int | None = None,
    dry_run: bool = False,
) -> JSONResponse:
    result = run_cycle(trigger="manual", limit=limit, dry_run=dry_run)
    status = 200 if result.get("ok") else 409
    return JSONResponse(result, status_code=status)


@app.get("/", response_class=HTMLResponse)
@app.get("/companies", response_class=HTMLResponse)
@app.get("/profile", response_class=HTMLResponse)
@app.get("/questions", response_class=HTMLResponse)
def dashboard() -> str:
    return PAGE


def serve() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8788"))
    uvicorn.run("autoapply.web:app", host="127.0.0.1", port=port, reload=False)
