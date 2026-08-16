"""Shared agent plumbing."""
from __future__ import annotations

from typing import Any

from ..memory.workspace import Workspace

ANALYST_SYSTEM = (
    "You are a specialist analyst on an equity research desk called The Second Opinion. You are patient, "
    "sceptical, and evidence-first. You never invent numbers. Every claim you make must be traceable to the "
    "evidence you are given, and you tag each claim with the source ids provided. You write for a careful "
    "individual investor, not for hype. You never give buy/sell advice."
)


class Agent:
    name: str = "agent"
    section: str = "general"

    def __init__(self, llm):
        self.llm = llm

    def run(self, ws: Workspace) -> None:  # pragma: no cover — interface
        raise NotImplementedError

    # helpers -------------------------------------------------------------------
    def _add_claims(self, ws: Workspace, claims: list[dict[str, Any]], default_sources: list[int]) -> int:
        """claims: [{"claim": str, "sources": [ids]}]. Drops empties and claims that cite unknown ids."""
        valid = {s["id"] for s in ws.sources}
        n = 0
        for c in claims:
            text = str(c.get("claim", "")).strip()
            ids = [int(i) for i in (c.get("sources") or []) if str(i).isdigit() and int(i) in valid] or default_sources
            if text and ids:
                ws.add_finding(self.name, self.section, text, *ids)
                n += 1
        return n

    @staticmethod
    def _fmt_money(v: float | None) -> str:
        if v is None:
            return "n/a"
        a = abs(v)
        for unit, div in (("T", 1e12), ("B", 1e9), ("M", 1e6)):
            if a >= div:
                return f"{'-' if v < 0 else ''}${a / div:.1f}{unit}"
        return f"${v:,.0f}"

    @staticmethod
    def _fmt_pct(v: float | None, signed: bool = True) -> str:
        if v is None:
            return "n/a"
        return f"{v:+.1f}%" if signed else f"{v:.1f}%"
