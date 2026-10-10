from fastapi.testclient import TestClient

from autoapply.db import connect
from autoapply.profile import save_board_tokens, save_profile
from autoapply.web import app


def test_profile_roundtrip(tmp_path):
    target = tmp_path / "profile.yaml"
    saved = save_profile(
        {
            "name": "Deepak Bharti",
            "first_name": "Deepak",
            "last_name": "Bharti",
            "email": "deepakbharti823@gmail.com",
            "phone": "+91 7275036250",
            "resume_path": "/tmp/resume.pdf",
            "skills": {"python": "4"},
            "languages": ["English", ""],
            "employers": [{"name": "Deel", "current": True}, {"name": ""}],
            "knowledge": [{"title": "AI", "text": "RAG and tool calling"}],
            "skip_companies": ["Deel"],
            "years_experience": "7",
            "outside_business_activities": False,
        },
        target,
    )
    assert saved["skills"]["python"] == 4
    assert saved["languages"] == ["English"]
    assert saved["employers"] == [{"name": "Deel", "current": True}]
    assert saved["knowledge"][0]["title"] == "AI"
    assert "email must contain @" in _error(save_profile, {**saved, "email": "nope"}, target)


def _error(fn, *args):
    try:
        fn(*args)
    except ValueError as exc:
        return str(exc)
    raise AssertionError("expected ValueError")


def test_company_add_and_remove(tmp_path, monkeypatch):
    boards = tmp_path / "boards.txt"
    boards.write_text("# keep\ngitlab\n")
    db_path = tmp_path / "jobs.db"
    monkeypatch.setattr("autoapply.profile.BOARDS_PATH", boards)
    monkeypatch.setattr("autoapply.web.load_board_tokens", lambda: __import__("autoapply.profile", fromlist=["load_board_tokens"]).load_board_tokens(boards))
    monkeypatch.setattr("autoapply.web.save_board_tokens", lambda tokens: save_board_tokens(tokens, boards))
    monkeypatch.setattr("autoapply.db.DB_PATH", db_path)

    conn = connect(db_path)
    conn.execute(
        """
        INSERT INTO jobs (
            dedupe_key, source, external_id, title, company, location, url,
            score, status, skip_reason, created_at, updated_at
        ) VALUES ('k', 'greenhouse', 'exampleco:1', 'Backend Engineer', 'Example',
                  'India', 'https://example.com', 10, 'skipped',
                  'Removed from company list', 't', 't')
        """
    )
    conn.commit()
    conn.close()

    client = TestClient(app)
    added = client.post("/api/companies", json={"token": "https://job-boards.greenhouse.io/exampleco/jobs/1"})
    assert added.status_code == 200
    assert added.json()["added"] == "exampleco"
    tokens = [line.strip() for line in boards.read_text().splitlines() if line and not line.startswith("#")]
    assert tokens == ["gitlab", "exampleco"]
    restored = connect(db_path).execute("SELECT status FROM jobs").fetchone()["status"]
    assert restored == "new"

    removed = client.delete("/api/companies/exampleco")
    assert removed.status_code == 200
    assert "exampleco" not in [item["token"] for item in removed.json()["companies"]]
    row = connect(db_path).execute("SELECT status, skip_reason FROM jobs").fetchone()
    assert row["status"] == "skipped"
    assert row["skip_reason"] == "Removed from company list"

    bad = client.post("/api/companies", json={"token": "not a token"})
    assert bad.status_code == 400


def test_list_jobs_ignores_removed_board(tmp_path, monkeypatch):
    from autoapply.agent import ApplyTools
    from autoapply.profile import load_profile

    conn = connect(tmp_path / "agent.db")
    conn.execute(
        """
        INSERT INTO jobs (
            dedupe_key, source, external_id, title, company, location, url,
            score, status, created_at, updated_at
        ) VALUES ('k', 'greenhouse', 'removedco:9', 'Staff Engineer', 'Removed',
                  'Remote', 'https://example.com/j', 30, 'new', 't', 't')
        """
    )
    conn.execute("INSERT INTO runs (trigger, started_at) VALUES ('test', 't')")
    conn.commit()
    monkeypatch.setattr("autoapply.agent.load_board_tokens", lambda: ["gitlab"])
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
    assert tools.call("list_jobs", {"limit": 5})["jobs"] == []


def test_pages_render():
    client = TestClient(app)
    for path in ("/", "/companies", "/profile", "/questions"):
        response = client.get(path)
        assert response.status_code == 200
        assert "Run agent" in response.text
        assert 'href="/companies"' in response.text
