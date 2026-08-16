"""SEC EDGAR: pull the key sections of the latest 10-K for the RAG corpus.

We deliberately ingest only Item 1 (Business), Item 1A (Risk Factors), and Item 7 (MD&A) — the sections
that carry narrative evidence for bull/bear cases. Financial numbers come from yfinance/XBRL instead.

Requires EDGAR_IDENTITY in .env (SEC asks every automated client to identify itself).
"""
from __future__ import annotations

from typing import Any

from .. import cache
from ..config import settings

SECTIONS = {
    "Item 1": "business",
    "Item 1A": "risk_factors",
    "Item 7": "management_discussion",
}


def latest_10k_sections(ticker: str) -> dict[str, Any]:
    """Return {'ticker','form','filing_date','period','sections': {item: text}}."""
    ticker = ticker.upper()

    def fetch():
        if not settings.edgar_identity:
            raise RuntimeError("EDGAR_IDENTITY is not set in .env (SEC requires 'Name email@example.com').")
        from edgar import Company, set_identity
        set_identity(settings.edgar_identity)
        company = Company(ticker)
        tenk = company.latest_tenk
        if tenk is None:
            raise ValueError(f"No 10-K found on EDGAR for {ticker} (foreign filers use 20-F/40-F; not supported yet).")
        sections: dict[str, str] = {}
        for item, attr in SECTIONS.items():
            text = None
            try:
                text = getattr(tenk, attr, None)
                if not text:
                    text = tenk[item]
            except Exception:  # noqa: BLE001
                text = None
            if text:
                sections[item] = str(text)
        if not sections:
            raise ValueError(f"10-K for {ticker} parsed but no sections extracted.")
        return {
            "ticker": ticker,
            "form": "10-K",
            "filing_date": str(getattr(tenk, "filing_date", "")),
            "period": str(getattr(tenk, "period_of_report", "")),
            "sections": sections,
        }

    return cache.cached("tenk", ticker, fetch, max_age_s=30 * 24 * 3600)
