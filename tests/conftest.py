"""Shared test fixtures. Tests run fully offline (SO_OFFLINE=1) against fixtures/ and a FakeLLM."""
from __future__ import annotations

import json
import os
import re

import pytest

os.environ["SO_OFFLINE"] = "1"

from second_opinion.config import settings  # noqa: E402
from second_opinion.llm import FakeLLM  # noqa: E402
from second_opinion.memory import EpisodicMemory, SemanticMemory  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_and_tmp_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "offline", True)
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(settings, "reports_dir", tmp_path / "reports")
    (tmp_path / "data").mkdir()
    (tmp_path / "reports").mkdir()
    yield


def scripted_llm() -> FakeLLM:
    """A FakeLLM that answers each agent's prompt with plausible, well-formed JSON — enough to drive the
    whole pipeline end-to-end offline. It inspects the prompt to decide which agent is asking."""

    def fn(prompt: str, tier: str) -> str:
        p = prompt
        if "Classify each post's stance" in p:
            n = len(re.findall(r"^\d+\. ", p, re.M))
            labels = []
            for line in p.splitlines():
                m = re.match(r"^\d+\. (.*)", line)
                if not m:
                    continue
                t = m.group(1).lower()
                labels.append("bearish" if any(k in t for k in ("selling", "won't hold", "2000 all over")) else
                              "bullish" if any(k in t for k in ("moon", "forever", "easiest", "best ai", "adding")) else "neutral")
            return json.dumps({"labels": labels[:n]})
        if "advice_seeking" in p:
            return json.dumps({"advice_seeking": "should i buy" in p.lower()})
        if "You route requests" in p:
            return json.dumps({"mode": "focused_question" if "?" in p else "full_report", "subject": "NVDA", "question": ""})
        if "Write 5-8 concise analyst findings" in p:
            ids = sorted({int(x) for x in re.findall(r"\[source (\d+)\]", p)})
            fin, tenk = ids[0], (ids[1:] or ids)
            return json.dumps({"findings": [
                {"claim": "Data center is now roughly 87% of revenue, up from 78%, driven by AI training and inference demand.", "sources": tenk[:1]},
                {"claim": "Revenue grew 62% year over year to $211.0B, decelerating from 114% growth the prior year.", "sources": [fin] + tenk[:1]},
                {"claim": "Gross margin was 74.2% and operating margin 58.1%; free cash flow was $58.9B.", "sources": [fin]},
                {"claim": "The balance sheet holds $38.0B of cash against $9.7B of fixed-rate debt with no maturities before fiscal 2028.", "sources": [fin] + tenk[:1]},
                {"claim": "The 10-K reports three customers together represent about 40% of revenue and states this concentration increased versus the prior year.", "sources": tenk[:1]},
                {"claim": "The filing warns that several large customers are developing in-house AI accelerators that could reduce purchases.", "sources": tenk[:1]},
                {"claim": "Management states backlog for the next-generation platform is higher than a year ago but that growth rates will moderate.", "sources": tenk[-1:]},
            ], "business_model": "Full-stack accelerated computing platforms sold to cloud providers and enterprises.",
                "moat": "CUDA software ecosystem and installed developer base.",
                "risk_themes": ["customer concentration", "custom silicon substitution", "export controls", "growth deceleration"]})
        if "developments that a fundamentals-driven investor" in p:
            ids = [int(x) for x in re.findall(r"\[source (\d+)\]", p)]
            return json.dumps({"findings": [
                {"claim": "On 2026-08-08 the company beat on revenue but guided next-quarter revenue slightly below consensus.", "sources": ids[:1]},
                {"claim": "On 2026-08-11 the company pulled forward the next-generation platform ship date by one quarter.", "sources": ids[1:2]},
                {"claim": "On 2026-08-05 three of the four largest cloud providers raised full-year capex guidance.", "sources": ids[2:3]},
                {"claim": "On 2026-08-09 a major cloud customer expanded deployment of its in-house accelerator for inference.", "sources": ids[3:4]},
            ], "bull_support": ["capex guidance up", "ship date pulled forward"], "bear_support": ["guidance below consensus", "custom silicon"]})
        if "propose EPS growth" in p:
            return json.dumps({"scenarios": {
                "bear": {"eps_growth_pct": 10, "rationale": "Growth slows hard as custom silicon and export controls bite; multiple compresses to 5-yr low", "sources": []},
                "base": {"eps_growth_pct": 25, "rationale": "Deceleration continues on trend; multiple settles toward its median", "sources": []},
                "bull": {"eps_growth_pct": 40, "rationale": "Demand stays supply-constrained and next-gen ramps early; premium multiple holds", "sources": []}},
                "weights": {"bear": 0.30, "base": 0.50, "bull": 0.20},
                "weight_rationale": "Tilted 5 points toward bear because the 10-K reports increased customer concentration and retail sentiment is stretched."})
        if "You are writing the prose sections" in p:
            revised = "REVISION REQUIRED" in p
            bear3 = "**Growth decelerating into a premium multiple** — revenue growth slowed from 114% to 62% while the stock trades at 44x trailing earnings [1]."
            return json.dumps({
                "profile": "High-quality grower, premium valuation, concentrated risk",
                "business": "NVIDIA sells full-stack accelerated computing platforms, and data center is now about 87% of revenue, up from 78% [2]. Demand comes from cloud providers and enterprises building AI training and inference capacity [2].",
                "health": "The balance sheet holds $38.0B of cash against $9.7B of fixed-rate debt with no maturities before fiscal 2028 [1][2]. Free cash flow was $58.9B on a 58.1% operating margin [1].",
                "bull": ["**Demand still exceeds supply** — backlog for the next-generation platform is higher than a year ago and hyperscaler capex guidance rose again [2][8].",
                         "**Ecosystem moat** — the CUDA software stack and installed developer base raise switching costs beyond the hardware [2].",
                         "**Margins hold at scale** — operating margin was 58.1% even as revenue grew 62% [1]."],
                "bear": ["**Customer concentration rising** — three customers are about 40% of revenue and the 10-K says concentration increased versus last year [3].",
                         "**Custom-silicon substitution** — the filing warns large customers are building in-house accelerators, and one just expanded deployment [3][9].",
                         bear3 if revised else "**Valuation is high** — the stock is expensive."],
                "sentiment_check": "Both Wall Street and retail lean bullish, with analysts at 7.9/10 and Reddit posts 73% bullish [10][11]. That optimism sits against decelerating growth and guidance below consensus, a divergence that argues for extra weight on the bear case [1][6].",
                "developments": "On 2026-08-08 the company beat on revenue but guided slightly below consensus [6]. On 2026-08-11 it pulled forward the next-generation ship date by one quarter [7].",
                "since_last": "First analysis of this ticker.",
                "valuation_note": "The base case lands near the current price, so today's price already assumes the base scenario plays out [12]."})
        if "Review strictly" in p:
            if "the stock is expensive" in p:
                return json.dumps({"verdict": "REJECT", "issues": ["Bear bullet 3 ('the stock is expensive') is vaguer than its bull counterpart and cites nothing — quantify with the growth deceleration vs. the 44x multiple."],
                                   "weights_signed_off": True, "strengths": ["Bull case well cited"]})
            return json.dumps({"verdict": "APPROVE", "issues": [], "weights_signed_off": True, "strengths": ["Bear case now specific and cited"]})
        if "Answer in <=180 words" in p:
            return "NVIDIA carries $9.7B of debt against $38.0B of cash [1]. The 10-K states the notes are fixed-rate with no maturities before fiscal 2028 [2]."
        return "{}"

    return FakeLLM(fn=fn)


@pytest.fixture
def llm():
    return scripted_llm()


@pytest.fixture
def memories(tmp_path):
    return EpisodicMemory(tmp_path / "ep"), SemanticMemory(tmp_path / "sem")
