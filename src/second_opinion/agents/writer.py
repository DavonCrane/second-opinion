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

    REQUIRED = ("profile", "business", "health", "bull", "bear", "sentiment_check", "developments", "since_last")

    def run(self, ws: Workspace, critique: dict | None = None) -> str:
        prose = self._normalize(self._prose(ws, critique))
        missing = [k for k in self.REQUIRED if not prose.get(k)]
        if missing:
            ws.note(f"[writer] model output missing {missing} — retrying with strict schema")
            retry = self._normalize(self._prose(ws, critique, strict_missing=missing))
            for k in missing:
                if retry.get(k):
                    prose[k] = retry[k]
            still = [k for k in self.REQUIRED if not prose.get(k)]
            if still:
                ws.errors.append(f"writer: sections {still} filled from raw findings (model omitted them twice)")
                prose = self._fallback_fill(ws, prose, still)
        md = self._render(ws, prose)
        ws.drafts.append(md)
        ws.report_md = md
        return md

    # ---------------------------------------------------------------------------------
    def _normalize(self, raw: dict) -> dict:
        """Accept common deviations: nested under 'report'/'sections', different casing, bull/bear as strings."""
        d = raw or {}
        for wrapper in ("report", "sections", "prose", "content"):
            if isinstance(d.get(wrapper), dict):
                d = {**d, **d[wrapper]}
        out: dict = {}
        aliases = {"profile": ("profile", "profile_line", "one_liner"), "business": ("business", "what_the_company_does", "company"),
                   "health": ("health", "financial_health", "balance_sheet"), "bull": ("bull", "bull_case"), "bear": ("bear", "bear_case"),
                   "sentiment_check": ("sentiment_check", "sentiment"), "developments": ("developments", "recent_developments", "news"),
                   "since_last": ("since_last", "since_last_analysis"), "valuation_note": ("valuation_note", "valuation")}
        lower = {str(k).lower(): v for k, v in d.items()}
        for key, names in aliases.items():
            for n in names:
                if n in lower and lower[n] not in (None, "", [], {}):
                    out[key] = lower[n]
                    break
        for k in ("bull", "bear"):
            v = out.get(k)
            if isinstance(v, str):
                out[k] = [s.strip(" -*") for s in v.split("\n") if s.strip()]
            elif isinstance(v, list):
                out[k] = [str(x.get("text") or x.get("claim") or x) if isinstance(x, dict) else str(x) for x in v]
        return out

    def _fallback_fill(self, ws: Workspace, prose: dict, keys: list[str]) -> dict:
        """Never ship an empty section: build it from cited workspace findings."""
        def joined(section: str, n: int = 3) -> str:
            fs = ws.findings_for(section)[:n]
            return " ".join(f"{f.claim} {ws.cite(*f.source_ids)}" for f in fs) or "No findings available for this section in this run."
        fills = {
            "profile": "Auto-generated summary — see findings below",
            "business": joined("fundamentals", 2), "health": joined("fundamentals", 3),
            "bull": [f"**Finding** — {f.claim} {ws.cite(*f.source_ids)}" for f in ws.findings_for("fundamentals")[:3]] or ["No bull-case findings available."],
            "bear": [f"**Risk** — {f.claim} {ws.cite(*f.source_ids)}" for f in ws.findings_for("fundamentals")[3:6]] or ["No bear-case findings available."],
            "sentiment_check": joined("sentiment", 2), "developments": joined("news", 3),
            "since_last": "First analysis of this ticker." if not ws.facts.get("episodic_diff") else "See episodic memory diff.",
        }
        for k in keys:
            prose[k] = fills[k]
        return prose

    # ---------------------------------------------------------------------------------
    def _prose(self, ws: Workspace, critique: dict | None, strict_missing: list[str] | None = None) -> dict:
        snap, m = ws.facts["snapshot"], ws.facts.get("metrics", {})
        sc = ws.facts.get("scenarios")
        diff = ws.facts.get("episodic_diff")
        revision = ""
        if strict_missing:
            revision = (f"\n\nIMPORTANT: your previous answer omitted these required top-level JSON keys: {strict_missing}. "
                        "Return ALL keys listed below at the TOP LEVEL of the JSON object, using EXACTLY these key names, "
                        "no wrapper object, no extra keys.")
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

Return ONE flat JSON object with EXACTLY these eight top-level keys and no others — "profile", "business", "health", "bull", "bear", "sentiment_check", "developments", "since_last". "bull" and "bear" are JSON arrays of 3 strings; every other value is a string. Markdown allowed inside strings; every factual sentence ends with [n] citations. Key meanings:
- "profile": one line, <=12 words, e.g. "High-quality grower, premium valuation, concentrated risk"
- "business": 2-3 sentences on what the company does and its mix
- "health": 2-3 sentences on balance sheet, cash generation, liquidity
- "bull": exactly 3 bullet strings, each "**Bold headline** — one cited sentence". Bullets must rest on evidence (filings, financials, news, sentiment) — NEVER cite the scenario table or the weighted view as a bull/bear reason (that would be circular)
- "bear": exactly 3 bullet strings, same format; the bear case must be AS SPECIFIC AND EVIDENCED as the bull case
- "sentiment_check": 2-3 sentences that cross-examine sentiment against fundamentals — name any divergence explicitly
- "developments": 2-4 sentences on recent news that matters (dated), or one sentence saying none was available
- "since_last": 1-3 sentences: price change and what is new vs the previous analysis, or "First analysis of this ticker." """
        return self.llm.complete_json(prompt, system=ANALYST_SYSTEM, tier="strong", max_tokens=3500)

    @staticmethod
    def _valuation_note(sc: dict) -> str:
        """Deterministic interpretation of the scenario table — pure comparison, so code writes it, not the model."""
        rows = {r["name"]: r["implied_price"] for r in sc["rows"]}
        price, view = sc["price"], sc["claude_view"]
        bear, base, bull = rows.get("bear"), rows.get("base"), rows.get("bull")
        if bear is None or base is None or bull is None:
            return ""
        if price < bear:
            pos = "below even the bear scenario — the market is pricing an outcome worse than the stated bear assumptions"
        elif price < base:
            pos = f"between the bear (${bear:,.0f}) and base (${base:,.0f}) scenarios — the market is pricing something short of the base case"
        elif price < bull:
            pos = f"between the base (${base:,.0f}) and bull (${bull:,.0f}) scenarios — today's price already assumes better than the base case"
        else:
            pos = "above even the bull scenario — the market is pricing an outcome beyond the stated bull assumptions"
        rel = "above" if view > price else "below"
        return (f"The current price (${price:,.2f}) sits {pos}. The weighted view (${view:,.2f}) is {abs(view / price - 1) * 100:.0f}% {rel} "
                f"the current price; treat that as the arithmetic of the stated assumptions, not a forecast.")

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
            row("Total debt / net cash (cash + ST investments)", f"{fm(m.get('total_debt'))} / {fm(m.get('net_cash'))}", fin_src),
            row("Debt-to-equity · interest coverage", f"{m.get('debt_to_equity') if m.get('debt_to_equity') is None else round(m['debt_to_equity'],2)} · {m.get('interest_coverage') if m.get('interest_coverage') is None else str(round(m['interest_coverage'],1))+'×'}", fin_src),
            row("P/E (trailing / forward)", f"{'n/a' if m.get('trailing_pe') is None else str(round(m['trailing_pe'],1))+'×'} / {'n/a' if m.get('forward_pe') is None else str(round(m['forward_pe'],1))+'×'}", fin_src),
        ]
        if an.get("score_0_10") is not None:
            tgt = f" · target ${an.get('target_mean'):.2f} ({an['target_upside_pct']:+.0f}%)" if an.get("target_upside_pct") is not None else ""
            rows.append(row("Analyst consensus", f"{(an.get('recommendation') or '').replace('_',' ').title()} {an['score_0_10']}/10 (n={an.get('n_analysts')}){tgt}", an_src))
        if rt.get("available"):
            srcs = rt.get("sources") or {}
            parts, ids = [], []
            for key, label in (("stocktwits", "StockTwits"), ("reddit", "Reddit")):
                if key in srcs:
                    parts.append(f"{label} {srcs[key]['pct_bullish']}% bullish (n={srcs[key]['n_posts']})")
                    ids.append(srcs[key].get("source_id"))
            ids = [i for i in ids if i] or [an_src]
            rows.append(f"| Retail sentiment (7d) | {' · '.join(parts)} · confidence {rt.get('confidence')} | {''.join(f'[{i}]' for i in ids)} |")
        else:
            why = (rt.get("reason") or "unavailable")
            rows.append(row("Retail sentiment", f"{why} — analyst-only", an_src))

        bullets = lambda items: "\n".join(f"{i+1}. {b}" for i, b in enumerate(items))
        scen_md = "_Not computed: no positive trailing EPS._"
        if sc:
            scen_rows = "\n".join(f"| **{r['name'].title()}** | {r['rationale']} | {r['eps_growth_pct']:+.0f}% | {r['multiple']}× | **${r['implied_price']}** ({r['upside_pct']:+.0f}%) |" for r in sc["rows"])
            w = sc["weights"]
            scen_md = (f"_Scenarios, not predictions — arithmetic on stated, sourced assumptions (EPS ${sc['eps']}, price ${sc['price']})._ [{val_src}]\n\n"
                       f"| Scenario | What has to be true | EPS growth | Multiple | Implied 12-mo price |\n|---|---|---|---|---|\n{scen_rows}\n\n"
                       f"> **Claude's 12-mo view: ~${sc['claude_view']}** (weights {w['bear']:.0%} bear / {w['base']:.0%} base / {w['bull']:.0%} bull; "
                       f"neutral prior 25/50/25) — {sc.get('weight_rationale','')} [{val_src}]"
                       + (f" · **Analyst mean target: ${sc['analyst_mean_target']:.2f}** [{an_src}]" if sc.get("analyst_mean_target") else "")
                       + f"\n>\n> {self._valuation_note(sc)}")
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
@@SOURCES@@

---
{DISCLAIMER}
"""
        if ws.errors:
            md += "\n_Data-quality notes: " + "; ".join(ws.errors) + "_\n"
        # Sources: list only what the report actually cites (retrieved-but-unused headlines are omitted; numbering is kept stable)
        import re as _re
        body = md.split("## Sources")[0]
        cited = {int(n) for n in _re.findall(r"\[(\d+)\]", body)}
        lines = [f"[{s_['id']}] {s_['label']}" + (f" — {s_['url']}" if s_.get("url") else "")
                 for s_ in ws.sources if s_["id"] in cited]
        md = md.replace("@@SOURCES@@", "\n".join(lines) if lines else ws.sources_text())
        return md
