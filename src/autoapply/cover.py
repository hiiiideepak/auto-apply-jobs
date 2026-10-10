"""Short cover letters filled from the profile. No model API required."""

from __future__ import annotations


def cover_letter(profile: dict, company: str, title: str) -> str:
    return (
        f"Hello {company} team,\n\n"
        f"I am applying for the {title} role. I am a software engineer with "
        f"{profile['years_experience']}+ years building backend systems, APIs, and "
        f"production data pipelines in TypeScript/Node.js and Python. I currently "
        f"work at {profile['current_company']} on tax integrations, and before that "
        f"I was a senior engineer at Payoneer, where I led a team of 4-5 engineers.\n\n"
        f"I am based in {profile['location_label']}, authorized to work in India, "
        f"and available after a {profile['notice_period_label']} notice period. "
        f"I am looking for backend, full-stack, staff, and tech-lead roles.\n\n"
        f"GitHub: {profile['github']}\n"
        f"LinkedIn: {profile['linkedin']}\n"
        f"Portfolio: {profile['portfolio']}\n\n"
        f"Thank you,\n{profile['name']}\n{profile['phone']}\n{profile['email']}\n"
    )
