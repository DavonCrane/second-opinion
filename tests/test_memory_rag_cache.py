import pytest

from second_opinion import cache
from second_opinion.memory import EpisodicMemory, SemanticMemory, Workspace
from second_opinion.rag.index import FilingIndex, chunk_text
from second_opinion.tools import market


def test_cache_serves_fixture_offline_and_raises_on_miss():
    snap = market.snapshot("NVDA")            # from fixtures/snapshot__NVDA.json
    assert snap["ticker"] == "NVDA" and snap["price"] > 0
    assert market.resolve_ticker("nvidia") == "NVDA"
    assert market.resolve_ticker("msft") == "MSFT"   # short alnum -> upper-cased, no network
    with pytest.raises(cache.CacheMiss):
        market.snapshot("ZZZZ")


def test_cache_roundtrip_writes_runtime_cache():
    cache.write("unit", "k1", {"x": 1})
    assert cache.read("unit", "k1") == {"x": 1}
    assert cache.cached("unit", "k1", lambda: {"x": 2}) == {"x": 1}  # served from cache, fetch not called


def test_workspace_sources_and_findings():
    ws = Workspace(ticker="NVDA")
    a = ws.add_source("A")
    b = ws.add_source("B")
    assert ws.add_source("A") == a  # dedupe
    ws.add_finding("fundamentals", "fundamentals", "claim one", a, b)
    assert ws.cite(a, b) == "[1][2]"
    assert "[1][2]" in ws.findings_text("fundamentals")
    assert "[2] B" in ws.sources_text()


def test_episodic_memory_diff(tmp_path):
    ep = EpisodicMemory(tmp_path)
    assert ep.diff_summary("NVDA", 100.0, ["a"]) is None
    ep.record("NVDA", price=100.0, metrics={"pe": 40}, weights={"bear": .3, "base": .5, "bull": .2}, key_findings=["a", "b"], report_path="r.md")
    d = ep.diff_summary("NVDA", 109.0, ["b", "c"])
    assert d["price_change_pct"] == 9.0
    assert d["new_findings"] == ["c"]
    assert d["previous_weights"]["bear"] == 0.3


def test_semantic_memory_accumulates_risk_themes(tmp_path):
    sm = SemanticMemory(tmp_path)
    sm.update("NVDA", sector="Technology", risk_themes=["concentration"])
    sm.update("NVDA", risk_themes=["export controls", "concentration"])
    f = sm.get("NVDA")
    assert f["sector"] == "Technology" and f["risk_themes"] == ["concentration", "export controls"]
    assert "concentration" in sm.context_text("NVDA")


def test_rag_chunks_and_retrieves_with_citations():
    filing = {"form": "10-K", "filing_date": "2026-02-26", "period": "2026-01-26", "sections": {
        "Item 1A": "Customer concentration risk: three customers represent about 40% of revenue. " * 40
                   + "Export controls may reduce our addressable market. " * 40,
        "Item 7": "Revenue grew 62% to $211 billion. Free cash flow was $58.9 billion. " * 60}}
    assert len(chunk_text(filing["sections"]["Item 7"], "Item 7")) >= 2
    idx = FilingIndex("NVDA", backend="tfidf")
    n = idx.ingest(filing)
    assert n >= 3
    hits = idx.retrieve("customer concentration percentage of revenue", k=2)
    assert hits and hits[0]["item"] == "Item 1A"
    assert "10-K" in idx.citation_label(hits[0]) and "Item 1A" in idx.citation_label(hits[0])
