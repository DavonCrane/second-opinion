"""Retail sentiment source: recent Reddit posts/comments mentioning a ticker.

Returns raw text items; classification (bullish/bearish/neutral) is done by the Sentiment agent with the
FAST model. If Reddit isn't configured, returns None so the agent falls back to analyst consensus only —
the report then says so explicitly instead of silently omitting retail sentiment.
"""
from __future__ import annotations

from typing import Any

from .. import cache
from ..config import settings

SUBREDDITS = ["stocks", "investing", "wallstreetbets", "StockMarket"]


def recent_posts(ticker: str, limit: int = 60) -> list[dict[str, Any]] | None:
    ticker = ticker.upper()
    if not settings.reddit_configured and not settings.offline:
        return None

    def fetch():
        import praw
        reddit = praw.Reddit(client_id=settings.reddit_client_id, client_secret=settings.reddit_client_secret,
                             user_agent=settings.reddit_user_agent)
        out: list[dict[str, Any]] = []
        for sub in SUBREDDITS:
            try:
                for post in reddit.subreddit(sub).search(f'"{ticker}"', sort="new", time_filter="week", limit=limit // len(SUBREDDITS) + 5):
                    text = f"{post.title}. {(post.selftext or '')[:600]}".strip()
                    out.append({"subreddit": sub, "id": post.id, "created_utc": post.created_utc,
                                "score": post.score, "num_comments": post.num_comments,
                                "text": text, "url": f"https://reddit.com{post.permalink}"})
            except Exception:  # noqa: BLE001 — one bad subreddit shouldn't kill the run
                continue
        out.sort(key=lambda p: p["created_utc"], reverse=True)
        return out[:limit]

    try:
        return cache.cached("reddit", ticker, fetch, max_age_s=6 * 3600)
    except cache.CacheMiss:
        return None
