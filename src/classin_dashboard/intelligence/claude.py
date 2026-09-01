"""Thin Claude helper: JSON-mode calls for parsing and copywriting."""

from __future__ import annotations

import json
import logging
from typing import Any

import anthropic

from ..config import Settings

log = logging.getLogger(__name__)


def run_json(settings: Settings, *, system: str, user: str, max_tokens: int = 4096) -> Any:
    if not settings.anthropic_api_key:
        raise RuntimeError("Anthropic API key가 설정되지 않았습니다 (DASH_ANTHROPIC_API_KEY).")
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    resp = client.messages.create(
        model=settings.anthropic_model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in resp.content if block.type == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.error("non-JSON model output: %s", text[:500])
        raise
