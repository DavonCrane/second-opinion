"""Working memory: the shared workspace (blackboard) for one research run.

Agents append Findings (a claim + its source). The writer reads them all; the critic checks them;
the guardrails count citations against them. Sources are numbered as they're registered so the report
can render [1], [2] ... consistently.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class Finding:
    agent: str
    section: str          # e.g. "fundamentals", "news", "sentiment", "valuation"
    claim: str
    source_ids: list[int]  # indexes into Workspace.sources
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Workspace:
    ticker: str
    query: str = ""
    mode: str = "full_report"          # or "focused_question"
    created_at: str = field(default_factory=lambda: dt.datetime.now().isoformat(timespec="seconds"))
    sources: list[dict[str, Any]] = field(default_factory=list)   # {"id", "label", "url"?}
    findings: list[Finding] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)             # structured numbers (snapshot, metrics, scenarios)
    log: list[str] = field(default_factory=list)                    # human-readable trace for the UI/CLI
    drafts: list[str] = field(default_factory=list)                 # writer drafts (one per critic round)
    critiques: list[dict[str, Any]] = field(default_factory=list)   # critic verdicts per round
    report_md: str = ""
    errors: list[str] = field(default_factory=list)                 # graceful-degradation notes

    # -- sources -----------------------------------------------------------------
    def add_source(self, label: str, url: str | None = None) -> int:
        for s in self.sources:
            if s["label"] == label:
                return s["id"]
        sid = len(self.sources) + 1
        self.sources.append({"id": sid, "label": label, "url": url})
        return sid

    def cite(self, *ids: int) -> str:
        return "".join(f"[{i}]" for i in ids)

    # -- findings ----------------------------------------------------------------
    def add_finding(self, agent: str, section: str, claim: str, *source_ids: int, **data: Any) -> Finding:
        f = Finding(agent=agent, section=section, claim=claim, source_ids=list(source_ids), data=data)
        self.findings.append(f)
        return f

    def findings_for(self, section: str) -> list[Finding]:
        return [f for f in self.findings if f.section == section]

    def findings_text(self, section: str | None = None) -> str:
        items = self.findings if section is None else self.findings_for(section)
        return "\n".join(f"- ({f.agent}) {f.claim} {self.cite(*f.source_ids)}" for f in items)

    def sources_text(self) -> str:
        return "\n".join(f"[{s['id']}] {s['label']}" + (f" — {s['url']}" if s.get("url") else "") for s in self.sources)

    # -- trace -------------------------------------------------------------------
    def note(self, msg: str) -> None:
        self.log.append(f"{dt.datetime.now().strftime('%H:%M:%S')} {msg}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d
