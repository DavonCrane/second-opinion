"""Semantic memory: durable facts about a company that survive across runs.

Not a vector store (that's the RAG index) — this is a small key/value store of stable facts the system
has learned: sector, business model summary, identified moat, recurring risk themes, whether Rule of 40
applies. It's read at the start of a run (so agents don't re-derive basics) and updated at the end.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..config import settings


class SemanticMemory:
    def __init__(self, root: Path | None = None):
        self.root = (root or settings.data_dir / "memory" / "semantic")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ticker: str) -> Path:
        return self.root / f"{ticker.upper()}.json"

    def get(self, ticker: str) -> dict[str, Any]:
        p = self._path(ticker)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def update(self, ticker: str, **facts: Any) -> dict[str, Any]:
        cur = self.get(ticker)
        for k, v in facts.items():
            if v in (None, "", [], {}):
                continue
            if k == "risk_themes":  # accumulate, dedupe, cap
                merged = list(dict.fromkeys((cur.get(k) or []) + list(v)))
                cur[k] = merged[:12]
            else:
                cur[k] = v
        cur["updated_at"] = dt.datetime.now().isoformat(timespec="seconds")
        self._path(ticker).write_text(json.dumps(cur, indent=1, default=str), encoding="utf-8")
        return cur

    def context_text(self, ticker: str) -> str:
        f = self.get(ticker)
        if not f:
            return ""
        lines = [f"Known facts about {ticker.upper()} from prior research (semantic memory, updated {f.get('updated_at','?')}):"]
        for k in ("name", "sector", "industry", "business_model", "moat", "rule_of_40_applies"):
            if k in f:
                lines.append(f"- {k}: {f[k]}")
        if f.get("risk_themes"):
            lines.append("- recurring risk themes: " + "; ".join(f["risk_themes"]))
        return "\n".join(lines)
