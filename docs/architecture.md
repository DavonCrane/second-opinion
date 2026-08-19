# Architecture — The Second Opinion

*The screener finds them. This does the homework.*

## 1. Problem and goal

Retail investors face an information problem, not a data problem. Everything needed to evaluate a company is
public — 10-K filings, financial statements, price history, analyst views, news — but synthesizing it takes hours per
ticker, and a single LLM prompt ("tell me about NVDA") produces a confident, uncited summary that may be stale,
wrong, or promotional. The bar this system has to clear is a Google search plus one chatbot call.

**The Second Opinion** takes a ticker or company name and produces a one-page due-diligence report in which every
factual claim is cited to a specific source (10-K paragraph, statement line, dated article, analyst dataset), the
bear case is forced to be as concrete as the bull case, sentiment is cross-examined against fundamentals, a
12-month view is derived from visible and disputable assumptions rather than an oracle number, and a re-run tells you
what changed since last time. It refuses to give buy/sell advice.

Five ways it beats the search-plus-chatbot baseline, each traceable to a component below: (1) primary sources cited
by paragraph (RAG); (2) sources cross-examined against each other (Sentiment agent + Writer prompt + Critic);
(3) a bear case held to the same evidentiary standard as the bull case (Writer prompt + Critic); (4) memory of past
analyses (episodic + semantic memory); (5) arithmetic done by code from stated assumptions, never by the model
(Calculator + Valuation agent + code-generated valuation note).

## 2. System diagram

```
                     ┌──────────────────────────────────────────────────────────────────────┐
 user query ────────►│ Input guard (regex + Haiku)  ─►  Router (heuristics → Haiku)          │
 "NVDA" /            │ refuse advice-seeking            full_report | focused_question        │
 "nvidia" /          └───────────────┬───────────────────────────────┬──────────────────────┘
 "NVDA — debt?"                      │                               │
                                     ▼                               ▼
                       resolve ticker · yfinance snapshot     snapshot + RAG retrieve (k=5)
                       load semantic memory                   ─► one cited answer (1 Sonnet call)
                                     │
            ┌────────────────────────┼────────────────────────┐        ThreadPoolExecutor
            ▼                        ▼                        ▼        (parallel fan-out)
   ┌────────────────┐      ┌────────────────┐      ┌──────────────────┐
   │ Fundamentals   │      │ News           │      │ Sentiment        │
   │ 10-K RAG (Item │      │ headlines →    │      │ analyst consensus│
   │ 1/1A/7) +      │      │ relevance      │      │ (yfinance) +     │
   │ statements via │      │ filter, dated  │      │ StockTwits/Reddit│
   │ calculator     │      │ findings       │      │ Haiku classifier │
   └───────┬────────┘      └───────┬────────┘      └────────┬─────────┘
           └────────────────────────┼────────────────────────┘
                                    ▼
                    ┌────────────────────────────────────┐
                    │ Shared workspace (working memory)  │  findings · numbered sources ·
                    │                                    │  facts · log · drafts · critiques
                    └───────────────┬────────────────────┘
                                    ▼
                    ┌────────────────────────────────────┐
                    │ Valuation agent                    │  bear/base/bull EPS assumptions (model)
                    │ multiples from own P/E history,    │  × implied prices (calculator)
                    │ weights from neutral 25/50/25      │  → weighted view + cited rationale
                    └───────────────┬────────────────────┘
                                    ▼
                    ┌────────────────────────────────────┐
                    │ Writer  ──►  Risk Critic           │  REJECT → revise (≤ 2 rounds) → APPROVE
                    └───────────────┬────────────────────┘
                                    ▼
                    ┌────────────────────────────────────┐
                    │ Output guard                       │  citation coverage ≥ 0.9 · dangling refs ·
                    │                                    │  forbidden phrasing · disclaimer
                    └───────────────┬────────────────────┘
                                    ▼
             report.md (+ optional PDF) · episodic memory (run) · semantic memory (durable facts)
```

## 3. Components

| Component | File(s) | Responsibility |
|---|---|---|
| CLI | `__main__.py` | `python -m second_opinion <query>`; flags `--no-critic`, `--pdf`, `--offline`, `--sequential`; streams the agent log |
| Orchestrator | `orchestrator.py` | Deterministic control flow: guard → route → resolve → parallel analysts → valuation → writer/critic loop → output guard → persist. One agent failing never kills a run (`_safe_run` records the error, the report notes it) |
| Router | `router.py` | `full_report` vs `focused_question`; bare tickers and "X — question?" are decided by heuristics, ambiguous requests by Haiku |
| Guardrails | `guardrails.py` | Input guard (advice refusal), output guard (citation coverage, dangling `[n]`, promissory phrasing, disclaimer), weight guard (tilt > 0.20 needs critic sign-off), citation normalisation |
| LLM wrapper | `llm.py` | Two tiers (`claude-sonnet-5` / `claude-haiku-4-5`), retries with backoff, robust JSON (cheap repairs + fast-model repair pass), token/cost ledger, `FakeLLM` for tests |
| Cache | `cache.py` | Every tool call cached to `data/cache/`; `SO_OFFLINE=1` serves cache/fixtures only; fixtures **never** served online |
| Tools | `tools/market.py`, `edgar.py`, `news.py`, `stocktwits.py`, `reddit.py`, `calculator.py` | yfinance (price, statements, analyst consensus, headlines), SEC EDGAR 10-K sections, Finnhub/yfinance news, StockTwits public stream (user-declared Bullish/Bearish labels), Reddit via PRAW (optional), pure-function financial math |
| Agents | `agents/fundamentals.py`, `news.py`, `sentiment.py`, `valuation.py`, `writer.py`, `critic.py` | Each `run(workspace)`; analysts write cited `Finding`s; writer renders the fixed template; critic returns APPROVE/REJECT with actionable issues |
| Memory | `memory/workspace.py`, `episodic.py`, `semantic.py` | Working (per-run blackboard), episodic (per-ticker run log → "since last analysis"), semantic (durable company facts read at start, updated at end) |
| RAG | `rag/index.py` | Chunk 10-K Items 1/1A/7 (220 words, 40 overlap) → ChromaDB + all-MiniLM-L6-v2 (TF-IDF fallback) → retrieve top-k, cite `Item 1A ¶7`; chunk IDs namespaced by filing period+hash, retrieval scoped to the current filing |
| Report export | `report.py` | Markdown is canonical; optional styled PDF via headless Chrome/Edge |
| Eval harness | `eval/run_eval.py`, `eval/cases/*.json` | 12 cases, three arms, metrics; see `eval/eval_report.md` |

## 4. Patterns used (and one deliberately not)

| # | Pattern | Where | Justification | Exercised by test |
|---|---|---|---|---|
| 1 | **Routing + parallelization** | `router.py`; `orchestrator.py` ThreadPool fan-out of three analysts | A bare ticker and "what's their debt situation?" need different workflows; the cheap path costs 9 ¢ / 22 s vs $0.11 / 60 s. Analysts are independent, so parallel fan-out is free wall-clock savings | `test_router_heuristics`, `test_focused_question_is_routed_and_cited` |
| 2 | **Reflection / self-critique** | `agents/critic.py` + writer revision loop, ≤ 2 rounds | The single highest-leverage quality lever for LLM-written research; also the eval ablation. See §5.4 for what the ablation actually showed | `test_full_report_end_to_end` (REJECT→APPROVE), `test_ablation_without_critic` |
| 3 | **Tool use (6 tools)** | `tools/` | The system must act on *live* data, not training data; every tool is cached and degrades gracefully | `test_cache_serves_fixture_offline_and_raises_on_miss`, `test_graceful_degradation_when_a_tool_fails` |
| 4 | **Multi-agent with handoffs** | 6 agents over the shared workspace | Specialist prompts beat one mega-prompt; the workspace makes handoffs explicit (numbered sources, typed findings) and inspectable | `test_full_report_end_to_end` |
| 5 | **Memory (3 types)** | working / episodic / semantic | Re-researching a ticker should recall prior findings and *show what changed*; durable facts (sector, moat, risk themes) shouldn't be re-derived every run | `test_episodic_memory_diff`, `test_semantic_memory_accumulates_risk_themes`, `test_rerun_produces_since_last_analysis_diff` |
| 6 | **RAG with citations** | `rag/index.py` over 10-K Items 1/1A/7 | Grounds the fundamentals narrative in the primary source; paragraph-level citations make hallucination measurable | `test_rag_chunks_and_retrieves_with_citations` |
| 7 | **Guardrails + evaluation harness** | `guardrails.py`; `eval/` | Reliability is the difference between a demo and a system; the harness turned up three real defects on its first live pass | `test_input_guard_*`, `test_output_guard_*`, `test_weight_guard`, `test_advice_seeking_is_refused` |

**Deliberately not implemented — LLM planning (ReAct / plan-and-execute).** The research workflow has a known,
fixed shape. A code-driven orchestrator is cheaper (no planning tokens), more reliable (no improvised tool loops),
fully testable offline, and produces an identical, observable trace every run. Planning would add flexibility the
task doesn't need at the cost of the properties it does.

## 5. Design decisions

### 5.1 Deterministic orchestrator, not an LLM planner
See above. Concretely: the orchestrator is ~150 lines of plain control flow; the entire pipeline runs end-to-end
offline in the test suite with a scripted `FakeLLM` in under ten seconds, which would be impossible with an agent
choosing its own steps.

### 5.2 Two-model tiering
Sonnet where judgment matters (analysis, writing, critique, scenario assumptions); Haiku for routing, guardrail
adjudication, JSON repair, and Reddit sentiment classification (batches of 20 posts). Measured: a full report costs
about $0.11; the focused-question path about $0.01. All routing/guard/classification calls together are a small
fraction of a run's cost, so the tiering keeps the high-volume calls cheap without touching quality-critical ones.

### 5.3 Citations are a hard guardrail, not a prompt suggestion
Every agent tags findings with numbered source IDs from the workspace; the writer must cite per sentence; a post-hoc
checker (`output_guard`) computes citation coverage over factual sentences and rejects references to non-existent
sources. The critic sees the same check. Measured live: 0.93 mean coverage across 12 cases. The check also caught
its own blind spot — the model once wrote `[source 1]` instead of `[1]`, scoring 0.0 — which led to citation
normalisation and a test.

### 5.4 Bounded reflection, and what the ablation showed
The critic loop is capped at two rounds to prevent writer/critic oscillation and cap cost. The ablation
(`eval/eval_report.md` §3.2, §5) found that on automated metrics the critic changes almost nothing (12/12 either
way, coverage within half a point) at ~24 % more cost. The reason is that the critic's rejections during development
were converted into writer-prompt rules, moving the quality upstream. Its remaining role is insurance and
observability: it still rejected 2 of 5 stability-arm drafts with concrete issues. This is documented as a finding,
not hidden.

### 5.5 No naked price predictions
The system never emits an ungrounded forecast. The Valuation agent proposes bear/base/bull EPS-growth assumptions
bracketed by analyst estimates and recent trend, each with a cited "what has to be true" rationale; multiples come
from the stock's own P/E history (per-fiscal-year EPS, last ~36 months) clamped to corridors around the current
multiple (bear 0.50–0.75×, base 0.75–1.05×, bull 1.00–1.30×); the **calculator** computes implied prices; Claude
assigns weights starting from a neutral 25/50/25 prior, moving weight only with cited evidence, and any tilt over
0.20 requires explicit critic sign-off (`weight_guard`). The weighted view is shown beside the analyst mean target,
and the sentence interpreting the table is generated by code from the numbers (after the model once got the
comparison backwards). Run-to-run weight variation is measured (max σ 0.031 over 5 runs) and labelled *stability*,
because self-consistency demonstrates precision, not accuracy.

### 5.6 Everything cached; everything runs offline
Every tool response is written to `data/cache/`. `SO_OFFLINE=1` (used by tests and available to the eval) never
touches the network. Fixtures (`fixtures/`, an illustrative NVDA dataset) are served **only** offline — an early
version served them online too and shadowed live data, which is how the RAG-contamination failure happened.

### 5.7 LLM chooses; code computes
Growth rates, margins, Rule of 40, debt ratios, scenario prices, weighted views, price-vs-scenario comparisons,
citation coverage — all computed by pure functions in `tools/calculator.py`, `writer.py`, and `guardrails.py`, all
unit-tested. The model supplies judgment (assumptions, weights, prose); it never supplies arithmetic.

## 6. Model choice and secret handling

**Provider:** Anthropic Claude — `claude-sonnet-5` (strong tier) and `claude-haiku-4-5` (fast tier), set in `.env`
(`SO_MODEL_STRONG`, `SO_MODEL_FAST`). Note: Claude-5-generation models reject the `temperature` parameter; the wrapper
does not send it.

**Secrets:** keys live only in `.env` (git-ignored, verified: `git ls-files` shows no `.env`); `.env.example`
carries placeholders; `config.py` reads via `python-dotenv`; nothing hard-coded; reports and caches never contain
keys. SEC EDGAR requires an identity string (`EDGAR_IDENTITY`). Optional: `REDDIT_CLIENT_ID/SECRET` (else
analyst-only sentiment, stated in the report), `FINNHUB_API_KEY` (else yfinance headlines).

## 7. Limitations

- **Valuation applicability.** P/E-based scenarios don't apply to loss-making companies; the report says so
  explicitly (`RIVN` case) rather than forcing a number. Historical multiples use annual EPS as a step function and are
  clamped; for hypergrowth names the bands are dominated by the corridor rather than history.
- **Filings.** Only 10-K Items 1/1A/7 are ingested; foreign filers (20-F/40-F) are not; 10-Q updates are not yet
  used, so filing-based evidence can lag by up to a year.
- **Data providers.** yfinance headlines are generic without a Finnhub key (the news agent is instructed not to
  stretch relevance); `totalCash` excludes marketable securities (fixed by using the cash + short-term investments
  line); analyst consensus counts depend on yfinance coverage.
- **Sentiment.** StockTwits is an unofficial public endpoint and may change; Reddit needs credentials; sample sizes can be
  small (confidence is reported). With no retail source reachable the system runs analyst-only and says so.
- **Weights and view.** Measured for stability and evidence-linkage, not validated as forecasts; the weighted view
  inherits variance from scenario EPS assumptions (±6 % over 5 runs).
- **Critic.** Binary verdict; a rubric score would let the ablation measure quality deltas.
- **Guard proxies.** Citation coverage measures presence and validity of references, not per-claim truth; spot-checked
  manually on three reports.
- **Concurrency.** Three analysts fan out in parallel; the writer/critic loop is sequential by design.

## 7a. Future work
- X/FinTwit as a third retail-sentiment source (paid API) alongside StockTwits and Reddit. `tools/stocktwits.py`
  is the template — a sibling tool plugs into the same Sentiment agent, which already reports sources separately and
  flags when they disagree.
- 10-Q ingestion so filing evidence is at most a quarter old; 20-F/40-F support for foreign filers.
- Rubric-scored critic (quality deltas instead of pass/fail) to make the ablation more sensitive.
- Deployed dashboard (Streamlit Community Cloud) with a spending cap and access password.

## 8. Failure log (development + eval), all fixed

1. `temperature` rejected by Claude 5 models (400) → not sent.
2. Fixtures shadowed live data online → fixtures offline-only.
3. Malformed JSON dropped an agent → cheap repairs + fast-model repair pass.
4. Writer returned different JSON keys → strict schema, alias normalisation, retry, findings fallback.
5. RAG index contaminated by fixture chunks (model flagged "non-reconciling figures") → filing-scoped chunk IDs and retrieval.
6. Model reversed a price-vs-scenario comparison → code generates that sentence.
7. Bear scenario not bearish for hypergrowth stock → bear EPS growth ≤ ⅓ base, bear multiple 0.50–0.75×.
8. Cash excluded $40 B marketable securities → cash + short-term investments.
9. Guard false positive on cautionary "guaranteed" trapped the critic loop → promissory-only pattern + test.
10. Focused answers wrote `[source n]` and truncated → citation normalisation, larger budget.
