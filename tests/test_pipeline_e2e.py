"""End-to-end offline run of the whole pipeline with fixtures + a scripted FakeLLM.

This is the test that proves the system composes: routing -> parallel analysts -> valuation -> writer ->
critic (reject, revise, approve) -> guardrails -> memory persistence -> focused follow-up.
"""
import re

from second_opinion.orchestrator import Orchestrator
from second_opinion.agents.writer import SECTIONS


def _orch(llm, memories, **kw):
    ep, sm = memories
    return Orchestrator(llm=llm, episodic=ep, semantic=sm, **kw)


def test_full_report_end_to_end(llm, memories):
    ws = _orch(llm, memories).run("NVDA")
    md = ws.report_md
    # all template sections present (eval task-success criterion)
    for sec in SECTIONS:
        assert f"## {sec}" in md, f"missing section {sec}"
    # numbers came from the calculator, not the model
    assert "+61.7%" in md                    # revenue growth from statements
    assert "Rule of 40" in md and "**119**" in md or "**120**" in md
    # scenario table + Claude's view + analyst target
    assert "| **Bear** |" in md and "Claude's 12-mo view" in md and "Analyst mean target: $145" in md
    sc = ws.facts["scenarios"]
    assert sc["weights"] == {"bear": 0.3, "base": 0.5, "bull": 0.2} and sc["tilt"] == 0.05
    # sentiment: analyst score computed + reddit classified
    assert ws.facts["analyst"]["score_0_10"] == 7.9
    assert ws.facts["retail"]["available"] and ws.facts["retail"]["n_posts"] >= 64 and "reddit" in ws.facts["retail"]["sources"]
    # critic rejected the vague bear bullet then approved the revision (reflection loop)
    verdicts = [c["verdict"] for c in ws.critiques]
    assert verdicts == ["REJECT", "APPROVE"]
    assert len(ws.drafts) == 2 and "the stock is expensive" not in ws.drafts[-1]
    # guardrails
    og = ws.facts["output_guard"]
    assert og["ok"], og
    assert og["coverage"] >= 0.9 and og["has_disclaimer"] and not og["bad_refs"]
    # every citation maps to a real source
    assert max(int(n) for n in re.findall(r"\[(\d+)\]", md)) <= len(ws.sources)
    # persistence: report on disk + memories written
    assert ws.facts["report_path"].endswith(".md")
    ep, sm = memories
    assert ep.last("NVDA")["weights"]["bear"] == 0.3
    assert "concentration" in " ".join(sm.get("NVDA")["risk_themes"])
    assert ws.facts["usage"]["calls"] > 5


def test_rerun_produces_since_last_analysis_diff(llm, memories):
    o = _orch(llm, memories)
    o.run("NVDA")
    ws2 = o.run("nvidia")             # company name resolves via fixture
    assert ws2.ticker == "NVDA"
    d = ws2.facts["episodic_diff"]
    assert d is not None and d["previous_weights"]["bear"] == 0.3


def test_ablation_without_critic(llm, memories):
    ws = _orch(llm, memories, critic_enabled=False).run("NVDA")
    assert ws.critiques == [] and len(ws.drafts) == 1
    assert "the stock is expensive" in ws.report_md          # the weak bear bullet ships un-fixed
    assert any("critic DISABLED" in line for line in ws.log)


def test_focused_question_is_routed_and_cited(llm, memories):
    ws = _orch(llm, memories).run("NVDA — what's their debt situation?")
    assert ws.mode == "focused_question"
    assert "$9.7B" in ws.report_md and "[1]" in ws.report_md
    assert ws.facts["output_guard"]["ok"]
    assert ws.facts["usage"]["calls"] <= 3        # cheap path: no full pipeline


def test_advice_seeking_is_refused(llm, memories):
    ws = _orch(llm, memories).run("Should I buy NVDA?")
    assert ws.mode == "refused" and "not an adviser" in ws.report_md


def test_graceful_degradation_when_a_tool_fails(llm, memories, monkeypatch):
    from second_opinion.tools import news as news_tool
    monkeypatch.setattr(news_tool, "recent_news", lambda t, **k: (_ for _ in ()).throw(RuntimeError("provider down")))
    ws = _orch(llm, memories).run("NVDA")
    assert any("news" in e for e in ws.errors)
    assert "## Recent developments" in ws.report_md          # report still ships
    assert ws.facts["output_guard"]["has_disclaimer"]


def test_unknown_ticker_fails_cleanly(llm, memories):
    import pytest
    from second_opinion import cache
    with pytest.raises(cache.CacheMiss):
        _orch(llm, memories).run("ZZZZ")
