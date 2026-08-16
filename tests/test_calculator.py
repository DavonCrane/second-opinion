import pytest

from second_opinion.tools import calculator as calc


def test_pct_and_cagr():
    assert calc.pct(211.0, 130.5) == pytest.approx(61.69, abs=0.01)
    assert calc.pct(5, 0) is None and calc.pct(None, 3) is None
    assert calc.cagr(211.0, 27.0, 3) == pytest.approx(98.5, abs=0.5)
    assert calc.cagr(-1, 2, 3) is None


def test_margins_and_rule_of_40():
    assert calc.margin(156.6, 211.0) == pytest.approx(74.2, abs=0.05)
    assert calc.rule_of_40(62.0, 58.1) == pytest.approx(120.1)
    assert calc.rule_of_40(None, 10) is None
    assert calc.rule_of_40_applies("Technology", "Semiconductors") is True
    assert calc.rule_of_40_applies("Financial Services", "Banks - Diversified") is False


def test_debt_ratios():
    assert calc.safe_ratio(9.7, 125.0) == pytest.approx(0.0776, abs=1e-3)
    assert calc.interest_coverage(123.0, -3.0) == pytest.approx(41.0)
    assert calc.interest_coverage(10, 0) is None


def test_scenario_table_math_and_weighting():
    scen = [calc.Scenario("bear", 10, 28), calc.Scenario("base", 25, 36), calc.Scenario("bull", 40, 44)]
    rows = calc.scenario_table(2.92, 128.44, scen)
    assert [r.implied_price for r in rows] == [pytest.approx(89.94, abs=0.01), pytest.approx(131.4, abs=0.01), pytest.approx(179.87, abs=0.01)]
    assert rows[0].upside_pct == pytest.approx(-30.0, abs=0.1)
    view = calc.weighted_view(rows, {"bear": 0.3, "base": 0.5, "bull": 0.2})
    assert view == pytest.approx(0.3 * 89.94 + 0.5 * 131.4 + 0.2 * 179.87, abs=0.05)
    with pytest.raises(ValueError):
        calc.scenario_table(-1.0, 100.0, scen)


def test_weights_normalise_and_tilt():
    assert calc.normalize_weights({"bear": 3, "base": 5, "bull": 2}) == pytest.approx({"bear": 0.3, "base": 0.5, "bull": 0.2})
    assert calc.normalize_weights({"bear": 0, "base": 0, "bull": 0}) == calc.NEUTRAL_WEIGHTS
    assert calc.weight_tilt({"bear": 0.30, "base": 0.50, "bull": 0.20}) == pytest.approx(0.05)
    assert calc.weight_tilt({"bear": 0.60, "base": 0.30, "bull": 0.10}) == pytest.approx(0.35)


def test_historical_multiple_bands():
    bands = calc.historical_multiple_bands([20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70])
    assert bands["bear"] < bands["base"] < bands["bull"]
    assert bands["base"] == 45
    assert calc.historical_multiple_bands([10, 20]) is None
