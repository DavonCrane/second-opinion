"""Agents — each is a small class with a `run(workspace)` method that reads tools/memory and writes Findings.

  fundamentals.py  10-K RAG + financial statements -> business, health, growth, risk-language findings
  news.py          recent developments (deduped, dated, relevant only)
  sentiment.py     analyst consensus (yfinance) + Reddit posts classified by the FAST model
  valuation.py     metrics table + bear/base/bull scenarios + Claude's weighted 12-mo view
  writer.py        assembles the one-page report from the workspace, revises on critique
  critic.py        Risk Critic: attacks the draft (weak bear case, uncited claims, stale data, weight tilt)
"""
from .fundamentals import FundamentalsAgent
from .news import NewsAgent
from .sentiment import SentimentAgent
from .valuation import ValuationAgent
from .writer import WriterAgent
from .critic import CriticAgent

__all__ = ["FundamentalsAgent", "NewsAgent", "SentimentAgent", "ValuationAgent", "WriterAgent", "CriticAgent"]
