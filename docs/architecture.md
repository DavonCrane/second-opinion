# Architecture — The Second Opinion

> Status: **draft skeleton (Aug 16)** — to be completed Aug 20 per the project schedule. Sections marked TODO
> will be filled with final diagrams, measured numbers from `eval/`, and screenshots.

## 1. Problem
Retail investors face an information problem, not a data problem: everything needed to evaluate a company is
public (10-Ks, price data, news, analyst views) but synthesizing it takes hours per ticker, and a single LLM call
produces confident, uncited, frequently wrong summaries. The Second Opinion divides research among specialist
agents, grounds every claim in retrievable sources, and forces the draft through a sceptical critic.

The bar to beat is a Google search. Five ways the system clears it: primary sources cited by section; cross-
examination of sources against each other; a bear case forced to be as strong as the bull case; memory of past
analyses; and a 12-month view derived from visible, disputable assumptions rather than an oracle number.

## 2. System diagram

```
 user query ─► input guard ─► router ─► resolve ticker ─► load semantic memory
                                │
                 ┌──────────────┴──────────────┐
          full_report                     focused_question
                 │                              │
   snapshot (yfinance)                 snapshot + RAG retrieve
                 │                              │
   ┌─────────────┼─────────────┐          cited answer (1 call)
   ▼             ▼             ▼
Fundamentals   News        Sentiment        ← parallel (ThreadPool)
(10-K RAG +   (headlines   (analyst consensus
 statements)   filtered)    + Reddit/Haiku)
   └─────────────┴─────────────┘
                 ▼
          Shared workspace  (findings + numbered sources + facts)
                 ▼
          Valuation agent   (scenarios via calculator; evidence-weighted view)
                 ▼
          Writer ──► Risk Critic ──► (REJECT → revise, ≤2 rounds) ──► APPROVE
                 ▼
          Output guard (citation coverage, disclaimer, forbidden phrasing)
                 ▼
          report.md (+ optional PDF) · episodic + semantic memory updated
```

## 3. Components
| Component | File | Responsibility |
|---|---|---|
| Orchestrator | `orchestrator.py` | Deterministic control flow; parallel fan-out; critic loop; persistence |
| Router | `router.py` | full_report vs focused_question (heuristics → Haiku) |
| Guardrails | `guardrails.py` | input (advice refusal), output (citations/disclaimer), weight tilt |
| Agents | `agents/*.py` | fundamentals, news, sentiment, valuation, writer, critic |
| Tools | `tools/*.py` | yfinance, EDGAR, news, Reddit, calculator — all disk-cached |
| Memory | `memory/*.py` | workspace (working), episodic (runs), semantic (durable facts) |
| RAG | `rag/index.py` | 10-K chunking + retrieval with (item, chunk) citations |
| LLM | `llm.py` | Two-tier Anthropic wrapper, retries, JSON parsing, cost ledger, FakeLLM |

## 4. Patterns used (map)
See README table. TODO: expand each with a paragraph + pointer to the test that exercises it.

## 5. Design decisions (≥3)
1. **Deterministic orchestrator over LLM planning.** The workflow has a fixed shape; code-driven control is
   cheaper, testable, and observable. ReAct planning was deliberately not implemented.
2. **Two-model tiering.** Sonnet where judgment matters (analysis, writing, critique); Haiku for routing,
   guardrail adjudication, and sentiment classification. TODO: measured cost split from eval.
3. **Citations as a hard guardrail, not a prompt suggestion.** A post-hoc checker computes coverage and rejects
   references to non-existent sources; the critic sees the same check.
4. **Bounded reflection (≤2 rounds).** Prevents writer/critic oscillation; caps cost per run.
5. **No naked price predictions.** Scenario table with sourced assumptions; calculator does the arithmetic;
   Claude assigns visible weights from a neutral 25/50/25 prior with cited justification; large tilts require
   critic sign-off. Run-to-run weight stability is measured in the eval — labelled as *stability*, not accuracy.
6. **Everything cached, everything runs offline.** Reproducible demos/evals; fixtures for tests.

## 6. Model choice & secret handling
Anthropic Claude (`claude-sonnet-5`, `claude-haiku-4-5`), configurable via `.env`. Secrets only in `.env`
(git-ignored); `.env.example` has placeholders; nothing hard-coded. See README.

## 7. Limitations
- P/E scenarios don't apply to loss-making companies (declined explicitly).
- Historical multiples use annual EPS as a step function; bands clamped to 0.55–1.30× current P/E.
- Foreign filers (20-F/40-F) not ingested; RAG covers Items 1/1A/7 only.
- Reddit sample sizes can be small; confidence is reported.
- Weights are stable/evidence-linked by measurement, not validated as forecasts.
- TODO: add observed live-run limitations after Aug 17–19 testing.
