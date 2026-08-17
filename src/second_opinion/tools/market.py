"""Market data via yfinance: price, profile, financial statements, analyst consensus, price history, headlines.

Everything returned is plain JSON-serialisable dicts so it can be cached and used as fixtures.
"""
from __future__ import annotations

import math
from typing import Any

from .. import cache


def _clean(v: Any) -> Any:
    """Convert numpy/pandas scalars & NaN to JSON-safe python values."""
    try:
        if v is None:
            return None
        if hasattr(v, "item"):
            v = v.item()
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return None
        return v
    except Exception:  # noqa: BLE001
        return None


def _row(df, label: str, n: int = 4) -> list[float | None]:
    """First n annual values for a statement row (most recent first), or [] if the row is missing."""
    try:
        if df is None or df.empty or label not in df.index:
            return []
        return [_clean(x) for x in list(df.loc[label].values)[:n]]
    except Exception:  # noqa: BLE001
        return []


def resolve_ticker(query: str) -> str:
    """Accept a ticker or a company name ('nvidia') and return an upper-case ticker."""
    q = query.strip().strip("\"'$")
    if not q:
        raise ValueError("Empty ticker/company name")
    # Already-resolved names (cache/fixture) win, so 'nvidia' -> NVDA even offline
    hit = cache.read("resolve", q.lower())
    if hit:
        return hit
    if q.isupper() and q.replace(".", "").replace("-", "").isalnum() and len(q) <= 6 and " " not in q:
        return q                                                    # looks like a ticker: NVDA, BRK.B
    if q.replace(".", "").replace("-", "").isalnum() and len(q) <= 4 and " " not in q:
        return q.upper()                                            # short lowercase: 'msft', 'jpm'

    def fetch():
        import yfinance as yf
        res = yf.Search(q, max_results=5)
        quotes = [x for x in res.quotes if x.get("quoteType") == "EQUITY"]
        if not quotes:
            raise ValueError(f"No equity found for '{query}'")
        return quotes[0]["symbol"]

    return cache.cached("resolve", q.lower(), fetch, max_age_s=30 * 24 * 3600)


def snapshot(ticker: str) -> dict[str, Any]:
    """Company profile + price + fundamentals + analyst consensus, one dict."""
    ticker = ticker.upper()

    def fetch():
        import yfinance as yf
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info or info.get("regularMarketPrice") is None and info.get("currentPrice") is None:
            raise ValueError(f"No market data for ticker '{ticker}' — is it valid and listed?")
        inc, bs, cf = t.income_stmt, t.balance_sheet, t.cashflow
        rec = None
        try:
            rs = t.recommendations_summary
            if rs is not None and not rs.empty:
                r0 = rs.iloc[0]
                rec = {k: int(_clean(r0.get(k)) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
        except Exception:  # noqa: BLE001
            rec = None
        hist = t.history(period="5y", interval="1mo")
        closes = [_clean(x) for x in hist["Close"].values] if hist is not None and not hist.empty else []
        return {
            "ticker": ticker,
            "name": info.get("longName") or info.get("shortName") or ticker,
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "summary": (info.get("longBusinessSummary") or "")[:1500],
            "price": _clean(info.get("currentPrice") or info.get("regularMarketPrice")),
            "currency": info.get("currency"),
            "market_cap": _clean(info.get("marketCap")),
            "trailing_pe": _clean(info.get("trailingPE")),
            "forward_pe": _clean(info.get("forwardPE")),
            "trailing_eps": _clean(info.get("trailingEps")),
            "forward_eps": _clean(info.get("forwardEps")),
            "revenue_growth_ttm": _clean(info.get("revenueGrowth")),  # fraction
            "earnings_growth_ttm": _clean(info.get("earningsGrowth")),
            "gross_margin_ttm": _clean(info.get("grossMargins")),
            "operating_margin_ttm": _clean(info.get("operatingMargins")),
            "free_cash_flow_ttm": _clean(info.get("freeCashflow")),
            "total_debt": _clean(info.get("totalDebt")),
            "total_cash": _clean(info.get("totalCash")),
            "debt_to_equity": _clean(info.get("debtToEquity")),  # yfinance gives % (e.g. 12.9)
            "beta": _clean(info.get("beta")),
            "analyst": {
                "recommendation": info.get("recommendationKey"),
                "recommendation_mean": _clean(info.get("recommendationMean")),  # 1=strong buy .. 5=sell
                "n_analysts": _clean(info.get("numberOfAnalystOpinions")),
                "target_low": _clean(info.get("targetLowPrice")),
                "target_mean": _clean(info.get("targetMeanPrice")),
                "target_high": _clean(info.get("targetHighPrice")),
                "counts": rec,
            },
            "statements": {
                "years": [str(c)[:10] for c in list(inc.columns)[:4]] if inc is not None and not inc.empty else [],
                "revenue": _row(inc, "Total Revenue"),
                "gross_profit": _row(inc, "Gross Profit"),
                "operating_income": _row(inc, "Operating Income"),
                "net_income": _row(inc, "Net Income"),
                "ebit": _row(inc, "EBIT"),
                "interest_expense": _row(inc, "Interest Expense"),
                "diluted_eps": _row(inc, "Diluted EPS"),
                "total_debt": _row(bs, "Total Debt"),
                "cash": _row(bs, "Cash Cash Equivalents And Short Term Investments") or _row(bs, "Cash And Cash Equivalents"),
                "cash_only": _row(bs, "Cash And Cash Equivalents"),
                "short_term_investments": _row(bs, "Other Short Term Investments"),
                "equity": _row(bs, "Stockholders Equity"),
                "free_cash_flow": _row(cf, "Free Cash Flow"),
            },
            "monthly_closes_5y": closes,
        }

    return cache.cached("snapshot", ticker, fetch)


def headlines(ticker: str, limit: int = 15) -> list[dict[str, Any]]:
    ticker = ticker.upper()

    def fetch():
        import yfinance as yf
        items = yf.Ticker(ticker).news or []
        out = []
        for it in items[:limit]:
            c = it.get("content", it)  # yfinance >=0.2.5x nests under 'content'
            out.append({
                "title": c.get("title"),
                "publisher": (c.get("provider") or {}).get("displayName") if isinstance(c.get("provider"), dict) else c.get("publisher"),
                "link": (c.get("canonicalUrl") or {}).get("url") if isinstance(c.get("canonicalUrl"), dict) else c.get("link"),
                "published": c.get("pubDate") or c.get("providerPublishTime"),
                "summary": c.get("summary") or c.get("description"),
            })
        return out

    return cache.cached("headlines", ticker, fetch, max_age_s=2 * 3600)


def price_history(ticker: str, period: str = "5y") -> list[dict[str, Any]]:
    """Daily closes as [{"date": "YYYY-MM-DD", "close": float}], oldest first. Cached 6h."""
    ticker = ticker.upper()

    def fetch():
        import yfinance as yf
        hist = yf.Ticker(ticker).history(period=period, interval="1d", auto_adjust=True)
        if hist is None or hist.empty:
            raise ValueError(f"No price history for {ticker}")
        return [{"date": str(idx)[:10], "close": round(float(c), 4)} for idx, c in hist["Close"].items() if c == c]

    return cache.cached("prices", f"{ticker}_{period}", fetch, max_age_s=6 * 3600)
