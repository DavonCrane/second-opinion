"""Episodic memory: what happened in past runs, per ticker.

Stored as JSON under data/memory/episodic/<TICKER>.json. Each episode keeps the price, key metrics,
scenario weights, headline findings, and the report path — enough to render "since last analysis"
and to let the eval check that a re-run recalls prior context.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

from ..config import settings


class EpisodicMemory:
    def __init__(self, root: Path | None = None):
        self.root = (root or settings.data_dir / "memory" / "episodic")
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, ticker: str) -> Path:
        return self.root / f"{ticker.upper()}.json"

    def episodes(self, ticker: str) -> list[dict[str, Any]]:
        p = self._path(ticker)
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []

    def last(self, ticker: str) -> dict[str, Any] | None:
        eps = self.episodes(ticker)
        return eps[-1] if eps else None

    def record(self, ticker: str, *, price: float | None, metrics: dict[str, Any], weights: dict[str, float] | None,
               key_findings: list[str], report_path: str | None, mode: str = "full_report") -> dict[str, Any]:
        ep = {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "mode": mode,
            "price": price,
            "metrics": metrics,
            "weights": weights,
            "key_findings": key_findings[:8],
            "report_path": report_path,
        }
        eps = self.episodes(ticker)
        eps.append(ep)
        self._path(ticker).write_text(json.dumps(eps, indent=1, default=str), encoding="utf-8")
        return ep

    def diff_summary(self, ticker: str, current_price: float | None, current_findings: list[str]) -> dict[str, Any] | None:
        """Structured 'since last analysis' input for the writer. None if this is the first run."""
        prev = self.last(ticker)
        if not prev:
            return None
        price_change = None
        if prev.get("price") and current_price:
            price_change = round((current_price - prev["price"]) / prev["price"] * 100, 1)
        prev_set = set(prev.get("key_findings", []))
        return {
            "previous_ts": prev["ts"],
            "previous_price": prev.get("price"),
            "price_change_pct": price_change,
            "previous_weights": prev.get("weights"),
            "previous_findings": prev.get("key_findings", []),
            "new_findings": [f for f in current_findings if f not in prev_set][:6],
        }
