"""Thin LLM client.

Two things matter here. First, the model is asked for strict JSON against a
schema we validate — an unparseable answer is a failure, not something we
paper over. Second, the whole system degrades to a deterministic policy
reasoner when no key is present, so the evaluation suite is reproducible in
CI and a reviewer can run the repo without credentials.
"""

from __future__ import annotations

import json
import os
import re

MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "2000"))


def is_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _extract_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object in model output: {text[:200]}")
    return json.loads(text[start : end + 1])


def complete_json(system: str, user: str, *, temperature: float = 0.0) -> dict:
    """Call the model and return parsed JSON. Raises on unparseable output."""
    if not is_available():
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    from anthropic import Anthropic

    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return _extract_json(text)
