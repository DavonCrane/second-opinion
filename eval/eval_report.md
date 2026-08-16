# Evaluation Report — The Second Opinion

> Status: **skeleton (Aug 16)** — numbers below are from the offline smoke run with the scripted FakeLLM and
> exist only to prove the harness works. Live results (real Claude calls, real market data) will replace them
> on Aug 19 per the schedule.

## Methodology
- Harness: `eval/run_eval.py` runs every case in `eval/cases/*.json`, scores it, writes `eval/results/<stamp>/`.
- Isolated memory per eval run so memory cases are deterministic.
- Metrics: **task success rate** (all template sections present + every check in the case passes + output guard ok),
  **citation coverage** (% of factual sentences with a valid [n]), **scenario-weight stability** (max std-dev of
  weights over `--repeat 5`; a *consistency* metric, not accuracy), **cost/latency**, **critic rounds**.
- Ablation: identical suite with `--no-critic`.

## Test cases (12)
| id | query | what it tests |
|---|---|---|
| NVDA_full | NVDA | happy path, full pipeline |
| NVDA_by_name | nvidia | name → ticker resolution |
| MSFT_full | MSFT | second large-cap (Rule of 40 applies) |
| JPM_bank | JPM | bank filing structure; Rule of 40 N/A |
| recent_ipo | RDDT | thin filing history / EPS edge case |
| invalid_ticker | ZZZZ | clean failure |
| focused_debt | NVDA — what's their debt situation? | routing to cheap path; cited answer |
| advice_refused | Should I buy NVDA? | input guardrail |
| rerun_memory | NVDA (after a prior run) | since-last-analysis diff |
| news_outage | NVDA with broken news provider | graceful degradation |
| loss_making | RIVN | scenario valuation declines politely |
| weights_stability | NVDA ×5 | weight std-dev |

## Results (offline smoke, FakeLLM — placeholder)
| case | success | coverage | critic rounds |
|---|---|---|---|
| NVDA_full | ✓ | 0.94 | 2 (REJECT → APPROVE) |
| NVDA_by_name | ✓ | 0.94 | 2 |
| focused_debt | ✓ | 1.00 | 0 |
| advice_refused | ✓ | — | 0 |
| rerun_memory | ✓ | 0.94 | 2 |
| invalid_ticker | ✓ (clean error) | — | — |

Task success 6/6 · mean coverage 0.95. TODO: live table for all 12 cases.

## Failure analysis (3 examples) — TODO after live runs
Candidates to watch for: citation drift on paraphrased claims; critic over-triggering revisions; stale-data
confusion between fiscal-year and TTM figures; Reddit sample too small.

## Ablation: Risk Critic on vs off — TODO live numbers
Offline smoke already shows the mechanism: with the critic on, the vague bear bullet is rejected and revised;
with `--no-critic` it ships unfixed (see `tests/test_pipeline_e2e.py::test_ablation_without_critic`).
