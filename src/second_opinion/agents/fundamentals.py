"""Fundamentals agent: what the business is, how healthy it is, and what the 10-K itself says about risk.

Evidence: yfinance statements (numbers) + RAG over the 10-K (narrative). Produces cited findings and
fills ws.facts["metrics"] via the calculator (never LLM arithmetic).
"""
from __future__ import annotations

from typing import Any

from .. import cache
from ..memory.workspace import Workspace
from ..rag import FilingIndex
from ..tools import calculator as calc, edgar, market
from .base import Agent, ANALYST_SYSTEM

RAG_QUERIES = {
    "moat": "competitive advantages, moat, customer switching costs, ecosystem, market position",
    "risks": "principal risks, customer concentration, competition, regulatory, supply constraints",
    "growth": "revenue growth drivers, demand, backlog, guidance, outlook",
    "capital": "debt, liquidity, cash, credit facilities, capital allocation, buybacks",
}


class FundamentalsAgent(Agent):
    name = "fundamentals"
    section = "fundamentals"

    def run(self, ws: Workspace) -> None:
        snap = ws.facts["snapshot"]
        st = snap.get("statements") or {}
        ws.note("[fundamentals] computing metrics from statements")

        # ---- numbers (calculator only) -------------------------------------------------
        rev = st.get("revenue") or []
        yoy = calc.pct(rev[0], rev[1]) if len(rev) >= 2 else (snap.get("revenue_growth_ttm") or 0) * 100 if snap.get("revenue_growth_ttm") is not None else None
        prev_yoy = calc.pct(rev[1], rev[2]) if len(rev) >= 3 else None
        cagr3 = calc.cagr(rev[0], rev[3], 3) if len(rev) >= 4 else None
        gm = calc.margin((st.get("gross_profit") or [None])[0], rev[0] if rev else None) or (snap.get("gross_margin_ttm") or 0) * 100 or None
        om = calc.margin((st.get("operating_income") or [None])[0], rev[0] if rev else None) or (snap.get("operating_margin_ttm") or 0) * 100 or None
        fcf = (st.get("free_cash_flow") or [None])[0] or snap.get("free_cash_flow_ttm")
        fcf_prev = (st.get("free_cash_flow") or [None, None])[1] if len(st.get("free_cash_flow") or []) > 1 else None
        debt = (st.get("total_debt") or [None])[0] or snap.get("total_debt")
        cash = (st.get("cash") or [None])[0] or snap.get("total_cash")
        equity = (st.get("equity") or [None])[0]
        ebit = (st.get("ebit") or [None])[0] or (st.get("operating_income") or [None])[0]
        interest = (st.get("interest_expense") or [None])[0]
        r40_applies = calc.rule_of_40_applies(snap.get("sector"), snap.get("industry"))
        metrics = {
            "revenue_growth_yoy_pct": yoy, "revenue_growth_prev_yoy_pct": prev_yoy, "revenue_cagr_3y_pct": cagr3,
            "gross_margin_pct": gm, "operating_margin_pct": om,
            "rule_of_40": calc.rule_of_40(yoy, om) if r40_applies else None, "rule_of_40_applies": r40_applies,
            "free_cash_flow": fcf, "fcf_growth_pct": calc.pct(fcf, fcf_prev),
            "total_debt": debt, "cash": cash, "net_cash": (cash - debt) if (cash is not None and debt is not None) else None,
            "debt_to_equity": calc.safe_ratio(debt, equity) if equity else (snap.get("debt_to_equity") / 100 if snap.get("debt_to_equity") is not None else None),
            "interest_coverage": calc.interest_coverage(ebit, interest),
            "trailing_pe": snap.get("trailing_pe"), "forward_pe": snap.get("forward_pe"), "trailing_eps": snap.get("trailing_eps"),
            "fiscal_years": st.get("years"),
        }
        ws.facts["metrics"] = metrics
        s_fin = ws.add_source(f"{ws.ticker} financial statements & profile via yfinance ({ws.created_at[:10]})")

        # ---- 10-K narrative via RAG ---------------------------------------------------
        passages: list[str] = []
        idx = None
        try:
            filing = edgar.latest_10k_sections(ws.ticker)
            idx = FilingIndex(ws.ticker)
            n = idx.ingest(filing)
            ws.note(f"[fundamentals] 10-K {filing.get('period','')} ingested: {n} chunks ({idx.backend_name} backend)")
            ws.facts["filing"] = {k: filing.get(k) for k in ("form", "filing_date", "period")}
            for topic, q in RAG_QUERIES.items():
                for ch in idx.retrieve(q, k=2):
                    sid = ws.add_source(idx.citation_label(ch))
                    passages.append(f"[source {sid}] ({topic}, {ch['item']}) {ch['text'][:900]}")
        except cache.CacheMiss as e:
            ws.errors.append(f"fundamentals: 10-K unavailable offline ({e})")
            ws.note("[fundamentals] WARNING: no 10-K in cache/fixtures — narrative findings limited to profile summary")
        except Exception as e:  # noqa: BLE001
            ws.errors.append(f"fundamentals: EDGAR failed: {e}")
            ws.note(f"[fundamentals] WARNING: EDGAR failed ({type(e).__name__}); continuing without filing text")
        if idx is not None:
            ws.facts["_filing_index"] = idx  # kept for focused follow-up questions (not serialised)

        # ---- LLM: turn evidence into cited claims ------------------------------------------
        m = metrics
        prompt = f"""Company: {snap.get('name')} ({ws.ticker}), sector {snap.get('sector')}, industry {snap.get('industry')}.
Business summary [source {s_fin}]: {snap.get('summary')}

Computed metrics [source {s_fin}] (already verified arithmetic — quote them, do not recompute):
- revenue growth YoY {self._fmt_pct(m['revenue_growth_yoy_pct'])} (prior year {self._fmt_pct(m['revenue_growth_prev_yoy_pct'])}); 3y CAGR {self._fmt_pct(m['revenue_cagr_3y_pct'])}
- gross margin {self._fmt_pct(m['gross_margin_pct'], False)}, operating margin {self._fmt_pct(m['operating_margin_pct'], False)}
- Rule of 40: {('%.0f' % m['rule_of_40']) if m['rule_of_40'] is not None else 'n/a'} (applies: {r40_applies})
- free cash flow {self._fmt_money(m['free_cash_flow'])} ({self._fmt_pct(m['fcf_growth_pct'])} y/y)
- total debt {self._fmt_money(m['total_debt'])}, cash {self._fmt_money(m['cash'])}, net cash {self._fmt_money(m['net_cash'])}
- debt/equity {m['debt_to_equity'] if m['debt_to_equity'] is None else round(m['debt_to_equity'], 2)}, interest coverage {m['interest_coverage'] if m['interest_coverage'] is None else round(m['interest_coverage'], 1)}x
- trailing P/E {m['trailing_pe']}, forward P/E {m['forward_pe']}

10-K passages (each tagged with its source id):
{chr(10).join(passages) if passages else '(no filing text available — rely on the summary and metrics only, and say so)'}

Write 5-8 concise analyst findings as JSON: {{"findings": [{{"claim": "...", "sources": [ids]}}], "business_model": "one sentence", "moat": "one sentence or 'unclear'", "risk_themes": ["short phrase", ...]}}.
Cover: what the business does and its mix; growth trajectory (accelerating/decelerating and why); margins and cash generation; balance-sheet health (debt, liquidity); the 2-3 most material risks the 10-K itself emphasises (note if any risk language appears new or unusually specific); and anything in the filing that supports OR undercuts the growth story. Each claim cites the source ids that support it. Do not speculate beyond the evidence."""
        out = self.llm.complete_json(prompt, system=ANALYST_SYSTEM, tier="strong", max_tokens=1800)
        n = self._add_claims(ws, out.get("findings", []), [s_fin])
        ws.facts["semantic_update"] = {k: out.get(k) for k in ("business_model", "moat", "risk_themes")}
        ws.note(f"[fundamentals] {n} cited findings")
