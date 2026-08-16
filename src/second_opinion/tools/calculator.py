"""Deterministic financial calculator.

Design decision: all arithmetic in the report goes through these functions, never through the LLM.
The model may choose *assumptions*; it may not do the *math*. Every function is pure and unit-tested.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict


def pct(a: float | None, b: float | None) -> float | None:
    """Percentage change from b to a, i.e. (a-b)/|b|. None if not computable."""
    if a is None or b in (None, 0):
        return None
    return (a - b) / abs(b) * 100.0


def cagr(end: float | None, start: float | None, years: float) -> float | None:
    if end is None or start is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1.0 / years) - 1.0) * 100.0


def margin(numerator: float | None, revenue: float | None) -> float | None:
    if numerator is None or revenue in (None, 0):
        return None
    return numerator / revenue * 100.0


def rule_of_40(revenue_growth_pct: float | None, profit_margin_pct: float | None) -> float | None:
    """Growth % + profit margin %. Meaningful mainly for recurring-revenue/software businesses."""
    if revenue_growth_pct is None or profit_margin_pct is None:
        return None
    return revenue_growth_pct + profit_margin_pct


def rule_of_40_applies(sector: str | None, industry: str | None) -> bool:
    text = f"{sector or ''} {industry or ''}".lower()
    return any(k in text for k in ("software", "internet", "saas", "semiconductor", "information technology",
                                   "technology", "communication services", "interactive media"))


def safe_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def interest_coverage(ebit: float | None, interest_expense: float | None) -> float | None:
    if ebit is None or interest_expense in (None, 0):
        return None
    return ebit / abs(interest_expense)


@dataclass
class Scenario:
    name: str
    eps_growth_pct: float
    multiple: float
    rationale: str = ""

    def implied_price(self, eps: float) -> float:
        return eps * (1 + self.eps_growth_pct / 100.0) * self.multiple


@dataclass
class ScenarioResult:
    name: str
    eps_growth_pct: float
    multiple: float
    implied_price: float
    upside_pct: float
    rationale: str


def scenario_table(eps: float, price: float, scenarios: list[Scenario]) -> list[ScenarioResult]:
    """Implied 12-month price for each scenario: EPS × (1+growth) × multiple."""
    if eps is None or eps <= 0:
        raise ValueError("Scenario valuation requires positive trailing EPS (loss-making companies need a different method).")
    out = []
    for s in scenarios:
        p = s.implied_price(eps)
        out.append(ScenarioResult(s.name, s.eps_growth_pct, s.multiple, round(p, 2), round(pct(p, price) or 0.0, 1), s.rationale))
    return out


NEUTRAL_WEIGHTS = {"bear": 0.25, "base": 0.50, "bull": 0.25}
MAX_TILT = 0.20  # beyond this deviation from neutral, the critic must explicitly sign off


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    total = sum(max(0.0, float(w)) for w in weights.values())
    if total <= 0:
        return dict(NEUTRAL_WEIGHTS)
    return {k: max(0.0, float(v)) / total for k, v in weights.items()}


def weighted_view(results: list[ScenarioResult], weights: dict[str, float]) -> float:
    w = normalize_weights(weights)
    return round(sum(r.implied_price * w.get(r.name.lower(), 0.0) for r in results), 2)


def weight_tilt(weights: dict[str, float]) -> float:
    """Largest absolute deviation from the neutral prior — used by the critic and the eval."""
    w = normalize_weights(weights)
    return max(abs(w.get(k, 0.0) - v) for k, v in NEUTRAL_WEIGHTS.items())


def historical_multiple_bands(pe_history: list[float]) -> dict[str, float] | None:
    """Bear/base/bull multiples from the stock's own P/E history: ~10th pct, median, ~90th pct."""
    clean = sorted(x for x in pe_history if x is not None and 0 < x < 500)
    if len(clean) < 5:
        return None
    n = len(clean)
    q = lambda f: clean[min(n - 1, max(0, int(round(f * (n - 1)))))]
    return {"bear": round(q(0.10), 1), "base": round(q(0.50), 1), "bull": round(q(0.90), 1)}


def to_dicts(results: list[ScenarioResult]) -> list[dict]:
    return [asdict(r) for r in results]
