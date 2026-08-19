# The Second Opinion

*The screener finds them. This does the homework.*

A multi-agent equity research crew that takes a ticker (or company name) and produces a **one-page, fully cited
due-diligence report**: sourced metrics, a bull case and an equally-evidenced bear case, a 12-month scenario
valuation with a transparent weighted view, a sentiment cross-check, and a "since last analysis" diff from memory.

Built as the final project for *Building Agentic AI Systems*. Sequel to the midterm, *The Patient Screener*.

> **Not investment advice.** This is a research-synthesis tool that reads public sources and cites them.

---

## What it does (60-second version)

```
python -m second_opinion NVDA
```

1. **Guardrails** check the request (advice-seeking prompts like "should I buy?" are refused with a redirect).
2. A **router** classifies it: full report vs. focused question (`"NVDA — what's their debt situation?"`).
3. Three **analyst agents run in parallel** — Fundamentals (10-K RAG + statements), News, Sentiment (analyst
   consensus + StockTwits/Reddit posts classified by a fast model) — writing cited findings to a **shared workspace**.
4. The **Valuation agent** builds bear/base/bull scenarios (calculator does the math; multiples from the stock's own
   P/E history) and assigns evidence-weighted probabilities from a neutral 25/50/25 prior.
5. The **Writer** assembles the report; the **Risk Critic** attacks it (weak bear case? uncited numbers? unjustified
   weights?) and forces revisions, up to 2 rounds.
6. **Output guardrails** verify citation coverage and the disclaimer; the report is saved to `reports/`, and
   **episodic + semantic memory** are updated so the next run on that ticker shows what changed.

Everything is observable: the CLI streams the agent log live.

## Patterns implemented (course requirement: ≥4)

| # | Pattern | Where |
|---|---|---|
| 1 | Routing + parallelization | `router.py`; `orchestrator.py` thread-pool fan-out |
| 2 | Reflection / self-critique | `agents/critic.py` + writer revision loop (also the eval ablation) |
| 3 | Tool use (6 tools) | `tools/` — yfinance, SEC EDGAR, news, StockTwits, Reddit, calculator |
| 4 | Multi-agent with handoffs | 6 agents over a shared workspace (`memory/workspace.py`) |
| 5 | Memory (3 types) | working (workspace), episodic (`memory/episodic.py`), semantic (`memory/semantic.py`) |
| 6 | RAG with citations | `rag/index.py` over 10-K Items 1 / 1A / 7 |
| 7 | Guardrails + eval harness | `guardrails.py`; `eval/` |

Deliberately **not** implemented: LLM-driven planning (ReAct). The workflow has a known shape, so a deterministic
orchestrator is more reliable and testable. See `docs/architecture.md`.

**LLM provider:** Anthropic Claude — `claude-sonnet-5` for analysis/writing/critique, `claude-haiku-4-5` for routing,
guardrail checks and sentiment classification (cost-aware two-tier design).

---

## Setup (Windows, from scratch)

Prerequisites: **Python 3.12** (3.10+ works; 3.14 is too new for some deps), **Git**, and an **Anthropic API key**.

```powershell
# 1. get the code
git clone https://github.com/DavonCrane/second-opinion.git
cd second-opinion

# 2. create and activate a virtual environment (Python 3.12)
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
#   (if PowerShell blocks scripts: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned, then retry)

# 3. install  (the [rag] extra adds ChromaDB + MiniLM embeddings; without it a TF-IDF retriever is used)
pip install -e ".[rag]"

# 4. secrets — copy the template and edit it (never commit .env)
copy .env.example .env
notepad .env        # paste your ANTHROPIC_API_KEY, set EDGAR_IDENTITY="Your Name you@example.com"

# 5. run the tests (fully offline — no key or network needed)
pytest

# 6. run it
python -m second_opinion NVDA
```

macOS/Linux: same steps with `python3.12 -m venv .venv && source .venv/bin/activate` and `cp .env.example .env`.

### Optional providers
- **Retail sentiment** comes from the public StockTwits symbol stream by default (no key needed; user-declared
  Bullish/Bearish labels are kept, the rest is classified by the fast model). **Reddit** is an optional second source:
  create a "script" app at https://www.reddit.com/prefs/apps and fill `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET`.
  If no retail source is reachable, sentiment falls back to analyst consensus only (and the report says so).
- **Finnhub news:** free key at https://finnhub.io → `FINNHUB_API_KEY`. Without it, yfinance headlines are used.

## Dashboard (optional UI)

```powershell
streamlit run app.py
```

Opens a local web page: type a ticker or company name (or a focused question), watch the agents work in a live log,
read the report, download it as Markdown/HTML/PDF, expand the Risk Critic's verdicts, and ask a follow-up. The
sidebar's **Risk Critic** toggle is the same switch the eval uses for the ablation; **Research history** is the
episodic memory. `SO_FAKE_LLM=1 streamlit run app.py` runs a rehearsal with the scripted model and fixture data
(no key, no network) — handy for demos.

### Deploying the dashboard as a live web page (Streamlit Community Cloud)

1. Push the repo to GitHub (public). 2. Go to https://share.streamlit.io → **New app** → pick the repo, branch `main`,
main file `app.py`. 3. In **Advanced settings → Secrets**, paste:
```toml
ANTHROPIC_API_KEY = "sk-ant-..."
EDGAR_IDENTITY = "Your Name you@example.com"
APP_PASSWORD = "choose-a-password"     # optional but recommended: visitors must enter it; every run bills your key
# optional
REDDIT_CLIENT_ID = ""
REDDIT_CLIENT_SECRET = ""
REDDIT_USER_AGENT = "second-opinion/0.1 by your_reddit_username"
FINNHUB_API_KEY = ""
```
4. Deploy. The cloud build uses `requirements.txt` (no ChromaDB — the TF-IDF retriever runs instead, which fits the
free tier's memory). Cold start ~1 min; the first run on a ticker fetches its 10-K. Set a monthly spend limit on your
Anthropic account before sharing the URL. `app.py` reads secrets into environment variables at startup, so the same
code runs locally (from `.env`) and in the cloud (from Secrets).

## Usage (CLI)

```powershell
python -m second_opinion NVDA                                   # full report
python -m second_opinion "nvidia"                              # company name is resolved to a ticker
python -m second_opinion "NVDA — what's their debt situation?" # focused question (routed, cheap path)
python -m second_opinion NVDA --pdf                            # also export a styled PDF (needs Chrome/Edge)
python -m second_opinion NVDA --no-critic                      # ablation: skip the Risk Critic
python -m second_opinion NVDA --offline                        # cache/fixtures only, no network
python -m second_opinion                                       # interactive prompt
```

Reports land in `reports/<TICKER>_<timestamp>.md`. Memory lives in `data/memory/`; the RAG index in `data/rag/`;
tool responses are cached in `data/cache/` (delete to force fresh pulls).

## Offline mode & fixtures

Every tool call is cached to disk. With `SO_OFFLINE=1` (or `--offline`) nothing touches the network — data comes
from `data/cache/` or the committed `fixtures/` (an illustrative NVDA dataset). Tests and the eval harness run this
way so results are reproducible. **Fixture numbers are illustrative, not live market data.**

## Repository layout

```
second-opinion/
├── README.md                    this file
├── app.py                       Streamlit dashboard (optional UI over the same orchestrator)
├── .env.example                 secrets template (placeholders only)
├── pyproject.toml / requirements.txt
├── src/second_opinion/
│   ├── __main__.py              CLI
│   ├── orchestrator.py          deterministic control flow (route → fan-out → valuation → write → critique → guard)
│   ├── router.py                full_report vs focused_question
│   ├── guardrails.py            input/output/weight guards, disclaimer
│   ├── llm.py                   Anthropic wrapper (two tiers, retries, JSON, cost ledger) + FakeLLM for tests
│   ├── cache.py                 disk cache / fixtures / offline mode
│   ├── report.py                markdown → styled HTML/PDF export
│   ├── agents/                  fundamentals, news, sentiment, valuation, writer, critic
│   ├── tools/                   market (yfinance), edgar, news, stocktwits, reddit, calculator
│   ├── memory/                  workspace (working), episodic, semantic
│   └── rag/                     10-K chunking + retrieval (Chroma/MiniLM, TF-IDF fallback)
├── tests/                       pytest suite (offline; end-to-end pipeline test included)
├── eval/                        eval harness, test cases, eval_report.md
├── docs/architecture.md         system diagram, design decisions, patterns map, limitations
├── fixtures/                    offline NVDA dataset (illustrative)
└── reports/                     generated reports (git-ignored)
```

## Tests

```powershell
pytest            # 27 tests: calculator math, guardrails, router, cache, memory, RAG, full pipeline e2e, ablation
```

## Evaluation

See `eval/eval_report.md` for methodology, the ≥10 test cases, metrics (task success, citation coverage,
scenario-weight stability, cost/latency), failure analysis, and the critic on/off ablation. Run with:

```powershell
python eval/run_eval.py            # writes eval/results/*.json and a summary table
```

## Secret handling

- Keys live only in `.env` (git-ignored). `.env.example` contains placeholders.
- `config.py` reads them via `python-dotenv`; nothing is hard-coded.
- Reports and caches never contain keys. Before pushing: `git status` should never show `.env`.

## Limitations (short list — full list in docs/architecture.md)

- P/E-based scenarios don't apply to loss-making companies (the report says so instead of forcing a number).
- Historical multiples use annual EPS as a step function and are clamped to 0.55–1.30× the current P/E.
- Foreign filers (20-F/40-F) aren't ingested yet; the fundamentals agent falls back to profile + statements.
- Scenario weights are measured for stability and evidence-linkage, **not** validated as forecasts.
