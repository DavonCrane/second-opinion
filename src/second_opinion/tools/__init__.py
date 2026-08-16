"""Tools — the system's hands. Every tool caches to disk and degrades gracefully when a provider is down.

  market.py      yfinance: price, fundamentals, financial statements, analyst consensus & targets, headlines
  edgar.py       SEC EDGAR: latest 10-K sections (Business, Risk Factors, MD&A) for RAG
  news.py        Recent news (Finnhub if configured, else yfinance headlines)
  reddit.py      Retail sentiment posts (PRAW if configured, else None -> analyst-only fallback)
  calculator.py  Deterministic financial math: growth, margins, Rule of 40, debt ratios, scenario valuation
"""
