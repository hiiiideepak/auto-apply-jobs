"""Anthropic Messages API with tool use."""

from __future__ import annotations

import os

import httpx

DEFAULT_MODEL = "claude-sonnet-5"
API_URL = "https://api.anthropic.com/v1/messages"


def llm_configured() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


class AnthropicLLM:
    def __init__(self, model: str | None = None, api_key: str | None = None):
        self.model = model or os.environ.get("LLM_MODEL") or DEFAULT_MODEL
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY") or ""

    def complete(self, *, system: str, messages: list[dict], tools: list[dict]) -> dict:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set")
        response = httpx.post(
            API_URL,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": 1400,
                "system": system,
                "tools": tools,
                "messages": messages,
            },
            timeout=90,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"LLM API {response.status_code}: {response.text[:400]}")
        return response.json()
