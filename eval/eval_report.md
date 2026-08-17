# Evaluation Report — The Second Opinion

*All numbers below are from live runs (real Claude calls, real market/SEC data) on Aug 16, 2026, using the code at
commit tagged in this repo. Raw outputs, per-case reports, and `summary.json` files are in `eval/results/`
(git-ignored locally; the three referenced run folders are preserved for reproducibility). Re-run any arm with the
commands in §1.*

## 1. Methodology

**Harness.** `eval/run_eval.py` executes every case in `eval/cases/*.json`, scores each run against the case's
expectations, and writes `eval/results/<stamp>/summary.{json,md}` plus each generated report. Memory (episodic and
semantic) is isolated per eval run so memory-dependent cases are deterministic. Three arms were run:

```
python eval/run_eval.py                                  # main arm (critic on)          → 20260816_182725
python eval/run_eval.py --no-critic                      # ablation arm (critic off)     → 20260816_184211_nocritic
python eval/run_eval.py --repeat 5 --cases weights_stability   # stability arm (5× NVDA)  → 20260816_185040
```

**Metrics.**

| Metric | Definition | Why it matters |
|---|---|---|
| **Task success rate** | A case passes only if *every* check passes: correct route (full report / focused question / refusal), all ten template sections present, every metric row rendered, output guardrail OK (citation coverage ≥ 90 %, no dangling `[n]`, disclaimer present, no advice phrasing), plus case-specific checks (e.g. "since last analysis" diff present on a re-run; scenario table correctly *absent* for a loss-making company). | The one number that says "did the system do its job end-to-end". |
| **Citation coverage** | Share of factual sentences (contain a number/%/$ or a factual verb) that end with at least one `[n]` mapping to a real source in the workspace. Computed by `guardrails.output_guard`. | Groundedness is the project's core promise; this makes hallucination *measurable*. |
| **Scenario-weight stability** | Over 5 independent runs of the same ticker, the largest standard deviation among the bear/base/bull weights. | Measures run-to-run *consistency* of Claude's evidence weighting — deliberately labelled stability, not accuracy (self-consistency demonstrates precision, not truth). |
| **Cost / latency** | Estimated USD from the token ledger; wall-clock seconds per run. | The ablation's other side of the ledger. |
| **Critic rounds / catch rate** | Number of writer→critic rounds; how often round 1 was REJECT. | Whether the reflection loop is actually doing work. |

**Baseline / ablation.** The ablation removes the Risk Critic (`--no-critic`): the writer's first draft ships
directly to the output guard. Everything else is identical.

## 2. Test cases (12)

| id | query | what it exercises | expected |
|---|---|---|---|
| NVDA_full | `NVDA` | happy path, full pipeline | full report, all sections |
| NVDA_by_name | `nvidia` | company name → ticker resolution | full report on NVDA |
| MSFT_full | `MSFT` | second large-cap; Rule of 40 applies | full report |
| JPM_bank | `JPM` | bank: different filing structure; Rule of 40 must be N/A | full report, "not a recurring-revenue business model" |
| recent_ipo | `RDDT` | short filing history (2024 IPO) | full report; scenario section degrades gracefully if no EPS |
| loss_making | `RIVN` | negative EPS | full report; scenario table replaced by explicit "not computed" |
| invalid_ticker | `ZZZZ` | bad input | clean error, no stack trace |
| focused_debt | `NVDA — what's their debt situation?` | router → cheap path; RAG answer | focused_question mode, cited answer, ≤ 3 LLM calls |
| advice_refused | `Should I buy NVDA?` | input guardrail | refused with redirect, zero cost |
| rerun_memory | `NVDA` after a prior run | episodic memory | "since last analysis" diff present |
| news_outage | `NVDA` with news provider broken | graceful degradation | full report, degradation noted |
| weights_stability | `NVDA` ×5 | weight consistency | std-dev reported |

## 3. Results

### 3.1 Main arm (critic on) — run `20260816_182725`

| case | mode | success | citation coverage | critic rounds | cost $ | latency s |
|---|---|---|---|---|---|---|
| NVDA_full | full_report | ✓ | 0.905 | 1 | 0.106 | 58 |
| NVDA_by_name | full_report | ✓ | 0.950 | 1 | 0.098 | 53 |
| MSFT_full | full_report | ✓ | 0.947 | 1 | 0.099 | 57 |
| JPM_bank | full_report | ✓ | 0.941 | 1 | 0.101 | 63 |
| recent_ipo | full_report | ✓ | 0.905 | 1 | 0.113 | 69 |
| loss_making | full_report | ✓ | 0.941 | 1 | 0.086 | 54 |
| news_outage | full_report | ✓ | 0.905 | 1 | 0.107 | 66 |
| rerun_memory | full_report | ✓ | 0.950 | 1 | 0.202* | 60 |
| weights_stability | full_report | ✓ | 0.913 | 1 | 0.101 | 54 |
| focused_debt | focused_question | ✓ | 1.000 | 0 | 0.009 | 22 |
| advice_refused | refused | ✓ | — | 0 | 0.000 | 0 |
| invalid_ticker | error | ✓ (clean) | — | — | 0.000 | 1 |

\* includes the seeding run that populates memory.

**Task success 12/12 (100 %) · mean citation coverage 0.936 (full reports 0.929) · mean cost per full report $0.11 ·
mean latency 60 s.** Every full report passed the output guard on the first attempt; the router sent the focused
question down the cheap path (1 LLM call, 9 ¢, 22 s vs. ~$0.11 and 60 s for a full report); the advice request was
refused before any model call; the invalid ticker produced a one-line error.

### 3.2 Ablation — Risk Critic off — run `20260816_184211_nocritic`

| case | success | citation coverage | cost $ | latency s |
|---|---|---|---|---|
| NVDA_full | ✓ | 0.944 | 0.084 | 54 |
| NVDA_by_name | ✓ | 0.947 | 0.087 | 50 |
| MSFT_full | ✓ | 0.944 | 0.078 | 47 |
| JPM_bank | ✓ | 0.944 | 0.069 | 42 |
| recent_ipo | ✓ | 0.900 | 0.081 | 50 |
| loss_making | ✓ | 0.947 | 0.069 | 40 |
| news_outage | ✓ | 0.900 | 0.075 | 44 |
| rerun_memory | ✓ | 0.900 | 0.150 | 43 |
| weights_stability | ✓ | 0.900 | 0.069 | 44 |
| focused_debt / advice_refused / invalid_ticker | ✓ | as above | — | — |

| | critic ON | critic OFF | Δ |
|---|---|---|---|
| Task success | 12/12 | 12/12 | 0 |
| Mean citation coverage (full reports) | 0.929 | 0.925 | −0.004 |
| Mean cost per full report | $0.112 | $0.085 | **−24 %** |
| Mean latency per full report | 59.5 s | 46.0 s | **−23 %** |
| Bear-case specificity (numbers + citations per bear bullet, ratio to bull) | 0.86 avg | 1.05 avg | no degradation |

### 3.3 Stability — 5 independent NVDA runs — run `20260816_185040`

| rep | bear | base | bull | weighted view | critic rounds |
|---|---|---|---|---|---|
| 0 | 0.25 | 0.50 | 0.25 | $439 | 1 |
| 1 | 0.25 | 0.50 | 0.25 | $456 | 2 (REJECT → revise → APPROVE) |
| 2 | 0.22 | 0.50 | 0.28 | $464 | 1 |
| 3 | 0.30 | 0.48 | 0.22 | $404 | 2 (REJECT → revise → APPROVE) |
| 4 | 0.30 | 0.50 | 0.20 | $402 | 1 |

**Max weight std-dev 0.031** (bear 0.22–0.30, base 0.48–0.50, bull 0.20–0.28). The weighted 12-month view varied
more — mean $433, σ $26, range $402–$464 (±6 %) — because the *EPS-growth assumptions* the model proposes per
scenario also vary run to run, and that flows through the arithmetic. Interpretation: the evidence-weighting step is
stable to within a few points; the dollar output inherits additional variance from scenario inputs. Neither number
says anything about forecast accuracy, and the report labels the view accordingly.

## 4. Failure analysis

Ten failures were observed across development and evaluation; all were fixed and are pinned by tests where
practical. The three below are the most instructive (full list in `docs/architecture.md` §7 and this repo's history).

**F1 — RAG index contaminated by fixture data (caught by the model itself).** The very first live run happened while
fixture (illustrative) 10-K text was still being served alongside live data; its chunks were indexed with the same
IDs as the real filing's, so later real chunks were skipped and the fundamentals agent retrieved *fixture*
paragraphs. The generated report said the MD&A figures "do not reconcile" with computed metrics — Claude noticed the
inconsistency before I did. Root cause: chunk IDs were `Item 7#3` with no document scope. Fix: IDs are namespaced by
filing period + content hash and retrieval is filtered to the current document (`rag/index.py`); fixtures are only
served in offline mode (`cache.py`). Lesson: test data must be walled off from production paths, and a critic-minded
model is a useful smoke alarm.

**F2 — Guardrail false positive costs two revision rounds.** In the first eval pass, `recent_ipo` failed the output
guard because the report contained "no *guaranteed* follow-through" — a cautionary phrase — and the forbidden-phrase
pattern matched the bare word. Because the critic treats a guard failure as REJECT, the system spent two revision
rounds trying to satisfy a check the writer could not see. Fix: the pattern now matches only promissory usage
(`is guaranteed`, `guaranteed returns` …), pinned by `tests/test_guardrails_router.py`. Lesson: over-eager guardrails
are not free — they cost money and can trap the reflection loop.

**F3 — The model got a comparison backwards.** An early live report's valuation note read "the current price sits
below all three scenario outcomes, including the bear case" while the table showed bear $184 < price $225. The
critic missed it. Fix: that sentence is now generated by code from the scenario numbers (`WriterAgent._valuation_note`).
Design principle adopted project-wide: *the LLM chooses assumptions; code does arithmetic and comparisons.*

Also observed and fixed: Claude-5-generation models reject the `temperature` parameter (400 error); malformed JSON
from the model (unescaped quote) dropped an agent → cheap repairs + a fast-model repair pass; the writer returned
JSON under different key names → strict schema, alias normalisation, retry, findings-based fallback so no section
can ship empty; historical-P/E bands produced a 247× "bull" multiple for a hypergrowth stock → per-fiscal-year EPS
and clamped corridors, bear EPS growth capped at ⅓ of base; yfinance `totalCash` excluded $40 B of marketable
securities → use "cash + short-term investments"; focused answers wrote `[source 1]` and truncated → citation
normalisation and a larger token budget; the `loss_making` eval case wrongly required a scenario table (a spec bug —
the system had behaved correctly).

## 5. Ablation discussion — what the Risk Critic is worth

On the automated metrics the critic changed almost nothing: identical task success, coverage within half a point,
bear-case specificity no worse without it — at 24 % lower cost and 23 % lower latency. Every main-arm case approved
on round 1.

That is an honest result and it has a clear explanation. During development the critic *did* reject drafts and
name real problems — a bear bullet that read only "the stock is expensive", a bear case vaguer than the bull case, a
missing cross-check. Each time, the fix was to harden the **writer's** prompt ("bear must be as specific and
evidenced as bull", "never cite the scenario table as a bull/bear reason", strict schema). The critic's lessons
migrated upstream into the prompt it helped design, so first drafts are now usually good enough to pass it. The
remaining value is **insurance**: in the stability arm it still rejected round 1 in 2 of 5 runs and forced concrete
revisions; across the day its catch rate went from roughly 4 in 10 drafts before prompt hardening to about 2 in 14
after. It also makes the pipeline observable — every verdict and issue list is logged.

Recommendation stated in the report: keep the critic on by default for a research product where a bad report is
costly, and expose `--no-critic` for cheap bulk screening. Future work: replace the binary verdict with a rubric
score so the ablation can measure *quality deltas* rather than pass/fail, and hand-label a sample of bear cases to
validate the specificity proxy.

## 6. Limitations of this evaluation

- 12 cases and one market day; results will drift with news flow and data-provider behaviour (yfinance headlines
  are generic without a Finnhub key; Reddit sentiment was not configured, so retail sentiment ran in analyst-only mode).
- Citation coverage measures *presence* of citations, spot-checked for accuracy on NVDA/JPM/MSFT reports; it does not
  verify every claim against its source automatically.
- Weight stability measures consistency, not calibration; validating the weights as forecasts would need a multi-year
  horizon.
- Cost figures use list prices from the token ledger, not billed invoices.
