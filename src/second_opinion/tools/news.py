"""Recent news. Finnhub if a key is configured (richer, dated, deduped); otherwise yfinance headlines.

Graceful degradation is the point: the News agent always gets *something* or an explicit empty list.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from .. import cache
from ..config import settings
from . import market


def recent_news(ticker: str, days: int = 14, limit: int = 20) -> list[dict[str, Any]]:
    ticker = ticker.upper()
    if settings.finnhub_api_key:
        def fetch():
            import requests
            to = dt.date.today()
            frm = to - dt.timedelta(days=days)
            r = requests.get("https://finnhub.io/api/v1/company-news",
                             params={"symbol": ticker, "from": frm.isoformat(), "to": to.isoformat(),
                                     "token": settings.finnhub_api_key}, timeout=20)
            r.raise_for_status()
            seen, out = set(), []
            for it in r.json():
                title = (it.get("headline") or "").strip()
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                out.append({"title": title, "publisher": it.get("source"), "link": it.get("url"),
                            "published": dt.datetime.utcfromtimestamp(it.get("datetime", 0)).isoformat(),
                            "summary": it.get("summary")})
                if len(out) >= limit:
                    break
            return out
        try:
            return cache.cached("news_finnhub", f"{ticker}_{days}d", fetch, max_age_s=2 * 3600)
        except cache.CacheMiss:
            raise
        except Exception:  # noqa: BLE001 — fall through to yfinance
            pass
    try:
        return market.headlines(ticker, limit=limit)
    except cache.CacheMiss:
        raise
    except Exception:  # noqa: BLE001
        return []
