"""Thin wrapper around the Anthropic API.

Two tiers (a documented design decision):
  - STRONG (Sonnet): analysis, writing, critique — where judgment matters.
  - FAST   (Haiku):  routing, guardrail checks, sentiment classification — cheap, high-volume calls.

Also provides:
  - retries with backoff for transient API errors
  - a JSON helper that asks for and parses a JSON object (used for structured agent outputs)
  - a token/cost ledger so every run can report cost (an eval metric)
  - a FakeLLM for tests and offline demos so nothing here requires a key to import or unit-test
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .config import settings

# Approximate $/MTok (input, output) — used only for the cost ledger shown in reports/evals.
_PRICES = {
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    def add(self, model: str, inp: int, out: int) -> None:
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        pin, pout = _PRICES.get(model, (3.0, 15.0))
        self.cost_usd += inp / 1e6 * pin + out / 1e6 * pout
        m = self.by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0})
        m["calls"] += 1
        m["input"] += inp
        m["output"] += out


class LLM:
    """Real Anthropic client. Lazily imports the SDK so tests can run without it configured."""

    def __init__(self, api_key: str | None = None):
        import anthropic  # local import keeps module importable without the SDK in exotic envs

        self._client = anthropic.Anthropic(api_key=api_key or settings.anthropic_api_key)
        self.usage = Usage()

    def complete(self, prompt: str, *, system: str = "", tier: str = "strong",
                 max_tokens: int = 2000, retries: int = 3, **_ignored) -> str:
        # Note: `temperature` is deliberately NOT sent — Claude 5-generation models reject it (400 error).
        model = settings.model_strong if tier == "strong" else settings.model_fast
        last_err: Exception | None = None
        for attempt in range(retries):
            try:
                kwargs: dict[str, Any] = dict(model=model, max_tokens=max_tokens,
                                              messages=[{"role": "user", "content": prompt}])
                if system:
                    kwargs["system"] = system
                resp = self._client.messages.create(**kwargs)
                self.usage.add(model, resp.usage.input_tokens, resp.usage.output_tokens)
                return "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
            except Exception as e:  # noqa: BLE001 — retry on any transient API failure
                last_err = e
                if attempt < retries - 1:
                    time.sleep(1.5 * (2 ** attempt))
        raise RuntimeError(f"LLM call failed after {retries} attempts: {last_err}") from last_err

    def complete_json(self, prompt: str, **kw) -> dict[str, Any]:
        """Ask for a JSON object and parse it robustly. If the model's JSON is malformed (unescaped quotes,
        trailing commas, truncated), ask the FAST model to repair it once before giving up."""
        text = self.complete(prompt + "\n\nRespond with a single valid JSON object and nothing else. "
                             "Escape any double quotes inside strings.", **kw)
        try:
            return parse_json(text)
        except json.JSONDecodeError:
            fixed = self.complete(
                "The following was supposed to be a single valid JSON object but is malformed. Return the corrected "
                "JSON object only — same content, valid syntax (escape inner quotes, remove trailing commas, close "
                "any unclosed brackets):\n\n" + text[:12000], tier="fast", max_tokens=kw.get("max_tokens", 2000))
            return parse_json(fixed)


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # cheap repairs: trailing commas, control characters, smart quotes
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        repaired = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", repaired)
        repaired = repaired.replace("\u201c", '\\"').replace("\u201d", '\\"')
        return json.loads(repaired)


class FakeLLM:
    """Deterministic stand-in for tests/offline demos.

    Give it a list of canned responses (returned in order) or a callable(prompt, tier) -> str.
    Records every prompt so tests can assert on what agents asked.
    """

    def __init__(self, responses: list[str] | None = None, fn=None):
        self._responses = list(responses or [])
        self._fn = fn
        self.prompts: list[tuple[str, str]] = []
        self.usage = Usage()

    def complete(self, prompt: str, *, system: str = "", tier: str = "strong", **kw) -> str:
        self.prompts.append((tier, prompt))
        self.usage.add(settings.model_strong if tier == "strong" else settings.model_fast, len(prompt) // 4, 100)
        if self._fn:
            return self._fn(prompt, tier)
        if self._responses:
            return self._responses.pop(0)
        return "{}"

    def complete_json(self, prompt: str, **kw) -> dict[str, Any]:
        return parse_json(self.complete(prompt, **kw))


def get_llm() -> LLM | FakeLLM:
    if settings.llm_configured:
        return LLM()
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key "
        "(or pass an explicit llm= to the orchestrator, e.g. FakeLLM for tests)."
    )
