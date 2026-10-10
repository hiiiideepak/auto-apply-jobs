from autoapply.answers import build_application, encode_select
from autoapply.profile import worked_at

PROFILE = {
    "first_name": "Deepak",
    "last_name": "Bharti",
    "email": "deepakbharti823@gmail.com",
    "phone": "+91 7275036250",
    "location_label": "Gurugram, Haryana, India",
    "years_experience": 7,
    "notice_period_label": "15 days",
    "current_ctc_label": "50 LPA",
    "current_ctc_inr": 5000000,
    "expected_ctc_label": "65 LPA",
    "expected_ctc_inr": 6500000,
    "current_company": "Deel",
    "primary_languages": "TypeScript (Node.js) and Python",
    "linkedin": "https://www.linkedin.com/in/hiiideepak/",
    "github": "https://github.com/hiiiideepak",
    "portfolio": "https://deepakbharti823.pythonanywhere.com/",
    "open_source_links": "https://github.com/hiiiideepak",
    "education_summary": "B.Tech, NIT Allahabad",
    "work_authorization": "Authorized to work in India",
    "employment_restrictions": False,
    "willing_to_relocate": False,
    "postal_code": "122001",
    "outside_business_activities": False,
    "skills": {"python": 4, "ruby": 1},
    "employers": [
        {"name": "Deel", "current": True},
        {"name": "Payoneer", "current": False},
    ],
}


def _q(label, name, required=True, qtype="input_text", values=None):
    field = {"name": name, "type": qtype}
    if values is not None:
        field["values"] = values
    return {"label": label, "required": required, "fields": [field]}


def test_boolean_yes_no_encoding():
    values = [{"label": "Yes", "value": 1}, {"label": "No", "value": 0}]
    encoded = encode_select("38137963002", 1, 0, values)
    assert encoded["38137963002"]["boolean_value"] == 1


def test_option_id_encoding():
    values = [{"label": "India", "value": 253167000002}, {"label": "Italy", "value": 99}]
    encoded = encode_select("99", 253167000002, 2, values)
    selected = encoded["99"]["answer_selected_options_attributes"]["0"]
    assert selected["question_option_id"] == 253167000002


def test_india_backend_form():
    questions = [
        _q("First Name", "first_name"),
        _q("Last Name", "last_name"),
        _q("Email", "email"),
        _q("Resume/CV", "resume", qtype="input_file"),
        _q(
            "What is your current country of residence?",
            "question_1",
            qtype="multi_value_single_select",
            values=[{"label": "India", "value": 10}, {"label": "Canada", "value": 11}],
        ),
        _q(
            "Are you located in India?",
            "question_2",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        ),
        _q(
            "Have you previously worked at or consulted for GitLab?",
            "question_3",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        ),
        _q(
            "Do you have over 3 years of professional software engineering experience?",
            "question_4",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        ),
        _q("LinkedIn Profile", "question_5"),
        _q("What is your primary programming language and/or framework?", "question_6"),
    ]
    result = build_application(
        questions,
        PROFILE,
        "GitLab",
        "Hello",
        "https://example.com/resume.pdf",
        "DeepakBharti.pdf",
    )
    assert result.disqualify_reason is None
    assert result.missing_required == []
    answers = result.application["answers_attributes"]
    assert answers["2"]["boolean_value"] == 1
    assert answers["3"]["boolean_value"] == 0
    assert answers["4"]["boolean_value"] == 1
    assert answers["1"]["answer_selected_options_attributes"]["0"]["question_option_id"] == 10
    assert "TypeScript" in answers["6"]["text_value"]


def test_us_location_gate_is_skipped():
    questions = [
        _q(
            "Do you currently live in the United States or Canada?",
            "question_9",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        )
    ]
    result = build_application(questions, PROFILE, "GitLab", "Hello", "u", "f.pdf")
    assert result.disqualify_reason


def test_standard_compliance_questions():
    questions = [
        _q(
            "Are you legally authorized to work in the country you reside?",
            "question_10",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        ),
        _q(
            "Have you been employed by Okta, Inc. or any of its subsidiaries in the past?",
            "question_11",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        ),
        _q(
            "Do you have any family members at Okta?",
            "question_12",
            qtype="multi_value_single_select",
            values=[{"label": "Yes", "value": 1}, {"label": "No", "value": 0}],
        ),
        _q(
            "I acknowledge and agree to the processing of my personal data.",
            "question_13[]",
            qtype="multi_value_multi_select",
            values=[{"label": "I acknowledge", "value": 717919414}],
        ),
        _q("What's your postal code?", "question_14"),
    ]
    result = build_application(
        questions, PROFILE, "Okta", "Hello", "u", "f.pdf", location="Bengaluru, India"
    )
    assert result.missing_required == []
    answers = result.application["answers_attributes"]
    assert answers["10"]["boolean_value"] == 1
    assert answers["11"]["boolean_value"] == 0
    assert answers["12"]["boolean_value"] == 0
    assert answers["13"]["answer_selected_options_attributes"]["0"]["question_option_id"] == 717919414
    assert answers["14"]["text_value"] == "122001"


def test_common_greenhouse_selects():
    questions = [
        _q(
            "How did you hear about Twilio?",
            "question_20[]",
            qtype="multi_value_multi_select",
            values=[{"label": "Careers Website", "value": 7}, {"label": "Indeed", "value": 8}],
        ),
        _q(
            "What is the source of your right to work where this role is listed?",
            "question_21",
            qtype="multi_value_single_select",
            values=[
                {"label": "Citizen or permanent resident where this role is listed", "value": 9},
                {"label": "I do not currently have an unlimited right to work where this role is listed and will require sponsorship", "value": 10},
            ],
        ),
        _q(
            "By checking this box, I confirm I have read the candidate privacy notice",
            "question_22[]",
            qtype="multi_value_multi_select",
            values=[{"label": "Acknowledge", "value": 11}],
        ),
    ]
    result = build_application(
        questions, PROFILE, "Twilio", "Hello", "u", "f.pdf", location="Bangalore, India"
    )
    assert result.missing_required == []
    answers = result.application["answers_attributes"]
    assert answers["20"]["answer_selected_options_attributes"]["0"]["question_option_id"] == 7
    assert answers["21"]["answer_selected_options_attributes"]["0"]["question_option_id"] == 9
    assert answers["22"]["answer_selected_options_attributes"]["0"]["question_option_id"] == 11


def test_payoneer_history():
    assert worked_at(PROFILE, "Payoneer")
    assert not worked_at(PROFILE, "GitLab")
