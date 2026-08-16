"""News agent: recent developments that matter — deduped, dated, relevance-filtered."""
from __future__ import annotations

from .. import cache
from ..memory.workspace import Workspace
from ..tools import news as news_tool
from .base import Agent, ANALYST_SYSTEM


class NewsAgent(Agent):
    name = "news"
    section = "news"

    def run(self, ws: Workspace) -> None:
        try:
            items = news_tool.recent_news(ws.ticker)
        except cache.CacheMiss:
            items = []
            ws.errors.append("news: no headlines in cache/fixtures (offline)")
        except Exception as e:  # noqa: BLE001
            items = []
            ws.errors.append(f"news: provider failed: {e}")
        ws.note(f"[news] {len(items)} headlines retrieved")
        if not items:
            ws.add_finding(self.name, self.section, "No recent news headlines were available for this run.",
                           ws.add_source("News feed unavailable at run time"))
            return
        lines = []
        for it in items[:20]:
            sid = ws.add_source(f"{it.get('publisher') or 'News'}: \"{it.get('title')}\" ({str(it.get('published') or '')[:10]})", it.get("link"))
            lines.append(f"[source {sid}] {str(it.get('published') or '')[:10]} — {it.get('title')} :: {(it.get('summary') or '')[:300]}")
        prompt = f"""Ticker: {ws.ticker} ({ws.facts['snapshot'].get('name')}).
Recent headlines (each tagged with a source id):
{chr(10).join(lines)}

Select the 3-5 developments that a fundamentals-driven investor should actually know about (earnings, guidance, product/launch timing, major customer/competitor moves, regulatory/legal, capital allocation). Ignore clickbait, price-move-only stories, and duplicates. For each, write one factual sentence with the date and cite its source id. Also flag anything that materially strengthens the bull case or the bear case.
JSON: {{"findings": [{{"claim": "...", "sources": [id]}}], "bull_support": ["..."], "bear_support": ["..."]}}"""
        out = self.llm.complete_json(prompt, system=ANALYST_SYSTEM, tier="strong", max_tokens=1200)
        n = self._add_claims(ws, out.get("findings", []), [ws.sources[-1]["id"]])
        ws.facts["news_flags"] = {"bull": out.get("bull_support", []), "bear": out.get("bear_support", [])}
        ws.note(f"[news] {n} relevant developments after filtering")
