"""Sentiment agent: Wall Street consensus (yfinance) + retail sentiment (Reddit posts classified by the FAST model).

Output is framed as an *input to cross-examine*, never a verdict: the writer looks for divergence between
sentiment and fundamentals (e.g. euphoric retail + decelerating growth -> bear-case material).
"""
from __future__ import annotations

import json

from ..memory.workspace import Workspace
from ..tools import reddit as reddit_tool
from .base import Agent


class SentimentAgent(Agent):
    name = "sentiment"
    section = "sentiment"

    def run(self, ws: Workspace) -> None:
        snap = ws.facts["snapshot"]
        an = snap.get("analyst") or {}
        price = snap.get("price")
        s_an = ws.add_source(f"Analyst recommendations & price targets via yfinance ({ws.created_at[:10]})")

        # ---- Wall Street consensus -> 0..10 bullishness score ----------------------------------
        counts = an.get("counts") or {}
        n = sum(counts.values()) if counts else (an.get("n_analysts") or 0)
        score = None
        if counts and n:
            # strongBuy=10, buy=7.5, hold=5, sell=2.5, strongSell=0
            score = round((counts.get("strongBuy", 0) * 10 + counts.get("buy", 0) * 7.5 + counts.get("hold", 0) * 5
                           + counts.get("sell", 0) * 2.5) / n, 1)
        elif an.get("recommendation_mean"):
            score = round((5 - an["recommendation_mean"]) / 4 * 10, 1)  # yfinance mean: 1 (strong buy) .. 5 (sell)
        upside = None
        if an.get("target_mean") and price:
            upside = round((an["target_mean"] - price) / price * 100, 1)
        analyst = {"score_0_10": score, "n_analysts": n or an.get("n_analysts"), "counts": counts or None,
                   "recommendation": an.get("recommendation"), "target_low": an.get("target_low"),
                   "target_mean": an.get("target_mean"), "target_high": an.get("target_high"), "target_upside_pct": upside}
        ws.facts["analyst"] = analyst
        if score is not None:
            ws.add_finding(self.name, self.section,
                           f"Wall Street consensus is {an.get('recommendation') or 'n/a'} ({score}/10 bullishness across "
                           f"{n or an.get('n_analysts') or '?'} analysts); mean 12-month target ${an.get('target_mean')} "
                           f"({upside:+.1f}% vs current price)" if upside is not None else
                           f"Wall Street consensus is {an.get('recommendation') or 'n/a'} ({score}/10 bullishness).", s_an)
        else:
            ws.add_finding(self.name, self.section, "No analyst consensus data available for this ticker.", s_an)
        ws.note(f"[sentiment] analyst consensus {score}/10, mean target {an.get('target_mean')}")

        # ---- Retail sentiment (Reddit) classified by FAST model -----------------------------
        posts = reddit_tool.recent_posts(ws.ticker)
        if not posts:
            ws.facts["retail"] = {"available": False, "reason": "Reddit not configured or unavailable"}
            ws.add_finding(self.name, self.section, "Retail (Reddit) sentiment was not available for this run; "
                           "sentiment view relies on analyst consensus only.", s_an)
            ws.note("[sentiment] reddit unavailable -> analyst-only fallback")
            return
        s_rd = ws.add_source(f"Reddit posts mentioning {ws.ticker} (r/stocks, r/investing, r/wallstreetbets, r/StockMarket), "
                             f"last 7 days, n={len(posts)}, classified bullish/bearish/neutral by the fast model")
        labels = self._classify(posts, ws.ticker)
        counts_r = {"bullish": 0, "bearish": 0, "neutral": 0}
        for lab in labels:
            counts_r[lab] = counts_r.get(lab, 0) + 1
        opinionated = counts_r["bullish"] + counts_r["bearish"]
        pct_bull = round(counts_r["bullish"] / opinionated * 100) if opinionated else None
        ws.facts["retail"] = {"available": True, "n_posts": len(posts), "counts": counts_r, "pct_bullish": pct_bull,
                              "confidence": "low" if len(posts) < 15 else "medium" if len(posts) < 40 else "ok"}
        if pct_bull is not None:
            ws.add_finding(self.name, self.section,
                           f"Retail sentiment on Reddit over the past week is {pct_bull}% bullish among opinionated posts "
                           f"(n={len(posts)} posts; {counts_r['bullish']} bullish / {counts_r['bearish']} bearish / "
                           f"{counts_r['neutral']} neutral). Sample size confidence: {ws.facts['retail']['confidence']}.", s_rd)
        ws.note(f"[sentiment] reddit {pct_bull}% bullish (n={len(posts)})")

    def _classify(self, posts: list[dict], ticker: str) -> list[str]:
        """Batch-classify with the FAST model; deterministic fallback to 'neutral' on any parse issue."""
        labels: list[str] = []
        batch = 20
        for i in range(0, len(posts), batch):
            chunk = posts[i:i + batch]
            numbered = "\n".join(f"{j+1}. {p['text'][:400].replace(chr(10), ' ')}" for j, p in enumerate(chunk))
            prompt = (f"Classify each Reddit post's stance on {ticker} stock as bullish, bearish, or neutral "
                      f"(neutral = no clear directional view, a question, or off-topic). Reply JSON "
                      f"{{\"labels\": [\"bullish\"|\"bearish\"|\"neutral\", ...]}} with exactly {len(chunk)} entries in order.\n\n{numbered}")
            try:
                out = self.llm.complete_json(prompt, tier="fast", max_tokens=400)
                got = [str(x).lower() for x in out.get("labels", [])]
                got = [g if g in ("bullish", "bearish", "neutral") else "neutral" for g in got]
                got = (got + ["neutral"] * len(chunk))[:len(chunk)]
            except Exception:  # noqa: BLE001
                got = ["neutral"] * len(chunk)
            labels.extend(got)
        return labels
