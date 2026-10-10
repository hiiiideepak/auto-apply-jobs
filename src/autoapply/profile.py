"""Load and save the candidate profile and Greenhouse company list."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "config" / "profile.yaml"
BOARDS_PATH = ROOT / "config" / "greenhouse_boards.txt"


def load_profile(path: Path | None = None) -> dict:
    raw = (path or PROFILE_PATH).read_text()
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError("profile.yaml must be a mapping")
    data.setdefault("skip_companies", [])
    data.setdefault("employers", [])
    data.setdefault("skills", {})
    data.setdefault("saved_answers", [])
    return data


_TOKEN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_BOARDS_HEADER = (
    "# Greenhouse board tokens scanned for software roles in India or worldwide-remote.\n"
    "# One token per line. Lines starting with # are ignored.\n"
)
_REQUIRED_PROFILE = ("name", "first_name", "last_name", "email", "phone", "resume_path")
_INT_FIELDS = (
    "current_ctc_inr",
    "expected_ctc_inr",
    "notice_period_days",
    "years_experience",
    "daily_target",
    "per_run_limit",
    "max_per_company_per_day",
    "run_interval_hours",
)
_BOOL_FIELDS = (
    "willing_to_relocate",
    "open_to_remote",
    "employment_restrictions",
    "outside_business_activities",
)


def load_board_tokens(path: Path | None = None) -> list[str]:
    tokens: list[str] = []
    for line in (path or BOARDS_PATH).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line)
    return tokens


def normalize_board_token(raw: str) -> str:
    text = (raw or "").strip()
    found = re.search(r"greenhouse\.io/([A-Za-z0-9-]+)", text)
    if found:
        text = found.group(1)
    text = text.lower().strip("/")
    if not _TOKEN.fullmatch(text):
        raise ValueError("Enter a Greenhouse board token, such as gitlab")
    return text


def save_board_tokens(tokens: list[str], path: Path | None = None) -> list[str]:
    cleaned: list[str] = []
    for token in tokens:
        token = normalize_board_token(token)
        if token not in cleaned:
            cleaned.append(token)
    target = path or BOARDS_PATH
    body = _BOARDS_HEADER + "\n".join(cleaned) + ("\n" if cleaned else "")
    target.write_text(body)
    return cleaned


def board_token_from_external_id(external_id: str | None) -> str:
    if not external_id or ":" not in external_id:
        return ""
    return external_id.split(":", 1)[0]


def save_profile(data: dict, path: Path | None = None) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Profile must be an object")
    profile = dict(data)
    for key in _REQUIRED_PROFILE:
        if not str(profile.get(key) or "").strip():
            raise ValueError(f"{key.replace('_', ' ')} is required")
    if "@" not in str(profile["email"]):
        raise ValueError("email must contain @")
    for key in _INT_FIELDS:
        if profile.get(key) in (None, ""):
            continue
        profile[key] = int(profile[key])
    for key in _BOOL_FIELDS:
        profile[key] = bool(profile.get(key))
    skills = {}
    for name, years in (profile.get("skills") or {}).items():
        label = str(name).strip().lower()
        if not label:
            continue
        skills[label] = int(years)
    profile["skills"] = skills
    profile["languages"] = [str(item).strip() for item in (profile.get("languages") or []) if str(item).strip()]
    profile["skip_companies"] = [
        str(item).strip() for item in (profile.get("skip_companies") or []) if str(item).strip()
    ]
    employers = []
    for item in profile.get("employers") or []:
        name = str((item or {}).get("name") or "").strip()
        if not name:
            continue
        employers.append({"name": name, "current": bool(item.get("current"))})
    profile["employers"] = employers
    knowledge = []
    for item in profile.get("knowledge") or []:
        title = str((item or {}).get("title") or "").strip()
        text = str((item or {}).get("text") or "").strip()
        if not title or not text:
            continue
        knowledge.append({"title": title, "text": text})
    profile["knowledge"] = knowledge
    saved_answers = []
    for item in profile.get("saved_answers") or []:
        question = str((item or {}).get("question") or "").strip()
        answer = str((item or {}).get("answer") or "").strip()
        if question and answer:
            saved_answers.append({"question": question, "answer": answer})
    profile["saved_answers"] = saved_answers
    target = path or PROFILE_PATH
    target.write_text(
        yaml.safe_dump(profile, sort_keys=False, allow_unicode=True, width=88)
    )
    return load_profile(target)


def worked_at(profile: dict, company: str) -> bool:
    hay = (company or "").lower()
    if not hay:
        return False
    for employer in profile.get("employers") or []:
        name = (employer.get("name") or "").lower()
        if name and name in hay:
            return True
    return False
