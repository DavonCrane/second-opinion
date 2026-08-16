from second_opinion import guardrails as g
from second_opinion.router import route
from second_opinion.llm import FakeLLM, parse_json


def test_input_guard_refuses_advice_and_allows_research():
    assert g.input_guard("Should I buy NVDA?").ok is False
    assert g.input_guard("is NVDA a good buy right now").ok is False
    assert g.input_guard("NVDA").ok is True
    assert g.input_guard("NVDA — what are the biggest risks in the 10-K?").ok is True


def test_input_guard_uses_llm_for_borderline():
    llm = FakeLLM(responses=['{"advice_seeking": true}'])
    assert g.input_guard("what would you do with a big NVDA position", llm).ok is False
    llm2 = FakeLLM(responses=['{"advice_seeking": false}'])
    assert g.input_guard("what does the filing say about their debt covenants", llm2).ok is True


def test_output_guard_citation_coverage_and_disclaimer():
    good = "# X\n\nRevenue grew 62% to $211B [1]. Margins were 74% [1].\n\n" + g.DISCLAIMER
    r = g.output_guard(good, n_sources=1)
    assert r.ok and r.details["coverage"] == 1.0
    bad = "# X\n\nRevenue grew 62% to $211B. Margins were 74% [7].\n"
    r2 = g.output_guard(bad, n_sources=1)
    assert not r2.ok
    assert r2.details["bad_refs"] == [7]
    assert not r2.details["has_disclaimer"]
    assert "not investment advice" in g.ensure_disclaimer("hello").lower()


def test_output_guard_flags_advice_language():
    md = "You should buy this stock now [1].\n" + g.DISCLAIMER
    r = g.output_guard(md, n_sources=1)
    assert not r.ok and r.details["forbidden"]


def test_weight_guard():
    assert g.weight_guard({"bear": 0.3, "base": 0.5, "bull": 0.2}, critic_signed_off=False).ok
    assert not g.weight_guard({"bear": 0.6, "base": 0.3, "bull": 0.1}, critic_signed_off=False).ok
    assert g.weight_guard({"bear": 0.6, "base": 0.3, "bull": 0.1}, critic_signed_off=True).ok


def test_router_heuristics():
    r = route("NVDA")
    assert r.mode == "full_report" and r.subject == "NVDA"
    r = route("research nvidia")
    assert r.mode == "full_report" and r.subject == "nvidia"
    r = route("NVDA — what's their debt situation?")
    assert r.mode == "focused_question" and r.subject == "NVDA" and "debt" in r.question
    r = route("MSFT: how fast is revenue growing")
    assert r.mode == "focused_question" and r.subject == "MSFT"


def test_router_llm_fallback_and_json_parsing():
    llm = FakeLLM(responses=['```json\n{"mode": "focused_question", "subject": "AAPL", "question": "margins?"}\n```'])
    r = route("Can you tell me how Apple's margins have been trending lately", llm)
    assert r.mode == "focused_question" and r.subject == "AAPL" and r.via == "llm"
    assert parse_json('Sure! {"a": 1} thanks') == {"a": 1}
