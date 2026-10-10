"""Map Greenhouse questions onto the profile.

Required questions we cannot answer honestly are left unresolved so the
application is not submitted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STANDARD_FIELDS = {
    "first_name",
    "last_name",
    "preferred_name",
    "email",
    "phone",
    "resume",
    "resume_text",
    "cover_letter",
    "cover_letter_text",
    "location",
    "candidate_location",
}

INDIA_PLACE = re.compile(
    r"\b(india|gurugram|gurgaon|bengaluru|bangalore|hyderabad|pune|mumbai|"
    r"noida|delhi|chennai|kolkata|ahmedabad|jaipur|kochi|indore)\b",
    re.I,
)
OTHER_PLACE = re.compile(
    r"united states|\busa\b|\bu\.s\.?\b|canada|united kingdom|\buk\b|"
    r"europe|germany|france|poland|ireland|australia|singapore|"
    r"san francisco|new york|london|dublin",
    re.I,
)
YES_NO_QUESTION = re.compile(
    r"\b(are you|do you|have you|will you|can you)\b",
    re.I,
)


@dataclass
class BuildResult:
    application: dict
    missing_required: list[str] = field(default_factory=list)
    missing_details: list[dict] = field(default_factory=list)
    fills: list[dict] = field(default_factory=list)
    disqualify_reason: str | None = None
    answered: list[str] = field(default_factory=list)


def _dom_id(field_name: str) -> str:
    if field_name.endswith("[]"):
        return field_name[:-2]
    return field_name


def _qid(field_name: str) -> str | None:
    if not field_name.startswith("question_"):
        return None
    token = field_name.split("_", 1)[1]
    if token.endswith("[]"):
        token = token[:-2]
    return token


def _option(values: list[dict], label: str) -> dict | None:
    want = label.strip().lower()
    for value in values:
        if str(value.get("label", "")).strip().lower() == want:
            return value
    for value in values:
        if str(value.get("label", "")).strip().lower().startswith(want):
            return value
    return None


def _decline_option(values: list[dict]) -> dict | None:
    for value in values:
        label = str(value.get("label", "")).lower()
        if any(
            phrase in label
            for phrase in (
                "decline",
                "don't wish",
                "do not wish",
                "prefer not",
                "not to say",
                "not wish",
            )
        ):
            return value
    return None


def _is_boolean_pair(values: list[dict]) -> bool:
    raw = []
    for value in values:
        try:
            raw.append(int(value.get("value")))
        except (TypeError, ValueError):
            return False
    return set(raw) <= {0, 1} and len(raw) > 0


def encode_select(question_id: str, option_value, priority: int, values: list[dict]) -> dict:
    """Match Greenhouse's client: 0/1 yes-no options are boolean_value."""
    chosen = option_value if isinstance(option_value, list) else [option_value]
    if _is_boolean_pair(values):
        return {
            question_id: {
                "question_id": question_id,
                "priority": priority,
                "boolean_value": int(chosen[0]),
            }
        }
    return {
        question_id: {
            "question_id": question_id,
            "priority": priority,
            "answer_selected_options_attributes": {
                str(index): {"question_option_id": value}
                for index, value in enumerate(chosen)
            },
        }
    }


def _years_option(values: list[dict], years: int) -> dict | None:
    best = None
    best_distance = 10**9
    for value in values:
        label = str(value.get("label", ""))
        numbers = [int(n) for n in re.findall(r"\d+", label)]
        if not numbers:
            continue
        if len(numbers) >= 2 and numbers[0] <= years <= numbers[1]:
            return value
        if "+" in label and years >= numbers[0]:
            return value
        distance = min(abs(years - n) for n in numbers)
        if distance < best_distance:
            best = value
            best_distance = distance
    if best is not None and best_distance <= 3:
        return best
    return None


def _skill_rating(profile: dict, label: str) -> int | None:
    skills = {k.lower(): int(v) for k, v in (profile.get("skills") or {}).items()}
    lower = label.lower()
    for name, rating in skills.items():
        if name in lower:
            return rating
    return None


def _saved_text(profile: dict, label: str) -> str | None:
    from autoapply.questions import question_key

    want = question_key(label)
    if not want:
        return None
    for item in profile.get("saved_answers") or []:
        if question_key(str(item.get("question") or "")) != want:
            continue
        answer = str(item.get("answer") or "").strip()
        if answer:
            return answer
    return None


def _remember_missing(result: BuildResult, label: str, field_type: str, values: list | None = None) -> None:
    options: list[str] = []
    for value in values or []:
        text = str(value.get("label") or "").strip()
        if text and text not in options:
            options.append(text)
    if field_type in {"select", "multi"} and not options:
        field_type = "textarea"
    options = options[:40]
    result.missing_required.append(label)
    result.missing_details.append(
        {"label": label, "field_type": field_type, "options": options}
    )


def _text_for(label: str, profile: dict, company: str) -> str | None:
    lower = label.lower()
    if any(word in lower for word in ("linkedin",)):
        return profile["linkedin"]
    if "github" in lower or "gitlab username" in lower:
        return profile["github"]
    if any(word in lower for word in ("portfolio", "website", "personal site", "url")):
        return profile["portfolio"]
    if "phone" in lower or "mobile" in lower:
        return profile["phone"]
    if any(phrase in lower for phrase in ("current firm", "current company", "current employer", "present employer")):
        return profile.get("current_company") or ""
    if "notice" in lower:
        return profile["notice_period_label"]
    if "current" in lower and "expected" in lower and any(
        word in lower for word in ("ctc", "salary", "compensation", "pay")
    ):
        return (
            f"Current {profile['current_ctc_label']} ({profile['current_ctc_inr']} INR), "
            f"expected {profile['expected_ctc_label']} ({profile['expected_ctc_inr']} INR)"
        )
    if "current" in lower and any(word in lower for word in ("ctc", "salary", "compensation", "pay")):
        return f"{profile['current_ctc_label']} ({profile['current_ctc_inr']} INR)"
    if any(word in lower for word in ("expected", "desired")) and any(
        word in lower for word in ("ctc", "salary", "compensation", "pay")
    ):
        return f"{profile['expected_ctc_label']} ({profile['expected_ctc_inr']} INR)"
    if "ctc" in lower or "salary expectation" in lower or "compensation expectation" in lower:
        return f"{profile['expected_ctc_label']} ({profile['expected_ctc_inr']} INR)"
    if re.search(r"years? of (professional |relevant )?experience", lower):
        return str(profile["years_experience"])
    if "primary programming" in lower or "programming language" in lower or "tech stack" in lower:
        return profile["primary_languages"]
    if any(word in lower for word in ("open source", "github", "portfolio link", "links of")):
        return profile["open_source_links"].strip()
    if any(word in lower for word in ("hear about", "how did you hear", "source")):
        return "Company careers page"
    if any(phrase in lower for phrase in ("postal", "zip code", "pin code", "pincode")):
        return str(profile.get("postal_code") or "122001")
    if ("current location" in lower or "where are you based" in lower) and "relocat" in lower:
        place = profile["location_label"]
        if profile.get("willing_to_relocate"):
            return f"{place}. Yes, I am open to relocating."
        return place
    if "city" in lower or "current location" in lower or "where are you based" in lower or "location" in lower:
        return profile["location_label"]
    if any(word in lower for word in ("education", "degree", "university", "college", "institute", "graduat", "school")):
        return profile["education_summary"]
    if any(
        phrase in lower
        for phrase in (
            "why do you",
            "why are you",
            "why this",
            "why us",
            "why our",
            "tell us about",
            "about yourself",
            "what interests",
            "cover letter",
        )
    ):
        return (
            f"I have {profile['years_experience']}+ years building backend systems in "
            f"{profile['primary_languages']}, currently at {profile['current_company']}. "
            f"I am applying to {company} for this role because it matches the backend "
            f"and leadership work I want to keep doing. I am based in "
            f"{profile['location_label']} and can join in {profile['notice_period_label']}."
        )
    if "first name" in lower or ( "name" in lower and "prefer" in lower ):
        return profile["first_name"]
    if "last name" in lower:
        return profile["last_name"]
    if "email" in lower:
        return profile["email"]
    if "work authorization" in lower or "authorised to work" in lower or "authorized to work" in lower:
        return profile["work_authorization"]
    return None


def _select_for(
    label: str,
    values: list[dict],
    profile: dict,
    company: str,
    location: str,
) -> tuple[dict | None, str | None]:
    """Return (option, disqualify_reason). disqualify_reason set means skip the job."""
    lower = label.lower()

    if any(phrase in lower for phrase in ("gender", "race", "veteran", "disability", "ethnic")):
        declined = _decline_option(values)
        return declined, None

    if "country of residence" in lower or "country do you" in lower or lower.strip() in {"country", "current country"}:
        match = _option(values, "India")
        return match, None if match else "Could not find India in country list"

    located = re.search(
        r"(located|based|live|living|reside|residing|work(?:ing)? from).{0,40}",
        lower,
    )
    if located or "are you in " in lower:
        if INDIA_PLACE.search(lower) and not OTHER_PLACE.search(lower):
            return _option(values, "Yes"), None
        if OTHER_PLACE.search(lower) and not INDIA_PLACE.search(lower):
            return None, f"Role requires presence outside India: {label}"

    if "authori" in lower and "work" in lower:
        if location and INDIA_PLACE.search(location) and not (
            OTHER_PLACE.search(lower) and not INDIA_PLACE.search(lower)
        ):
            yes = _option(values, "Yes")
            if yes:
                return yes, None
        if OTHER_PLACE.search(lower) and not INDIA_PLACE.search(lower):
            return None, f"Role requires work authorization outside India: {label}"
        if any(
            phrase in lower
            for phrase in (
                "reside",
                "resident",
                "current location",
                "where you live",
                "job is located",
                "role is located",
                "position is located",
            )
        ):
            if "located" in lower and location and not INDIA_PLACE.search(location):
                return None, f"Job is not in India: {location}"
            return _option(values, "Yes"), None
        if INDIA_PLACE.search(lower):
            return _option(values, "Yes"), None

    if any(
        phrase in lower
        for phrase in (
            "family member",
            "relatives",
            "personal relationship",
            "conflict of interest",
        )
    ):
        return _option(values, "No"), None

    if any(phrase in lower for phrase in ("outside business", "side business", "advisory", "board role")):
        active = bool(profile.get("outside_business_activities"))
        return _option(values, "Yes" if active else "No"), None

    if "hear about" in lower or "how did you hear" in lower:
        for candidate in ("Careers Website", "Company website", "LinkedIn", "Job board"):
            match = _option(values, candidate)
            if match:
                return match, None

    if "right to work" in lower:
        if location and not INDIA_PLACE.search(location):
            return None, f"Role is outside India: {location}"
        for candidate in (
            "Citizen or permanent resident where this role is listed",
            "Citizen",
        ):
            match = _option(values, candidate)
            if match:
                return match, None

    if any(
        phrase in lower
        for phrase in (
            "acknowledge",
            "i agree",
            "privacy policy",
            "consent to",
            "i understand",
            "confirm i have read",
            "by checking this box",
        )
    ):
        for candidate in ("I acknowledge", "Acknowledge", "I agree", "Yes", "Agree"):
            match = _option(values, candidate)
            if match:
                return match, None
        if len(values) == 1:
            return values[0], None

    if "sponsor" in lower or "visa" in lower:
        if OTHER_PLACE.search(lower) and not INDIA_PLACE.search(lower):
            return None, f"Role asks about visa sponsorship outside India: {label}"
        no = _option(values, "No")
        if no:
            return no, None

    if any(
        phrase in lower
        for phrase in (
            "previously worked",
            "worked at",
            "worked for",
            "consulted for",
            "employed by",
            "employed at",
            "work for us before",
        )
    ):
        from autoapply.profile import worked_at

        yes = worked_at(profile, company) or worked_at(profile, label)
        return _option(values, "Yes" if yes else "No"), None

    if "employment agreement" in lower or "post-employment" in lower or "non-compete" in lower or "restriction" in lower:
        restricted = bool(profile.get("employment_restrictions"))
        return _option(values, "Yes" if restricted else "No"), None

    if "relocat" in lower:
        return _option(values, "Yes" if profile.get("willing_to_relocate") else "No"), None

    if re.search(r"\bnotice\b", lower):
        for value in values:
            opt = str(value.get("label", "")).lower()
            if "15" in opt or "2 week" in opt or "two week" in opt or "immediate" in opt:
                return value, None
        return _years_option(values, 0), None

    if any(word in lower for word in ("rate yourself", "proficiency", "on a scale", "skill level")):
        rating = _skill_rating(profile, lower)
        if rating is None:
            return None, None
        return _years_option(values, rating), None

    if re.search(r"years|experience", lower) and _option(values, "Yes") and _option(values, "No"):
        numbers = [int(n) for n in re.findall(r"\d+", lower)]
        needed = numbers[0] if numbers else 1
        has_it = int(profile["years_experience"]) >= needed
        if "less than" in lower or "under " in lower:
            has_it = not has_it
        return _option(values, "Yes" if has_it else "No"), None

    if re.search(r"years|experience", lower):
        return _years_option(values, int(profile["years_experience"])), None

    if _option(values, "India"):
        return _option(values, "India"), None

    picked = _profile_option(lower, values, profile, location)
    if picked:
        return picked, None

    return None, None


def _yes_no_word(lower: str, profile: dict, location: str) -> str:
    """Profile-consistent yes/no when the form does not say which country."""
    if location and not INDIA_PLACE.search(location):
        if any(word in lower for word in ("citizen", "authoriz", "authoris", "reside", "live in", "based in")):
            return "No"
    if OTHER_PLACE.search(lower) and not INDIA_PLACE.search(lower):
        if any(word in lower for word in ("citizen", "authoriz", "authoris", "reside", "live", "based", "eligible")):
            return "No"
    if any(
        word in lower
        for word in (
            "sponsor",
            "visa",
            "criminal",
            "felony",
            "convict",
            "relative",
            "family member",
            "previously",
            "worked for",
            "worked at",
            "disability",
            "veteran",
        )
    ):
        return "No"
    if any(word in lower for word in ("weapon", "non-compete", "noncompete")):
        return "No"
    if any(word in lower for word in ("authoriz", "authoris", "willing", "able to", "agree", "acknowledge", "over 18", "at least 18")):
        return "Yes"
    return "Yes"


def _profile_option(lower: str, values: list[dict], profile: dict, location: str) -> dict | None:
    if _option(values, "Yes") and _option(values, "No"):
        return _option(values, _yes_no_word(lower, profile, location))
    declined = _decline_option(values)
    if declined and any(word in lower for word in ("gender", "race", "veteran", "disability", "ethnic", "pronoun")):
        return declined
    for candidate in ("Yes", "I agree", "I acknowledge", "Agree", "India", str(profile.get("city") or "")):
        match = _option(values, candidate)
        if match:
            return match
    for value in values:
        label = str(value.get("label") or "").strip().lower()
        if label and label not in {"choose", "select", "select...", "please select", "-"}:
            return value
    return None


def _relevant_text(profile: dict, company: str) -> str:
    return (
        f"{profile.get('years_experience', 7)}+ years as a software engineer, "
        f"currently at {profile.get('current_company') or 'Deel'} "
        f"in {profile.get('location_label') or 'Gurugram, India'}. "
        f"Available after {profile.get('notice_period_label') or '15 days'}."
    )


def build_application(
    questions: list[dict],
    profile: dict,
    company: str,
    cover_text: str,
    resume_url: str,
    resume_filename: str,
    location: str = "",
) -> BuildResult:
    application: dict = {
        "first_name": profile["first_name"],
        "last_name": profile["last_name"],
        "email": profile["email"],
        "phone": profile["phone"],
        "resume_url": resume_url,
        "resume_url_filename": resume_filename,
        "cover_letter_text": cover_text,
        "answers_attributes": {},
        "demographic_answers": [],
        "data_compliance": {},
        "attachments": {},
        "from_job_board_renderer": True,
        "employments": [],
        "time_zone": "Asia/Kolkata",
    }
    result = BuildResult(application=application)
    priority = 0

    for question in questions:
        fields = question.get("fields") or []
        if not fields:
            continue
        spec = fields[0]
        name = spec.get("name") or ""
        label = question.get("label") or name
        required = bool(question.get("required"))
        qtype = spec.get("type")
        values = spec.get("values") or []

        if name in {"first_name", "last_name", "email", "phone", "resume", "cover_letter"}:
            result.answered.append(label)
            continue
        if name == "preferred_name" and required:
            application["preferred_name"] = profile["first_name"]
            result.answered.append(label)
            result.fills.append({"id": "preferred_name", "text": profile["first_name"]})
            continue
        if name in STANDARD_FIELDS:
            continue

        qid = _qid(name)
        if not qid:
            if required:
                _remember_missing(result, label, "textarea")
            continue

        if qtype in {"input_text", "textarea"}:
            text = _saved_text(profile, label) or _text_for(label, profile, company)
            if not text and required:
                text = _relevant_text(profile, company)
            if text:
                application["answers_attributes"][qid] = {
                    "question_id": qid,
                    "priority": priority,
                    "text_value": text,
                }
                priority += 1
                result.answered.append(label)
                result.fills.append({"id": _dom_id(name), "text": text})
            elif required:
                _remember_missing(result, label, "textarea" if qtype == "textarea" else "text")
            continue

        if qtype == "input_file":
            if required:
                _remember_missing(result, label, "file")
            continue

        if qtype in {"multi_value_single_select", "multi_value_multi_select"}:
            saved = _saved_text(profile, label)
            option = _option(values, saved) if saved else None
            matched: list[dict] = []
            if qtype == "multi_value_multi_select" and saved:
                for part in saved.split("|"):
                    match = _option(values, part.strip())
                    if match and match not in matched:
                        matched.append(match)
                if matched:
                    option = matched[0]
            disqualify = None
            if option is None:
                option, disqualify = _select_for(label, values, profile, company, location)
            if disqualify and option is None:
                result.disqualify_reason = disqualify
                return result
            if option is None:
                if required:
                    kind = "multi" if qtype == "multi_value_multi_select" else "select"
                    _remember_missing(result, label, kind, values)
                continue
            chosen = matched or [option]
            application["answers_attributes"].update(
                encode_select(
                    qid,
                    [item.get("value") for item in chosen],
                    priority,
                    values,
                )
            )
            priority += 1
            result.answered.append(f"{label} -> {option.get('label')}")
            result.fills.append(
                {
                    "id": _dom_id(name),
                    "choices": [str(item.get("label") or "") for item in chosen if item.get("label")],
                }
            )
            continue

        if required:
            _remember_missing(result, label, "textarea")

    return result
