"""Writer agent: assembles the one-page report from the workspace; revises when the critic objects.

The template is fixed (section completeness is an eval metric). The metrics table and scenario table are
rendered from ws.facts by code — the model writes only the prose sections, and every sentence must cite.
"""
from __future__ import annotations

from ..memory.workspace import Workspace
from ..guardrails import DISCLAIMER
from .base import Agent, ANALYST_SYSTEM

SECTIONS = ["Snapshot", "What the company does", "Financial health", "Bull case", "Bear case",
            "12-month valuation scenarios", "Sentiment check", "Recent developments", "Since last analysis", "Sources"]


class WriterAgent(Agent):
    name = "writer"
    section = "report"

    def run(self, ws: Workspace, critique: dict | None = None) -> str:
        prose = self._prose(ws, critique)
        md = self._render(ws, prose)
        ws.drafts.append(md)
        ws.report_md = md
        return md

    # ---------------------------------------------------------------------------------
    def _prose(self, ws: Workspace, critique: dict | None) -> dict:
        snap, m = ws.facts["snapshot"], ws.facts.get("metrics", {})
        sc = ws.facts.get("scenarios")
        diff = ws.facts.get("episodic_diff")
        revision = ""
        if critique:
            revision = ("\n\nREVISION REQUIRED. A Risk Critic rejected the previous draft. Fix every point below without "
                        "weakening cited facts:\n- " + "\n- ".join(critique.get("issues", [])) +
                        "\nPrevious draft:\n" + (ws.drafts[-1] if ws.drafts else ""))
        prompt = f"""You are writing the prose sections of a one-page equity research report on {snap.get('name')} ({ws.ticker}).
Price ${snap.get('price')}. Everything you write must be supported by the findings below and cite their source ids in [n] form
at the end of each sentence. Do not introduce numbers that are not in the findings. No advice language.

ALL FINDINGS (with source ids):
{ws.findings_text()}

Scenario summary: {sc if sc else 'not available'}
Sentiment facts: analyst={ws.facts.get('analyst')} retail={ws.facts.get('retail')}
Since-last-analysis data: {diff if diff else 'first analysis of this ticker'}
Data-quality notes: {ws.errors or 'none'}{revision}

Write JSON with these keys (markdown allowed inside strings, every factual sentence ends with [n] citations):
- "profile": one line, <=12 words, e.g. "High-quality grower, premium valuation, concentrated risk"
- "business": 2-3 sentences on what the company does and its mix
- "health": 2-3 sentences on balance sheet, cash generation, liquidity
- "bull": exactly 3 bullet strings, each "**Bold headline** — one cited sentence"
- "bear": exactly 3 bullet strings, same format; the bear case must be AS SPECIFIC AND EVIDENCED as the bull case
- "sentiment_check": 2-3 sentences that cross-examine sentiment against fundamentals — name any divergence explicitly
- "developments": 2-4 sentences on recent news that matters (dated), or one sentence saying none was available
- "since_last": 1-3 sentences: price change and what is new vs the previous analysis, or "First analysis of this ticker."
- "valuation_note": one sentence interpreting the scenario table (e.g. whether the current price already assumes the base case)"""
        return self.llm.complete_json(prompt, system=ANALYST_SYSTEM, tier="strong", max_tokens=1800)

    # ---------------------------------------------------------------------------------
    def _render(self, ws: Workspace, p: dict) -> str:
        snap, m = ws.facts["snapshot"], ws.facts.get("metrics", {})
        an, rt, sc = ws.facts.get("analyst", {}), ws.facts.get("retail", {}), ws.facts.get("scenarios")
        fin_src = next((s["id"] for s in ws.sources if "financial statements" in s["label"]), 1)
        an_src = next((s["id"] for s in ws.sources if "Analyst recommendations" in s["label"]), fin_src)
        rd_src = next((s["id"] for s in ws.sources if s["label"].startswith("Reddit posts")), None)
        val_src = next((s["id"] for s in ws.sources if s["label"].startswith("Scenario valuation")), fin_src)
        fp, fm = self._fmt_pct, self._fmt_money

        def row(label, val, src):
            return f"| {label} | {val} | [{src}] |"

        r40 = f"**{m['rule_of_40']:.0f}** ({fp(m.get('revenue_growth_yoy_pct'),False)} + {fp(m.get('operating_margin_pct'),False)})" \
            if m.get("rule_of_40") is not None else "n/a — not a recurring-revenue business model"
        rows = [
            row("Revenue growth (YoY)", f"{fp(m.get('revenue_growth_yoy_pct'))}" + (f" (prior yr {fp(m.get('revenue_growth_prev_yoy_pct'))})" if m.get('revenue_growth_prev_yoy_pct') is not None else ""), fin_src),
            row("3-yr revenue CAGR", fp(m.get("revenue_cagr_3y_pct")), fin_src),
            row("Gross / operating margin", f"{fp(m.get('gross_margin_pct'),False)} / {fp(m.get('operating_margin_pct'),False)}", fin_src),
            row("Rule of 40", r40, fin_src),
            row("Free cash flow", f"{fm(m.get('free_cash_flow'))}" + (f" ({fp(m.get('fcf_growth_pct'))} y/y)" if m.get('fcf_growth_pct') is not None else ""), fin_src),
            row("Total debt / net cash", f"{fm(m.get('total_debt'))} / {fm(m.get('net_cash'))}", fin_src),
            row("Debt-to-equity · interest coverage", f"{m.get('debt_to_equity') if m.get('debt_to_equity') is None else round(m['debt_to_equity'],2)} · {m.get('interest_coverage') if m.get('interest_coverage') is None else str(round(m['interest_coverage'],1))+'×'}", fin_src),
            row("P/E (trailing / forward)", f"{m.get('trailing_pe') if m.get('trailing_pe') is None else round(m['trailing_pe'],1)}× / {m.get('forward_pe') if m.get('forward_pe') is None else round(m['forward_pe'],1)}×", fin_src),
        ]
        if an.get("score_0_10") is not None:
            tgt = f" · target ${an.get('target_mean')} ({an['target_upside_pct']:+.0f}%)" if an.get("target_upside_pct") is not None else ""
            rows.append(row("Analyst consensus", f"{(an.get('recommendation') or '').replace('_',' ').title()} {an['score_0_10']}/10 (n={an.get('n_analysts')}){tgt}", an_src))
        if rt.get("available") and rd_src:
            rows.append(row("Retail sentiment (Reddit, 7d)", f"{rt.get('pct_bullish')}% bullish (n={rt.get('n_posts')}, confidence {rt.get('confidence')})", rd_src))
        else:
            rows.append(row("Retail sentiment", "not available this run (analyst-only)", an_src))

        bullets = lambda items: "\n".join(f"{i+1}. {b}" for i, b in enumerate(items))
        scen_md = "_Not computed: no positive trailing EPS._"
        if sc:
            scen_rows = "\n".join(f"| **{r['name'].title()}** | {r['rationale']} | {r['eps_growth_pct']:+.0f}% | {r['multiple']}× | **${r['implied_price']}** ({r['upside_pct']:+.0f}%) |" for r in sc["rows"])
            w = sc["weights"]
            scen_md = (f"_Scenarios, not predictions — arithmetic on stated, sourced assumptions (EPS ${sc['eps']}, price ${sc['price']})._ [{val_src}]\n\n"
                       f"| Scenario | What has to be true | EPS growth | Multiple | Implied 12-mo price |\n|---|---|---|---|---|\n{scen_rows}\n\n"
                       f"> **Claude's 12-mo view: ~${sc['claude_view']}** (weights {w['bear']:.0%} bear / {w['base']:.0%} base / {w['bull']:.0%} bull; "
                       f"neutral prior 25/50/25) — {sc.get('weight_rationale','')} [{val_src}]"
                       + (f" · **Analyst mean target: ${sc['analyst_mean_target']}** [{an_src}]" if sc.get("analyst_mean_target") else "")
                       + f"\n>\n> {p.get('valuation_note','')}")
        filing = ws.facts.get("filing") or {}
        data_note = f"10-K {filing.get('period','')}" if filing else "no 10-K text this run"
        md = f"""# {ws.ticker} — {snap.get('name')}
**Generated:** {ws.created_at[:10]} · **Price at analysis:** ${snap.get('price')} · **Data:** {data_note}, market data & news to {ws.created_at[:10]}
**Profile:** {p.get('profile','')}

## Snapshot
| Metric | Value | Source |
|---|---|---|
{chr(10).join(rows)}

## What the company does
{p.get('business','')}

## Financial health
{p.get('health','')}

## Bull case
{bullets(p.get('bull', []))}

## Bear case
{bullets(p.get('bear', []))}

## 12-month valuation scenarios
{scen_md}

## Sentiment check
{p.get('sentiment_check','')}

## Recent developments
{p.get('developments','')}

## Since last analysis
{p.get('since_last','')}

## Sources
{ws.sources_text()}

---
{DISCLAIMER}
"""
        if ws.errors:
            md += "\n_Data-quality notes: " + "; ".join(ws.errors) + "_\n"
        return md
