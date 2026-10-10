from pathlib import Path

from autoapply.agent import ApplyTools, run_apply_agent
from autoapply.db import connect
from autoapply.profile import load_profile
from autoapply.rag import ResumeIndex


def test_resume_retrieval_finds_llm_work():
    index = ResumeIndex(load_profile())
    passages = index.retrieve("RAG tool calling AI agents")
    blob = " ".join(item["text"] for item in passages).lower()
    assert "rag" in blob
    assert "tool calling" in blob


def test_payoneer_passage_outranks_unrelated_text():
    index = ResumeIndex(load_profile())
    top = index.retrieve("led a team at Payoneer security deposit")[0]
    assert top["title"] == "Payoneer"


class ScriptedLLM:
    model = "scripted"

    def __init__(self, turns):
        self.turns = turns
        self.calls = 0

    def complete(self, *, system, messages, tools):
        turn = self.turns[self.calls]
        self.calls += 1
        return turn


def _db_with_job(tmp_path: Path):
    conn = connect(tmp_path / "test.db")
    conn.execute(
        """
        INSERT INTO jobs (
            dedupe_key, source, external_id, title, company, location, url,
            score, status, created_at, updated_at
        ) VALUES ('greenhouse:gitlab:1', 'greenhouse', 'gitlab:1',
                  'Senior Backend Engineer', 'GitLab', 'Bangalore, India',
                  'https://example.com/job', 20, 'new', 't', 't')
        """
    )
    conn.execute("INSERT INTO runs (trigger, started_at) VALUES ('test', 't')")
    conn.commit()
    return conn


def test_agent_retrieves_then_skips(tmp_path):
    conn = _db_with_job(tmp_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    llm = ScriptedLLM(
        [
            {
                "content": [
                    {"type": "tool_use", "id": "1", "name": "get_budget", "input": {}},
                    {"type": "tool_use", "id": "2", "name": "list_jobs", "input": {"limit": 1}},
                ]
            },
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "3",
                        "name": "retrieve_resume",
                        "input": {"job_id": job_id, "query": "backend python node"},
                    }
                ]
            },
            {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "4",
                        "name": "skip_job",
                        "input": {"job_id": job_id, "reason": "Ruby-only team"},
                    }
                ]
            },
            {"content": [{"type": "text", "text": "Done."}]},
        ]
    )
    summary = run_apply_agent(
        conn=conn,
        profile=load_profile(),
        client=None,
        resume_url="https://example.com/resume.pdf",
        resume_filename="DeepakBharti.pdf",
        budget=1,
        per_company=5,
        company_counts={},
        run_id=1,
        llm=llm,
    )
    assert summary["skipped"] == 1
    assert summary["submitted"] == 0
    status = conn.execute("SELECT status, skip_reason FROM jobs").fetchone()
    assert status["status"] == "skipped"
    assert "Ruby" in status["skip_reason"]
    events = conn.execute("SELECT name FROM agent_events ORDER BY id").fetchall()
    assert [row["name"] for row in events][:4] == [
        "get_budget",
        "get_budget",
        "list_jobs",
        "list_jobs",
    ]


def test_submit_requires_retrieval(tmp_path):
    conn = _db_with_job(tmp_path)
    job_id = conn.execute("SELECT id FROM jobs").fetchone()["id"]
    tools = ApplyTools(
        conn=conn,
        profile=load_profile(),
        client=None,
        resume_url="u",
        resume_filename="f.pdf",
        budget=1,
        per_company=5,
        company_counts={},
        run_id=1,
    )
    result = tools.call(
        "submit_application",
        {"job_id": job_id, "cover_letter": "I built RAG systems with tool calling at Deel."},
    )
    assert "retrieve_resume" in result["error"]
    tools.call("retrieve_resume", {"job_id": job_id, "query": "RAG tool calling"})
    ungrounded = tools.call(
        "submit_application",
        {"job_id": job_id, "cover_letter": "Hello, I like this opening a lot."},
    )
    assert "retrieved resume" in ungrounded["error"]
