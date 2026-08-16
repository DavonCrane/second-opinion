"""Valuation agent: bear/base/bull 12-month scenarios + Claude's weighted view.

Design decision (documented in docs/architecture.md): the system never emits a naked price prediction.
  - Multiples come from the stock's OWN 5-year P/E history (bear≈10th pct, base=median, bull≈90th pct).
  - EPS growth assumptions are proposed by the model but must be bracketed by analyst estimates / recent
    growth, and every assumption carries a one-line rationale with citations.
  - Arithmetic is done by the calculator, not the model.
  - Weights start from a neutral prior (25/50/25); any tilt >=5pts must cite evidence; tilt >20pts needs
    critic sign-off (weight_guard). Weights are recomputed every run from the workspace evidence.
"""
from __future__ import annotations

from ..memory.workspace import Workspace
from ..tools import calculator as calc
from .base import Agent, ANALYST_SYSTEM


class ValuationAgent(Agent):
    name = "valuation"
    section = "valuation"

    @staticmethod
    def _pe_history(snap: dict, current_eps: float) -> list[float]:
        """Monthly P/E over the last ~4 fiscal years using EACH YEAR'S diluted EPS (not today's).

        monthly_closes_5y is oldest->newest. Fiscal-year EPS list is newest->oldest. We walk backwards from the
        latest month assigning ~12 months per fiscal year. Falls back to current EPS only for the most recent
        months beyond the last reported year. Documented limitation: annual EPS is a step function.
        """
        closes = [c for c in (snap.get("monthly_closes_5y") or []) if c]
        eps_by_year = [e for e in (snap.get("statements") or {}).get("diluted_eps", []) if e]
        if not closes:
            return []
        if not eps_by_year:
            return [c / current_eps for c in closes[-24:]]
        out: list[float] = []
        # newest month first; months since fiscal year end are ~ (0..11) -> use eps_by_year[0]... approximate as 12/yr
        for i, close in enumerate(reversed(closes)):
            yr_idx = min(i // 12, len(eps_by_year) - 1)
            e = eps_by_year[yr_idx]
            if e and e > 0:
                out.append(close / e)
        return out

    def run(self, ws: Workspace) -> None:
        snap = ws.facts["snapshot"]
        m = ws.facts.get("metrics", {})
        price, eps = snap.get("price"), snap.get("trailing_eps")
        s_val = ws.add_source(f"Scenario valuation: calculator over yfinance price/EPS history ({ws.created_at[:10]}); "
                              f"multiples from {ws.ticker}'s own 5-year P/E range")
        if not price or not eps or eps <= 0:
            ws.facts["scenarios"] = None
            ws.add_finding(self.name, self.section, "Scenario valuation not computed: company has no positive trailing "
                           "EPS (P/E-based scenarios don't apply to loss-making companies).", s_val)
            ws.note("[valuation] skipped — no positive EPS")
            return

        # ---- multiple bands from own history --------------------------------------------------
        pe_hist = self._pe_history(snap, eps)[:36]          # last ~3 fiscal years
        cur_pe = snap.get("trailing_pe") or (price / eps)
        raw = calc.historical_multiple_bands(pe_hist)
        # Bands come from the stock's own history but are clamped to a sane corridor around today's multiple, so a
        # low-earnings year (hypergrowth names) can't produce a 250x "bull" multiple. Documented limitation.
        bands = {
            "bear": round(min(max((raw or {}).get("bear", cur_pe * 0.65), cur_pe * 0.55), cur_pe * 0.90), 1),
            "base": round(min(max((raw or {}).get("base", cur_pe * 0.85), cur_pe * 0.70), cur_pe * 1.05), 1),
            "bull": round(min(max((raw or {}).get("bull", cur_pe), cur_pe * 0.95), cur_pe * 1.30), 1),
        }
        bands_note = f"from own P/E history (n={len(pe_hist)} months), clamped to 0.55–1.30× current P/E" if raw else "no usable P/E history — bands set relative to current P/E"
        # ---- growth brackets: analyst-implied and recent history --------------------------------
        an = ws.facts.get("analyst", {})
        fwd_eps = snap.get("forward_eps")
        analyst_eps_growth = calc.pct(fwd_eps, eps) if fwd_eps else None
        rev_yoy = m.get("revenue_growth_yoy_pct")
        evidence = ws.findings_text()
        prompt = f"""Ticker {ws.ticker}. Current price ${price}, trailing EPS ${eps}, trailing P/E {snap.get('trailing_pe')}.
Multiple bands ({bands_note}, calculator): bear {bands["bear"]}x, base {bands["base"]}x, bull {bands["bull"]}x [source {s_val}].
Analyst-implied forward EPS growth: {self._fmt_pct(analyst_eps_growth)}; latest revenue growth {self._fmt_pct(rev_yoy)}; analyst mean target ${an.get('target_mean')}.

Evidence gathered so far by the other analysts (with source ids):
{evidence}

Task 1 — propose EPS growth for the next 12 months under three scenarios. Bracket them by the analyst-implied growth and recent trend; bear must be clearly below, bull clearly above, base near the evidence. Each needs a one-line 'what has to be true' rationale citing source ids from the evidence.
Task 2 — assign probability weights to the three scenarios. START from the neutral prior bear 0.25 / base 0.50 / bull 0.25. Move weight ONLY where specific evidence justifies it, and explain each move in one sentence citing sources. Keep total tilt modest (max any single weight moves: 0.20) unless the evidence is extreme.
JSON: {{"scenarios": {{"bear": {{"eps_growth_pct": n, "rationale": "...", "sources": [ids]}}, "base": {{...}}, "bull": {{...}}}},
        "weights": {{"bear": 0.xx, "base": 0.xx, "bull": 0.xx}}, "weight_rationale": "one or two sentences with citations"}}"""
        out = self.llm.complete_json(prompt, system=ANALYST_SYSTEM, tier="strong", max_tokens=900)
        sc_in = out.get("scenarios", {})
        scenarios = []
        for name in ("bear", "base", "bull"):
            s = sc_in.get(name, {})
            try:
                g = float(s.get("eps_growth_pct"))
            except (TypeError, ValueError):
                g = {"bear": 0.0, "base": analyst_eps_growth or 10.0, "bull": (analyst_eps_growth or 10.0) * 1.5}[name]
            scenarios.append(calc.Scenario(name, g, bands[name], str(s.get("rationale", ""))))
        results = calc.scenario_table(eps, price, scenarios)
        weights = calc.normalize_weights(out.get("weights") or calc.NEUTRAL_WEIGHTS)
        view = calc.weighted_view(results, weights)
        ws.facts["scenarios"] = {"eps": eps, "price": price, "bands": bands, "rows": calc.to_dicts(results), "bands_note": bands_note,
                                 "weights": {k: round(v, 2) for k, v in weights.items()},
                                 "weight_rationale": out.get("weight_rationale", ""), "claude_view": view,
                                 "tilt": round(calc.weight_tilt(weights), 2), "analyst_mean_target": an.get("target_mean")}
        for r in results:
            src_ids = [int(i) for i in (sc_in.get(r.name, {}).get("sources") or []) if str(i).isdigit()] or [s_val]
            ws.add_finding(self.name, self.section,
                           f"{r.name.title()} scenario: EPS {r.eps_growth_pct:+.0f}% at {r.multiple}x implies ${r.implied_price} "
                           f"({r.upside_pct:+.0f}%) — {r.rationale}", s_val, *[i for i in src_ids if i != s_val])
        ws.add_finding(self.name, self.section,
                       f"Weighted 12-month view ${view} using weights bear {weights['bear']:.2f} / base {weights['base']:.2f} / "
                       f"bull {weights['bull']:.2f} (neutral prior 0.25/0.50/0.25). {out.get('weight_rationale','')}", s_val)
        ws.note(f"[valuation] scenarios {[(r.name, r.implied_price) for r in results]} weights {ws.facts['scenarios']['weights']} view ${view}")
