"""Sentiment agent: Wall Street consensus (yfinance) + retail sentiment from StockTwits (and Reddit if configured).

Retail posts are classified bullish / bearish / neutral. StockTwits messages often carry a user-declared label, which
we keep as-is; everything unlabeled (and all Reddit posts) is classified by the FAST model in batches.

Output is framed as an *input to cross-examine*, never a verdict: the writer looks for divergence between
sentiment and fundamentals (e.g. euphoric retail + decelerating growth -> bear-case material).
"""
from __future__ import annotations

from ..config import settings
from ..memory.workspace import Workspace
from ..tools import reddit as reddit_tool, stocktwits as st_tool
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
            score = round((counts.get("strongBuy", 0) * 10 + counts.get("buy", 0) * 7.5 + counts.get("hold", 0) * 5
                           + counts.get("sell", 0) * 2.5) / n, 1)
        elif an.get("recommendation_mean"):
            score = round((5 - an["recommendation_mean"]) / 4 * 10, 1)
        upside = None
        if an.get("target_mean") and price:
            upside = round((an["target_mean"] - price) / price * 100, 1)
        rnd = lambda v: round(v, 2) if isinstance(v, (int, float)) else v
        analyst = {"score_0_10": score, "n_analysts": n or an.get("n_analysts"), "counts": counts or None,
                   "recommendation": an.get("recommendation"), "target_low": rnd(an.get("target_low")),
                   "target_mean": rnd(an.get("target_mean")), "target_high": rnd(an.get("target_high")), "target_upside_pct": upside}
        ws.facts["analyst"] = analyst
        if score is not None:
            ws.add_finding(self.name, self.section,
                           f"Wall Street consensus is {an.get('recommendation') or 'n/a'} ({score}/10 bullishness across "
                           f"{n or an.get('n_analysts') or '?'} analysts); mean 12-month target ${rnd(an.get('target_mean'))} "
                           f"({upside:+.1f}% vs current price)" if upside is not None else
                           f"Wall Street consensus is {an.get('recommendation') or 'n/a'} ({score}/10 bullishness).", s_an)
        else:
            ws.add_finding(self.name, self.section, "No analyst consensus data available for this ticker.", s_an)
        ws.note(f"[sentiment] analyst consensus {score}/10, mean target {an.get('target_mean')}")

        # ---- Retail sentiment: StockTwits (always tried) + Reddit (if configured) -------------------
        sources: dict[str, dict] = {}

        st_msgs = st_tool.recent_messages(ws.ticker)
        if st_msgs:
            declared = [m for m in st_msgs if m.get("label")]
            unlabeled = [m for m in st_msgs if not m.get("label")]
            labels = [m["label"] for m in declared] + self._classify(unlabeled, ws.ticker)
            sources["stocktwits"] = self._tally(labels, len(st_msgs), extra={"declared_share": round(len(declared) / len(st_msgs), 2)})
            sid = ws.add_source(f"StockTwits $"+ws.ticker+f" stream, last 7 days, n={len(st_msgs)} messages "
                                f"({len(declared)} user-labelled Bullish/Bearish; the rest classified by the fast model)")
            sources["stocktwits"]["source_id"] = sid
            ws.note(f"[sentiment] stocktwits {sources['stocktwits']['pct_bullish']}% bullish (n={len(st_msgs)}, {len(declared)} self-labelled)")

        rd_posts = reddit_tool.recent_posts(ws.ticker) if (settings.reddit_configured or settings.offline) else None
        if rd_posts:
            labels = self._classify(rd_posts, ws.ticker)
            sources["reddit"] = self._tally(labels, len(rd_posts))
            sid = ws.add_source(f"Reddit posts mentioning {ws.ticker} (r/stocks, r/investing, r/wallstreetbets, r/StockMarket), "
                                f"last 7 days, n={len(rd_posts)}, classified bullish/bearish/neutral by the fast model")
            sources["reddit"]["source_id"] = sid
            ws.note(f"[sentiment] reddit {sources['reddit']['pct_bullish']}% bullish (n={len(rd_posts)})")

        if not sources:
            reason = "no retail posts available (StockTwits unreachable" + ("; Reddit not configured)" if not settings.reddit_configured else "; Reddit empty)")
            ws.facts["retail"] = {"available": False, "reason": reason}
            ws.add_finding(self.name, self.section, "Retail sentiment was not available for this run; the sentiment view "
                           "relies on analyst consensus only.", s_an)
            ws.note("[sentiment] retail unavailable -> analyst-only fallback")
            return

        # blended view + per-source findings
        total_op = sum(s["bullish"] + s["bearish"] for s in sources.values())
        total_bull = sum(s["bullish"] for s in sources.values())
        total_n = sum(s["n_posts"] for s in sources.values())
        pct = round(total_bull / total_op * 100) if total_op else None
        ws.facts["retail"] = {"available": True, "n_posts": total_n, "pct_bullish": pct, "sources": sources,
                              "confidence": "low" if total_n < 15 else "medium" if total_n < 40 else "ok"}
        for name, s in sources.items():
            label = "StockTwits" if name == "stocktwits" else "Reddit"
            extra = f" ({int(s['declared_share']*100)}% of messages carried a user-declared label)" if s.get("declared_share") is not None else ""
            ws.add_finding(self.name, self.section,
                           f"Retail sentiment on {label} over the past week is {s['pct_bullish']}% bullish among opinionated posts "
                           f"(n={s['n_posts']}; {s['bullish']} bullish / {s['bearish']} bearish / {s['neutral']} neutral){extra}.",
                           s["source_id"])
        if len(sources) == 2:
            a, b = sources["stocktwits"]["pct_bullish"], sources["reddit"]["pct_bullish"]
            if a is not None and b is not None and abs(a - b) >= 15:
                ws.add_finding(self.name, self.section,
                               f"The two retail sources disagree: StockTwits {a}% bullish vs Reddit {b}% bullish — "
                               "a divergence worth noting rather than averaging away.",
                               sources["stocktwits"]["source_id"], sources["reddit"]["source_id"])

    # ----------------------------------------------------------------------------------------------
    @staticmethod
    def _tally(labels: list[str], n_posts: int, extra: dict | None = None) -> dict:
        c = {"bullish": 0, "bearish": 0, "neutral": 0}
        for lab in labels:
            c[lab if lab in c else "neutral"] += 1
        op = c["bullish"] + c["bearish"]
        out = {**c, "n_posts": n_posts, "pct_bullish": round(c["bullish"] / op * 100) if op else None}
        if extra:
            out.update(extra)
        return out

    def _classify(self, posts: list[dict], ticker: str) -> list[str]:
        """Batch-classify with the FAST model; deterministic fallback to 'neutral' on any parse issue."""
        labels: list[str] = []
        batch = 20
        for i in range(0, len(posts), batch):
            chunk = posts[i:i + batch]
            numbered = "\n".join(f"{j+1}. {p['text'][:400].replace(chr(10), ' ')}" for j, p in enumerate(chunk))
            prompt = (f"Classify each post's stance on {ticker} stock as bullish, bearish, or neutral "
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
