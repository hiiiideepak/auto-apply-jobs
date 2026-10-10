from fastapi.testclient import TestClient

from autoapply.answers import build_application
from autoapply.db import connect
from autoapply.profile import load_profile, save_profile
from autoapply.questions import (
    list_open_questions,
    release_answered_jobs,
    save_custom_answer,
    sync_open_questions,
)
from autoapply.web import app
from tests.test_answers import PROFILE, _q


def test_text_answer_is_kept_for_the_browser_form():
    built = build_application(
        [_q("Which institute did you graduate from?", "question_42")],
        {
            **PROFILE,
            "saved_answers": [
                {"question": "Which institute did you graduate from?", "answer": "NIT Allahabad"}
            ],
        },
        "GitLab",
        "cover",
        "https://example.com/resume.pdf",
        "cv.pdf",
        "Bangalore, India",
    )
    assert built.fills == [{"id": "question_42", "text": "NIT Allahabad"}]


def test_saved_text_answer_fills_unknown_question():
    profile = {
        **PROFILE,
        "saved_answers": [
            {
                "question": "Which institute did you graduate from?",
                "answer": "NIT Allahabad",
            }
        ],
    }
    built = build_application(
        [_q("Which institute did you graduate from?", "question_42")],
        profile,
        "GitLab",
        "cover",
        "https://example.com/resume.pdf",
        "cv.pdf",
        "Bangalore, India",
    )
    assert built.missing_required == []
    assert built.application["answers_attributes"]["42"]["text_value"] == "NIT Allahabad"


def test_saved_answer_selects_matching_option():
    values = [
        {"label": "Immediately", "value": 11},
        {"label": "In 15 days", "value": 12},
    ]
    profile = {
        **PROFILE,
        "saved_answers": [
            {
                "question": "How soon would you be available to join?",
                "answer": "In 15 days",
            }
        ],
    }
    built = build_application(
        [
            _q(
                "How soon would you be available to join?",
                "question_7",
                qtype="multi_value_single_select",
                values=values,
            )
        ],
        profile,
        "Payoneer",
        "cover",
        "https://example.com/resume.pdf",
        "cv.pdf",
        "India",
    )
    assert built.missing_required == []
    selected = built.application["answers_attributes"]["7"]["answer_selected_options_attributes"]["0"]
    assert selected["question_option_id"] == 12


def test_saved_multi_select_keeps_each_option():
    values = [
        {"label": "None of the above", "value": 1},
        {"label": "Export controls", "value": 2},
    ]
    profile = {
        **PROFILE,
        "saved_answers": [
            {
                "question": "Please confirm whether any of the below applies to you.",
                "answer": "None of the above | Export controls",
            }
        ],
    }
    built = build_application(
        [
            _q(
                "Please confirm whether any of the below applies to you.",
                "question_3",
                qtype="multi_value_multi_select",
                values=values,
            )
        ],
        profile,
        "Databricks",
        "cover",
        "https://example.com/resume.pdf",
        "cv.pdf",
        "India",
    )
    selected = built.application["answers_attributes"]["3"]["answer_selected_options_attributes"]
    assert selected["0"]["question_option_id"] == 1
    assert selected["1"]["question_option_id"] == 2


def test_choice_options_are_attached_to_open_questions(tmp_path):
    from autoapply.questions import attach_choice_options, choice_questions

    parsed = choice_questions(
        {
            "questions": [
                {
                    "label": "Have you personally driven architecture across multiple teams?",
                    "fields": [
                        {
                            "type": "multi_value_single_select",
                            "values": [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
                        }
                    ],
                }
            ]
        }
    )
    assert parsed[0]["options"] == ["Yes", "No"]
    conn = connect(tmp_path / "opts.db")
    conn.execute(
        """
        INSERT INTO open_questions (
            question_key, label, field_type, options_json, asked_json, created_at, updated_at
        ) VALUES ('have you personally driven architecture across multiple teams',
                  'Have you personally driven architecture across multiple teams?',
                  'textarea', '[]', '[]', 't', 't')
        """
    )
    conn.commit()
    assert attach_choice_options(conn, parsed) == 1
    conn.commit()
    row = conn.execute("SELECT field_type, options_json FROM open_questions").fetchone()
    assert row["field_type"] == "select"
    assert row["options_json"] == '["Yes", "No"]'


def test_unknown_select_is_offered_with_options():
    values = [{"label": "Yes", "value": 1}, {"label": "No", "value": 2}, {"label": "Maybe", "value": 3}]
    built = build_application(
        [_q("Do you own a drone?", "question_8", qtype="multi_value_single_select", values=values)],
        PROFILE,
        "GitLab",
        "cover",
        "https://example.com/resume.pdf",
        "cv.pdf",
        "India",
    )
    assert built.missing_required == []
    assert any(item.get("choices") == ["Yes"] for item in built.fills)


def _base_profile():
    return {
        "name": "Deepak Bharti",
        "first_name": "Deepak",
        "last_name": "Bharti",
        "email": "deepakbharti823@gmail.com",
        "phone": "+91 7275036250",
        "resume_path": "/tmp/resume.pdf",
        "saved_answers": [],
    }


def test_answering_requeues_a_blocked_role(tmp_path, monkeypatch):
    profile_path = tmp_path / "profile.yaml"
    save_profile(_base_profile(), profile_path)
    monkeypatch.setattr("autoapply.profile.PROFILE_PATH", profile_path)
    db_path = tmp_path / "jobs.db"
    monkeypatch.setattr("autoapply.db.DB_PATH", db_path)
    reason = (
        "Unanswered required questions: Which institute did you graduate from?; "
        "Have you personally driven architecture across multiple teams?"
    )
    conn = connect(db_path)
    conn.execute(
        """
        INSERT INTO jobs (
            dedupe_key, source, external_id, title, company, location, url,
            score, status, skip_reason, created_at, updated_at
        ) VALUES ('k', 'greenhouse', 'turing:1', 'Staff Software Engineer', 'Turing',
                  'India', 'https://example.com', 10, 'needs_review', ?, 't', 't')
        """,
        (reason,),
    )
    conn.commit()
    sync_open_questions(conn, load_profile())
    labels = [item["label"] for item in list_open_questions(conn)]
    assert "Which institute did you graduate from?" in labels
    assert "Have you personally driven architecture across multiple teams?" in labels

    profile = save_custom_answer("Which institute did you graduate from?", "NIT Allahabad")
    sync_open_questions(conn, profile)
    assert release_answered_jobs(conn, profile) == 0
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "needs_review"

    profile = save_custom_answer(
        "Have you personally driven architecture across multiple teams?",
        "Yes, at Payoneer",
    )
    sync_open_questions(conn, profile)
    assert release_answered_jobs(conn, profile) == 1
    assert conn.execute("SELECT status FROM jobs").fetchone()["status"] == "new"
    assert list_open_questions(conn) == []

    client = TestClient(app)
    listed = client.get("/api/questions")
    assert listed.status_code == 200
    assert listed.json()["saved"][0]["answer"] == "NIT Allahabad"
    kept = client.put(
        "/api/profile",
        json={**_base_profile(), "city": "Gurugram", "saved_answers": []},
    )
    assert kept.status_code == 200
    assert kept.json()["saved_answers"][1]["answer"] == "Yes, at Payoneer"
