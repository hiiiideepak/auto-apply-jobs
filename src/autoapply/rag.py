"""Resume retrieval for the apply agent.

Chunks from the profile are embedded into a local vector index. The agent
must read those chunks before it writes a cover letter, so claims stay tied
to the resume.
"""

from __future__ import annotations

import hashlib
import math
import re

DIMS = 384


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9+#]{2,}", text.lower())


def embed(text: str) -> list[float]:
    vector = [0.0] * DIMS
    tokens = _tokens(text)
    grams = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
    for gram in grams:
        digest = hashlib.blake2s(gram.encode(), digest_size=4).digest()
        index = int.from_bytes(digest[:2], "big") % DIMS
        sign = 1.0 if digest[2] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right))


def _chunks_from_profile(profile: dict) -> list[dict]:
    chunks: list[dict] = []
    summary = (profile.get("summary") or "").strip()
    if summary:
        chunks.append({"title": "Summary", "text": summary})
    chunks.append(
        {
            "title": "Logistics",
            "text": (
                f"{profile['name']} is based in {profile['location_label']}. "
                f"Notice period {profile['notice_period_label']}. "
                f"Current CTC {profile['current_ctc_label']}. "
                f"Expected CTC {profile['expected_ctc_label']}. "
                f"{profile['work_authorization']} "
                f"Primary languages: {profile['primary_languages']}. "
                f"{profile['years_experience']}+ years of experience."
            ),
        }
    )
    for item in profile.get("knowledge") or []:
        title = (item.get("title") or "Resume").strip()
        text = (item.get("text") or "").strip()
        if text:
            chunks.append({"title": title, "text": text})
    employers = ", ".join(
        item.get("name", "") for item in profile.get("employers") or [] if item.get("name")
    )
    if employers:
        chunks.append({"title": "Employers", "text": f"Employers on record: {employers}."})
    return chunks


class ResumeIndex:
    def __init__(self, profile: dict):
        self.chunks = []
        for chunk in _chunks_from_profile(profile):
            self.chunks.append({**chunk, "vector": embed(f"{chunk['title']}\n{chunk['text']}")})

    def retrieve(self, query: str, limit: int = 3) -> list[dict]:
        if not query.strip() or not self.chunks:
            return []
        query_vector = embed(query)
        ranked = sorted(
            self.chunks,
            key=lambda chunk: _cosine(query_vector, chunk["vector"]),
            reverse=True,
        )
        return [
            {"title": chunk["title"], "text": chunk["text"].strip()}
            for chunk in ranked[:limit]
        ]
