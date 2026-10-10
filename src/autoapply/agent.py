"""Apply agent.

The model retrieves resume chunks, then calls tools to skip a role or submit
an application. Yes/no legal answers still come from the profile, not the model.
"""

from __future__ import annotations

import json
import logging
import re

from autoapply.db import now_iso
from autoapply.greenhouse import submit_job
from autoapply.llm import AnthropicLLM, llm_configured
from autoapply.profile import board_token_from_external_id, load_board_tokens
from autoapply.questions import record_open_questions
from autoapply.rag import ResumeIndex

log = logging.getLogger(__name__)

TOOLS = [
    {
        "name": "get_budget",
        "description": "How many applications can still be submitted in this run, and how many were already submitted.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "list_jobs",
        "description": "List the next Greenhouse roles waiting for a decision. Call this before applying.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 5}},
            "additionalProperties": False,
        },
    },
    {
        "name": "retrieve_resume",
        "description": (
            "Retrieve resume passages relevant to a role. Required before submit_application. "
            "Query with the job title, company, and the skills the role asks for."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "query": {"type": "string"},
            },
            "required": ["job_id", "query"],
        },
    },
    {
        "name": "submit_application",
        "description": (
            "Submit one application. The cover letter may only use facts returned by retrieve_resume "
            "for this job_id. Profile facts such as notice period and work authorization are filled separately."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "cover_letter": {"type": "string"},
            },
            "required": ["job_id", "cover_letter"],
        },
    },
    {
        "name": "skip_job",
        "description": "Skip a role that is a poor fit. Give a short reason.",
        "input_schema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "integer"},
                "reason": {"type": "string"},
            },
            "required": ["job_id", "reason"],
        },
    },
]

SYSTEM = """You are Deepak Bharti's job application agent.

Decide which queued Greenhouse roles to apply to. Use the tools. Do not invent employers, projects, or permissions.

Rules:
- Call get_budget first, then list_jobs.
- Before submit_application, call retrieve_resume for that job_id. Write the cover letter only from those passages plus the job title and company.
- Skip roles whose main stack is one he has not done, or that need work outside India.
- Prefer backend, Python, TypeScript/Node, data pipelines, and team lead work.
- A cover letter is 90 to 160 words, specific to the company, and ends with his name.
- Stop when get_budget says remaining is 0, or list_jobs returns no roles.
- needs_review and manual do not use the budget. Call list_jobs again and continue with a different role.
"""


def _grounded(cover_letter: str, passages: list[dict]) -> bool:
    blob = " ".join(passage["text"] for passage in passages).lower()
    evidence = {token for token in re.findall(r"[a-z0-9+#]{5,}", blob)}
    used = {token for token in re.findall(r"[a-z0-9+#]{5,}", cover_letter.lower())}
    return len(evidence & used) >= 3


class ApplyTools:
    def __init__(
        self,
        *,
        conn,
        profile: dict,
        client,
        resume_url: str,
        resume_filename: str,
        budget: int,
        per_company: int,
        company_counts: dict,
        run_id: int,
    ):
        self.conn = conn
        self.profile = profile
        self.client = client
        self.resume_url = resume_url
        self.resume_filename = resume_filename
        self.budget = budget
        self.per_company = per_company
        self.company_counts = company_counts
        self.run_id = run_id
        self.index = ResumeIndex(profile)
        self.retrieved: dict[int, list[dict]] = {}
        self.submitted = 0
        self.needs_review = 0
        self.skipped = 0
        self.failed = 0
        self.examined = 0

    def _event(self, kind: str, name: str, payload) -> None:
        self.conn.execute(
            """
            INSERT INTO agent_events (run_id, kind, name, payload, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (self.run_id, kind, name, json.dumps(payload)[:4000], now_iso()),
        )
        self.conn.commit()

    def _load_job(self, job_id: int):
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def _persist(self, row, result: dict, method: str) -> None:
        self.conn.execute(
            "UPDATE jobs SET status = ?, skip_reason = ?, company = ?, updated_at = ? WHERE id = ?",
            (
                result["status"],
                result.get("error"),
                result.get("company") or row["company"],
                now_iso(),
                row["id"],
            ),
        )
        self.conn.execute(
            """
            INSERT INTO applications (
                job_id, method, status, answers_json, cover_letter,
                response_excerpt, error, applied_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"],
                method,
                result["status"],
                json.dumps(result.get("answers") or []),
                result.get("cover_letter") or "",
                (result.get("response") or "")[:800],
                result.get("error"),
                now_iso(),
            ),
        )
        if result.get("questions"):
            record_open_questions(
                self.conn,
                questions=result.get("questions") or [],
                company=result.get("company") or row["company"],
                title=result.get("title") or row["title"],
                profile=self.profile,
            )
        self.conn.commit()

    def call(self, name: str, arguments: dict) -> dict:
        arguments = arguments or {}
        if name == "get_budget":
            return {
                "remaining": max(0, self.budget - self.submitted),
                "submitted": self.submitted,
                "attempt_cap": self.budget * 3,
                "examined": self.examined,
            }
        if name == "list_jobs":
            if self.submitted >= self.budget or self.examined >= self.budget * 3:
                return {"jobs": [], "note": "Stop. The run cap is reached."}
            limit = max(1, min(int(arguments.get("limit") or 5), 5))
            allowed = set(load_board_tokens())
            rows = self.conn.execute(
                """
                SELECT id, title, company, location, url, score, external_id
                FROM jobs
                WHERE status = 'new' AND source = 'greenhouse'
                ORDER BY score DESC, id ASC
                """
            ).fetchall()
            jobs = []
            for row in rows:
                if board_token_from_external_id(row["external_id"]) not in allowed:
                    continue
                item = dict(row)
                item.pop("external_id", None)
                jobs.append(item)
                if len(jobs) >= limit:
                    break
            return {"jobs": jobs}
        if name == "retrieve_resume":
            job_id = int(arguments["job_id"])
            passages = self.index.retrieve(str(arguments.get("query") or ""), limit=3)
            self.retrieved[job_id] = passages
            return {"job_id": job_id, "passages": passages}
        if name == "skip_job":
            return self._skip(int(arguments["job_id"]), str(arguments.get("reason") or "Poor fit"))
        if name == "submit_application":
            return self._submit(int(arguments["job_id"]), str(arguments.get("cover_letter") or ""))
        return {"error": f"Unknown tool {name}"}

    def _skip(self, job_id: int, reason: str) -> dict:
        row = self._load_job(job_id)
        if row is None or row["status"] != "new":
            return {"error": "That role is not waiting for a decision."}
        result = {"status": "skipped", "error": reason, "company": row["company"], "cover_letter": ""}
        self._persist(row, result, "agent")
        self.skipped += 1
        self.examined += 1
        return {"status": "skipped", "job_id": job_id}

    def _submit(self, job_id: int, cover_letter: str) -> dict:
        if self.submitted >= self.budget:
            return {"error": "Budget is used up. Stop."}
        passages = self.retrieved.get(job_id) or []
        if not passages:
            return {"error": "Call retrieve_resume for this job_id before submitting."}
        if not _grounded(cover_letter, passages):
            return {
                "error": "Cover letter does not use the retrieved resume. Rewrite it from those passages."
            }
        row = self._load_job(job_id)
        if row is None or row["status"] != "new":
            return {"error": "That role is not waiting for a decision."}
        token = row["external_id"].split(":", 1)[0]
        if self.company_counts.get(token.lower(), 0) >= self.per_company:
            return {"error": f"Already applied to {row['company']} enough times today. Skip it."}
        try:
            result = submit_job(
                self.client,
                token=token,
                job_id=row["external_id"].split(":", 1)[1],
                profile=self.profile,
                resume_url=self.resume_url,
                resume_filename=self.resume_filename,
                cover_text=cover_letter,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("agent submit failed")
            result = {
                "status": "failed",
                "error": str(exc),
                "company": row["company"],
                "title": row["title"],
                "cover_letter": cover_letter,
            }
        self._persist(row, result, "agent")
        self.examined += 1
        status = result["status"]
        if status == "submitted":
            self.submitted += 1
            self.company_counts[token.lower()] = self.company_counts.get(token.lower(), 0) + 1
        elif status == "needs_review":
            self.needs_review += 1
        elif status == "skipped":
            self.skipped += 1
        elif status == "manual":
            self.examined -= 1
        else:
            self.failed += 1
        return {
            "status": status,
            "job_id": job_id,
            "company": result.get("company") or row["company"],
            "title": result.get("title") or row["title"],
            "note": result.get("error") or "submitted",
        }


def run_apply_agent(
    *,
    conn,
    profile: dict,
    client,
    resume_url: str,
    resume_filename: str,
    budget: int,
    per_company: int,
    company_counts: dict,
    run_id: int,
    llm=None,
) -> dict:
    tools = ApplyTools(
        conn=conn,
        profile=profile,
        client=client,
        resume_url=resume_url,
        resume_filename=resume_filename,
        budget=budget,
        per_company=per_company,
        company_counts=company_counts,
        run_id=run_id,
    )
    model = llm if llm is not None else AnthropicLLM()
    messages = [
        {
            "role": "user",
            "content": (
                f"Apply to at most {budget} matching Greenhouse roles. "
                "Use retrieve_resume before every submission."
            ),
        }
    ]
    max_steps = budget * 6 + 2
    for _ in range(max_steps):
        completion = model.complete(system=SYSTEM, messages=messages, tools=TOOLS)
        blocks = completion.get("content") or []
        messages.append({"role": "assistant", "content": blocks})
        tool_uses = [block for block in blocks if block.get("type") == "tool_use"]
        if not tool_uses:
            break
        results = []
        for block in tool_uses:
            name = block.get("name") or ""
            arguments = block.get("input") or {}
            tools._event("tool_call", name, arguments)
            try:
                output = tools.call(name, arguments if isinstance(arguments, dict) else {})
            except Exception as exc:  # noqa: BLE001
                output = {"error": str(exc)}
            tools._event("tool_result", name, output)
            log.info("agent %s -> %s", name, json.dumps(output)[:300])
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.get("id"),
                    "content": json.dumps(output),
                }
            )
        messages.append({"role": "user", "content": results})
        if tools.failed >= 3:
            break
    return {
        "submitted": tools.submitted,
        "needs_review": tools.needs_review,
        "skipped": tools.skipped,
        "failed": tools.failed,
        "agent": True,
        "model": getattr(model, "model", "scripted"),
    }


def agent_will_run() -> bool:
    return llm_configured()
