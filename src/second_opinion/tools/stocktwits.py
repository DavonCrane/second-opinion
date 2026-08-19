"""Retail sentiment source #2: StockTwits public symbol stream (no auth required).

Every message is tagged to the ticker, and many carry a user-declared Bullish/Bearish label — so part of the
sentiment comes from the crowd's own labels and only the unlabeled remainder needs model classification.
Returns raw items; the Sentiment agent does the counting/classifying.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .. import cache

_URL = "https://api.stocktwits.com/api/2/streams/symbol/{sym}.json"
_UA = {"User-Agent": "Mozilla/5.0 (second-opinion research tool; student project)", "Accept": "application/json"}


def recent_messages(ticker: str, pages: int = 3, days: int = 7) -> list[dict[str, Any]] | None:
    """Up to ~30*pages most-recent messages within `days`. None if the endpoint is unreachable/unknown symbol."""
    ticker = ticker.upper()

    def fetch():
        import requests
        out: list[dict[str, Any]] = []
        cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)
        max_id = None
        for _ in range(pages):
            params = {"max": max_id} if max_id else {}
            r = requests.get(_URL.format(sym=ticker), params=params, headers=_UA, timeout=20)
            if r.status_code == 404:
                raise ValueError(f"StockTwits: unknown symbol {ticker}")
            r.raise_for_status()
            msgs = r.json().get("messages", [])
            if not msgs:
                break
            for m in msgs:
                ts = m.get("created_at", "")
                try:
                    when = dt.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                except ValueError:
                    when = dt.datetime.now(dt.timezone.utc)
                if when < cutoff:
                    msgs = []
                    break
                sent = ((m.get("entities") or {}).get("sentiment") or {}).get("basic")
                out.append({
                    "id": m.get("id"), "created_utc": when.timestamp(),
                    "text": (m.get("body") or "").strip(),
                    "label": sent.lower() if sent in ("Bullish", "Bearish") else None,
                    "likes": ((m.get("likes") or {}).get("total")) or 0,
                    "user": (m.get("user") or {}).get("username"),
                    "url": f"https://stocktwits.com/{(m.get('user') or {}).get('username','')}/message/{m.get('id')}",
                })
            if not msgs:
                break
            max_id = min(x["id"] for x in out if x["id"] is not None) - 1
        return out

    try:
        return cache.cached("stocktwits", ticker, fetch, max_age_s=2 * 3600)
    except cache.CacheMiss:
        return None
    except Exception:  # noqa: BLE001 — provider down / symbol unknown -> caller falls back
        return None
