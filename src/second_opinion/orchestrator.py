"""Orchestrator: deterministic control flow over the agents.

Design decision: a fixed, code-driven workflow rather than an LLM planner (ReAct). The research process has a
known shape, so a deterministic orchestrator is more reliable, cheaper, and testable — and every step is
observable in the workspace log.

    input guard -> route -> resolve ticker -> load memory
      full_report:      snapshot -> [fundamentals | news | sentiment] in parallel -> valuation
                        -> writer -> critic (<= N rounds) -> output guard -> save report + memory
      focused_question: snapshot -> answer from semantic memory + RAG + tools -> cited answer
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
from pathlib import Path
from typing import Any, Callable

from . import guardrails
from .agents import (CriticAgent, FundamentalsAgent, NewsAgent, SentimentAgent, ValuationAgent, WriterAgent)
from .config import settings
from .llm import get_llm
from .memory import EpisodicMemory, SemanticMemory, Workspace
from .rag import FilingIndex
from .router import route
from .tools import edgar, market
from . import cache

ProgressFn = Callable[[str], None]


class Orchestrator:
    def __init__(self, llm=None, *, critic_enabled: bool | None = None, max_critic_rounds: int | None = None,
                 episodic: EpisodicMemory | None = None, semantic: SemanticMemory | None = None,
                 on_progress: ProgressFn | None = None, parallel: bool = True):
        self.llm = llm or get_llm()
        self.critic_enabled = settings.critic_enabled if critic_enabled is None else critic_enabled
        self.max_rounds = settings.max_critic_rounds if max_critic_rounds is None else max_critic_rounds
        self.episodic = episodic or EpisodicMemory()
        self.semantic = semantic or SemanticMemory()
        self.on_progress = on_progress or (lambda msg: None)
        self.parallel = parallel

    # ------------------------------------------------------------------------------
    def run(self, query: str) -> Workspace:
        g = guardrails.input_guard(query, self.llm)
        if not g.ok:
            ws = Workspace(ticker="", query=query, mode="refused")
            ws.report_md = g.details["response"]
            ws.note("[guardrails] request refused: " + g.reason)
            self.on_progress(ws.log[-1])
            return ws
        r = route(query, self.llm)
        ticker = market.resolve_ticker(r.subject)
        ws = Workspace(ticker=ticker, query=query, mode=r.mode)
        self._note(ws, f"[orchestrator] request classified: {r.mode.upper()} ({r.via}) · ticker {ticker}")
        ws.facts["snapshot"] = market.snapshot(ticker)
        ws.facts["semantic_context"] = self.semantic.context_text(ticker)
        if r.mode == "focused_question":
            return self._focused(ws, r.question or query)
        return self._full_report(ws)

    # ------------------------------------------------------------------------------
    def _full_report(self, ws: Workspace) -> Workspace:
        agents = [FundamentalsAgent(self.llm), NewsAgent(self.llm), SentimentAgent(self.llm)]
        self._note(ws, f"[orchestrator] fanning out {len(agents)} analysts " + ("in parallel" if self.parallel else "sequentially"))
        if self.parallel:
            with cf.ThreadPoolExecutor(max_workers=len(agents)) as ex:
                futs = {ex.submit(self._safe_run, a, ws): a for a in agents}
                for f in cf.as_completed(futs):
                    f.result()
        else:
            for a in agents:
                self._safe_run(a, ws)
        for line in ws.log[-6:]:
            self.on_progress(line)
        self._safe_run(ValuationAgent(self.llm), ws)  # needs the others' evidence for weights
        self.on_progress(ws.log[-1])

        # memory: since-last-analysis diff feeds the writer
        key_findings = [f.claim for f in ws.findings if f.section in ("fundamentals", "news")][:8]
        ws.facts["episodic_diff"] = self.episodic.diff_summary(ws.ticker, ws.facts["snapshot"].get("price"), key_findings)

        writer, critic = WriterAgent(self.llm), CriticAgent(self.llm)
        draft = writer.run(ws)
        self._note(ws, "[writer] draft 1 assembled")
        critique = None
        if self.critic_enabled:
            for rnd in range(1, self.max_rounds + 1):
                critique = critic.run(ws, draft)
                self.on_progress(ws.log[-1])
                if critique["verdict"] == "APPROVE":
                    break
                if rnd < self.max_rounds:
                    draft = writer.run(ws, critique)
                    self._note(ws, f"[writer] revision {rnd} assembled")
                else:
                    self._note(ws, "[orchestrator] critic still objecting at max rounds — shipping last revision with issues noted")
        else:
            self._note(ws, "[orchestrator] critic DISABLED (ablation mode)")

        # final guardrails
        draft = guardrails.ensure_disclaimer(draft)
        og = guardrails.output_guard(draft, len(ws.sources))
        ws.facts["output_guard"] = og.details | {"ok": og.ok, "reason": og.reason}
        self._note(ws, f"[guardrails] citation coverage {og.details['coverage']:.0%} · disclaimer {'ok' if og.details['has_disclaimer'] else 'MISSING'}"
                   + ("" if og.ok else f" · WARNING {og.reason}"))
        ws.report_md = draft
        ws.facts["usage"] = self._usage()
        self._persist(ws, key_findings)
        self._note(ws, f"[orchestrator] done · {ws.facts['usage']['calls']} LLM calls · est. ${ws.facts['usage']['cost_usd']:.3f}")
        return ws

    # ------------------------------------------------------------------------------
    def _focused(self, ws: Workspace, question: str) -> Workspace:
        """Answer one question with cited evidence, without the full pipeline."""
        snap = ws.facts["snapshot"]
        s_fin = ws.add_source(f"{ws.ticker} financial statements & profile via yfinance ({ws.created_at[:10]})")
        passages = []
        try:
            filing = edgar.latest_10k_sections(ws.ticker)
            idx = FilingIndex(ws.ticker)
            idx.ingest(filing)
            for ch in idx.retrieve(question, k=5):
                sid = ws.add_source(idx.citation_label(ch))
                passages.append(f"[source {sid}] ({ch['item']}) {ch['text'][:1000]}")
            self._note(ws, f"[rag] {len(passages)} passages retrieved for the question")
        except (cache.CacheMiss, Exception) as e:  # noqa: BLE001
            ws.errors.append(f"focused: 10-K unavailable ({type(e).__name__})")
        prompt = f"""Question about {snap.get('name')} ({ws.ticker}): {question}

{ws.facts.get('semantic_context') or ''}
Profile & key numbers [source {s_fin}]: price ${snap.get('price')}, trailing P/E {snap.get('trailing_pe')}, revenue growth (TTM) {snap.get('revenue_growth_ttm')}, gross margin {snap.get('gross_margin_ttm')}, operating margin {snap.get('operating_margin_ttm')}, total debt {snap.get('total_debt')}, cash {snap.get('total_cash')}, debt/equity(%) {snap.get('debt_to_equity')}, FCF {snap.get('free_cash_flow_ttm')}. Summary: {snap.get('summary')}
10-K passages:
{chr(10).join(passages) if passages else '(none available)'}

Answer in <=180 words. Every factual sentence ends with [n] citations to the sources above. If the evidence doesn't answer the question, say what's missing. No advice."""
        answer = self.llm.complete(prompt, system="You are a careful equity research analyst. Cite every fact. Never give buy/sell advice.", tier="strong", max_tokens=500)
        ws.report_md = f"**{ws.ticker} — {question}**\n\n{answer}\n\n**Sources**\n{ws.sources_text()}\n\n{guardrails.DISCLAIMER}\n"
        og = guardrails.output_guard(ws.report_md, len(ws.sources), min_coverage=0.8)
        ws.facts["output_guard"] = og.details | {"ok": og.ok}
        ws.facts["usage"] = self._usage()
        self.episodic.record(ws.ticker, price=snap.get("price"), metrics={}, weights=None, key_findings=[question],
                             report_path=None, mode="focused_question")
        self._note(ws, f"[orchestrator] focused answer ready · coverage {og.details['coverage']:.0%}")
        return ws

    # ------------------------------------------------------------------------------
    def _safe_run(self, agent, ws: Workspace) -> None:
        try:
            agent.run(ws)
        except Exception as e:  # noqa: BLE001 — one agent failing must not kill the run
            ws.errors.append(f"{agent.name}: {type(e).__name__}: {e}")
            self._note(ws, f"[{agent.name}] FAILED: {type(e).__name__}: {e}")

    def _note(self, ws: Workspace, msg: str) -> None:
        ws.note(msg)
        self.on_progress(ws.log[-1])

    def _usage(self) -> dict[str, Any]:
        u = getattr(self.llm, "usage", None)
        return {"calls": u.calls, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                "cost_usd": round(u.cost_usd, 4), "by_model": u.by_model} if u else {}

    def _persist(self, ws: Workspace, key_findings: list[str]) -> None:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
        path = settings.reports_dir / f"{ws.ticker}_{stamp}.md"
        path.write_text(ws.report_md, encoding="utf-8")
        ws.facts["report_path"] = str(path)
        m = ws.facts.get("metrics", {})
        sc = ws.facts.get("scenarios") or {}
        self.episodic.record(ws.ticker, price=ws.facts["snapshot"].get("price"),
                             metrics={k: m.get(k) for k in ("revenue_growth_yoy_pct", "operating_margin_pct", "trailing_pe", "net_cash")},
                             weights=sc.get("weights"), key_findings=key_findings, report_path=str(path))
        su = ws.facts.get("semantic_update") or {}
        snap = ws.facts["snapshot"]
        self.semantic.update(ws.ticker, name=snap.get("name"), sector=snap.get("sector"), industry=snap.get("industry"),
                             business_model=su.get("business_model"), moat=su.get("moat"), risk_themes=su.get("risk_themes"),
                             rule_of_40_applies=m.get("rule_of_40_applies"))
        ws.facts.pop("_filing_index", None)
