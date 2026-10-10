"""Role and location filters for senior backend / full-stack searches."""

from __future__ import annotations

import re

INCLUDE = re.compile(
    r"("
    r"backend|back[- ]end|full[- ]stack|software engineer|software developer|"
    r"platform engineer|platform engineering|staff engineer|principal engineer|"
    r"tech(?:nical)? lead|engineering lead|team lead|"
    r"python engineer|node\.?js|java engineer|api engineer|"
    r"engineering manager|software engineering manager|"
    r"member of technical staff"
    r")",
    re.I,
)

EXCLUDE = re.compile(
    r"(?<!\w)("
    r"intern|internship|junior|fresher|new grad|co-?op|apprentice|"
    r"ios|android|mobile|front[- ]end|qa|sdet|quality assurance|"
    r"data scientist|machine learning|product manager|designer|recruiter|"
    r"sales|marketing|devops|site reliability|sre|security|"
    r"director|vp|vice president|chief|head of|"
    r"salesforce|\.net|c#|c\+\+|ruby|php|golang|rust|scala|elixir|sap|"
    r"swift|kotlin|operations|fraud|legal|analyst|support|customer success|"
    r"data engineer|ml engineer|firmware|embedded"
    r")(?!\w)",
    re.I,
)

INDIA = re.compile(
    r"india|gurugram|gurgaon|bengaluru|bangalore|hyderabad|pune|mumbai|"
    r"noida|delhi|chennai|kolkata|ahmedabad|jaipur|kochi|indore|remote\s*-\s*india",
    re.I,
)

WORLD = re.compile(
    r"worldwide|anywhere in the world|work from anywhere|globally|global remote",
    re.I,
)

OTHER_COUNTRY = re.compile(
    r"united states|\busa\b|\bu\.s\.?\b|canada|united kingdom|\buk\b|london|"
    r"europe|germany|france|poland|ireland|australia|singapore|brazil|israel|"
    r"san francisco|new york|seattle|austin|boston|dublin|amsterdam|"
    r"ukraine|mexico|japan|korea|china|netherlands|spain|italy|sweden|"
    r"philippines|pakistan|nigeria|kenya|south africa|uae|dubai",
    re.I,
)


def is_target_role(title: str) -> bool:
    if not title or not INCLUDE.search(title):
        return False
    if EXCLUDE.search(title):
        return False
    return True


def is_eligible_location(location: str) -> bool:
    loc = (location or "").strip()
    if not loc:
        return False
    if INDIA.search(loc):
        return True
    pure_remote = loc.lower() in {"remote", "anywhere", "worldwide", "global"}
    has_world = bool(WORLD.search(loc)) or pure_remote
    if has_world and not OTHER_COUNTRY.search(loc):
        return True
    return False


def score_job(title: str, location: str) -> int:
    score = 1
    if INDIA.search(location or ""):
        score += 10
    if re.search(r"senior|staff|principal|\blead\b", title, re.I):
        score += 5
    if re.search(r"backend|back[- ]end", title, re.I):
        score += 4
    if re.search(r"python|node|typescript|java", title, re.I):
        score += 3
    if re.search(r"full[- ]stack", title, re.I):
        score += 2
    return score


def company_is_skipped(company: str, skip_names: list[str]) -> bool:
    hay = (company or "").lower()
    return any(name.lower() in hay for name in skip_names if name)
