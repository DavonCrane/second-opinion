"""The Second Opinion — Streamlit dashboard.

    streamlit run app.py

Thin UI over the same Orchestrator the CLI uses: type a ticker or company name (or a focused question), watch the
agents work in a live log, read the report, download it, ask a follow-up. The critic toggle in the sidebar is the
same switch the eval uses for the ablation.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import streamlit as st

# ---- cloud bootstrap: make `src/` importable without `pip install -e .`, and map Streamlit secrets -> env vars
sys.path.insert(0, str(Path(__file__).parent / "src"))
try:
    for _k, _v in st.secrets.items():
        if isinstance(_v, (str, int, float)) and _k not in os.environ:
            os.environ[_k] = str(_v)
except Exception:  # noqa: BLE001 — no secrets file locally is normal
    pass

from second_opinion.config import settings  # noqa: E402
from second_opinion.memory import EpisodicMemory
from second_opinion.report import export_pdf, to_html

st.set_page_config(page_title="The Second Opinion", page_icon="📊", layout="wide")

# ---- optional password gate (set APP_PASSWORD in Streamlit secrets / .env to enable) ---------------------------
_pw = os.getenv("APP_PASSWORD", "")
if _pw and not st.session_state.get("_authed"):
    st.markdown("### The Second Opinion")
    st.caption("This deployment is private. Enter the access password to continue.")
    given = st.text_input("Password", type="password")
    if given and given == _pw:
        st.session_state["_authed"] = True
        st.rerun()
    elif given:
        st.error("Incorrect password.")
    st.stop()

# ---------- styling ------------------------------------------------------------------------------------------
st.markdown("""
<style>
.brand{font-size:30px;font-weight:700;color:#16324f;letter-spacing:.6px;line-height:1.1;margin-top:-10px}
.brand:after{content:"";display:block;width:64px;height:3px;background:#b5893a;border-radius:2px;margin:8px 0 6px}
.tag{color:#6b7686;font-style:italic;margin-bottom:14px}
div[data-testid="stTextInput"] input{font-size:16px;padding:12px 14px;border-radius:8px}
div[data-testid="stButton"] button[kind="primary"]{border-radius:8px;font-weight:600;letter-spacing:.2px}
h1{font-size:2.0rem !important;color:#16324f !important;letter-spacing:-.2px}
h2{font-size:1.15rem !important;text-transform:uppercase;letter-spacing:.9px;color:#16324f !important;border-bottom:1px solid #cdd5df;padding-bottom:4px;margin-top:1.4rem !important}
table{font-size:14px} thead th{color:#6b7686 !important;text-transform:uppercase;font-size:11.5px;letter-spacing:.4px}
blockquote{background:#eef2f7;border-left:3px solid #b5893a;padding:8px 12px;border-radius:4px}
[data-testid="stSidebar"] h3{color:#16324f;font-size:1rem;text-transform:uppercase;letter-spacing:.7px}
.log{background:#101b28;color:#b6c6d8;font-family:Consolas,monospace;font-size:12.5px;border-radius:7px;padding:10px 13px;
     max-height:260px;overflow-y:auto;white-space:pre-wrap}
.ok{color:#7fc99a}.warn{color:#d4a94e}.bad{color:#e07a6a}
.small{color:#8a94a3;font-size:12px}
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="brand">THE SECOND OPINION</div>', unsafe_allow_html=True)
st.markdown('<div class="tag">The screener finds them. This does the homework.</div>', unsafe_allow_html=True)

# ---------- sidebar ------------------------------------------------------------------------------------------
with st.sidebar:
    st.subheader("Settings")
    critic_on = st.toggle("Risk Critic (reflection loop)", value=True,
                          help="Turn off to see the ablation: the writer's first draft ships without review.")
    make_pdf = st.toggle("Export PDF alongside markdown", value=False)
    offline = st.toggle("Offline mode (cache/fixtures only)", value=settings.offline)
    st.caption("Model tiers: **%s** for analysis · **%s** for routing/guards" % (settings.model_strong, settings.model_fast))
    if not settings.llm_configured and os.getenv("SO_FAKE_LLM") != "1":
        st.error("ANTHROPIC_API_KEY not set — copy .env.example to .env and add your key.")
    if os.getenv("SO_FAKE_LLM") == "1":
        st.info("Rehearsal mode: scripted model + fixture data (SO_FAKE_LLM=1).")

    st.subheader("Research history")
    ep = EpisodicMemory()
    hist = []
    for p in sorted(ep.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:12]:
        eps = ep.episodes(p.stem)
        if eps:
            last = eps[-1]
            hist.append((p.stem, last.get("ts", "")[:16].replace("T", " "), last.get("mode", ""), len(eps), last.get("report_path")))
    if not hist:
        st.caption("No runs yet. Episodic memory fills in as you research tickers.")
    for tk, ts, mode, n, path in hist:
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"**{tk}** · {ts[:10]} · {'report' if mode == 'full_report' else 'Q'} · {n}×")
        if path and Path(path).exists() and c2.button("↗", key=f"open_{tk}_{ts}", help="Open this report"):
            st.session_state["report_md"] = Path(path).read_text(encoding="utf-8")
            st.session_state["report_ticker"] = tk
            st.session_state["report_path"] = path
    st.caption("Re-running a ticker adds a *since last analysis* diff automatically.")

# ---------- input ----------------------------------------------------------------------------------------------
c1, c2 = st.columns([5, 1])
query = c1.text_input("Analyze a stock", placeholder="Ticker or company name — e.g. NVDA, nvidia, JPMorgan — or “NVDA — what's their debt situation?”",
                      label_visibility="collapsed")
go = c2.button("Analyze ▸", type="primary", use_container_width=True)
st.caption("A plain ticker → full report. Add a question after a dash → the router takes the cheap, focused path. "
           "Advice-seeking prompts (“should I buy?”) are refused.")


def run_pipeline(q: str):
    from second_opinion.orchestrator import Orchestrator
    settings.offline = offline
    log_box = st.empty()
    status = st.status("Working…", expanded=True)
    lines: list[str] = []

    def progress(msg: str) -> None:
        lines.append(msg)
        cls = "ok" if any(k in msg for k in ("APPROVE", "done", "ready", "ingested", "findings")) else \
              "bad" if any(k in msg for k in ("FAILED", "WARNING", "refused")) else \
              "warn" if "REJECT" in msg else ""
        html = "\n".join(f'<span class="{cls}">{l}</span>' if (l == msg and cls) else l for l in lines[-40:])
        log_box.markdown(f'<div class="log">{html}</div>', unsafe_allow_html=True)
        stage = ("Analysts working — parallel fan-out" if "fanning out" in msg else
                 "Valuation — scenarios & weights" if "[valuation]" in msg else
                 "Writer → Risk Critic (reflection loop)" if "[writer]" in msg or "[critic]" in msg else
                 "Guardrails" if "[guardrails]" in msg else None)
        if stage:
            status.update(label=stage)

    llm = None
    if os.getenv("SO_FAKE_LLM") == "1":  # rehearsal mode: scripted model + fixtures, no key/network (see tests/conftest.py)
        import sys
        sys.path.insert(0, str(Path(__file__).parent / "tests"))
        from conftest import scripted_llm
        llm, settings.offline = scripted_llm(), True
    orch = Orchestrator(llm=llm, critic_enabled=critic_on, on_progress=progress)
    ws = orch.run(q)
    u = ws.facts.get("usage") or {}
    label = ("Refused (guardrail)" if ws.mode == "refused" else
             f"Done — {u.get('calls', 0)} LLM calls · est. ${u.get('cost_usd', 0):.3f}")
    status.update(label=label, state="complete", expanded=False)
    return ws


if go and query.strip():
    try:
        ws = run_pipeline(query.strip())
        st.session_state["report_md"] = ws.report_md
        st.session_state["report_ticker"] = ws.ticker or "request"
        st.session_state["report_path"] = ws.facts.get("report_path")
        st.session_state["report_mode"] = ws.mode
        st.session_state["errors"] = ws.errors
        st.session_state["critiques"] = ws.critiques
        st.session_state["scenarios"] = ws.facts.get("scenarios")
        st.session_state["analyst"] = ws.facts.get("analyst")
        if make_pdf and ws.facts.get("report_path"):
            pdf = export_pdf(ws.report_md, Path(ws.facts["report_path"]).with_suffix(".pdf"))
            st.session_state["pdf_path"] = str(pdf) if pdf else None
    except Exception as e:  # noqa: BLE001
        st.error(f"Run failed: {type(e).__name__}: {e}")

# ---------- report -----------------------------------------------------------------------------------------------
def _for_display(md: str) -> str:
    """Streamlit renders $...$ as LaTeX; financial reports are full of dollar signs. Escape them for display only."""
    return md.replace("$", r"\$")


def price_chart(ticker: str, scenarios: dict | None, analyst: dict | None):
    """Daily close with a range filter and direct-labelled reference lines: bear/base/bull implied prices + analyst
    mean target. One series → no legend; identity carried by labels, not colour."""
    import datetime as dt
    import altair as alt
    import pandas as pd
    from second_opinion.tools import market
    from second_opinion import cache

    rng = st.radio("Range", ["1M", "3M", "6M", "YTD", "1Y", "5Y"], index=4, horizontal=True, label_visibility="collapsed")
    try:
        rows = market.price_history(ticker, "5y")
    except cache.CacheMiss:
        st.caption("Price history not available offline."); return
    except Exception as e:  # noqa: BLE001
        st.caption(f"Price history unavailable ({type(e).__name__})."); return
    df = pd.DataFrame(rows); df["date"] = pd.to_datetime(df["date"])
    end = df["date"].max()
    start = {"1M": end - pd.DateOffset(months=1), "3M": end - pd.DateOffset(months=3), "6M": end - pd.DateOffset(months=6),
             "YTD": pd.Timestamp(year=end.year, month=1, day=1), "1Y": end - pd.DateOffset(years=1), "5Y": df["date"].min()}[rng]
    d = df[df["date"] >= start]
    if d.empty:
        st.caption("No prices in range."); return
    first, last = float(d["close"].iloc[0]), float(d["close"].iloc[-1])
    chg = (last / first - 1) * 100
    st.markdown(f"**${last:,.2f}** &nbsp; <span style='color:{'#4f9d69' if chg >= 0 else '#c0504d'}'>{chg:+.1f}% over {rng}</span>",
                unsafe_allow_html=True)

    REF_COLORS = {"bear": "#c0504d", "base": "#5b7db1", "bull": "#4f9d69", "analyst": "#b5893a"}
    refs = []
    if scenarios:
        for r in scenarios.get("rows", []):
            refs.append({"label": f"{r['name'].title()}  ${r['implied_price']:,.0f}", "y": r["implied_price"], "color": REF_COLORS[r["name"]]})
    if analyst and analyst.get("target_mean"):
        refs.append({"label": f"Analyst mean  ${analyst['target_mean']:,.0f}", "y": analyst["target_mean"], "color": REF_COLORS["analyst"]})
    ymin = min([d["close"].min()] + [r["y"] for r in refs]) * 0.96
    ymax = max([d["close"].max()] + [r["y"] for r in refs]) * 1.05

    base = alt.Chart(d).encode(x=alt.X("date:T", axis=alt.Axis(title=None, grid=False, labelColor="#8a94a3", tickColor="#cdd5df", domainColor="#cdd5df")))
    line = base.mark_line(color="#4a90d9", strokeWidth=2).encode(
        y=alt.Y("close:Q", scale=alt.Scale(domain=[ymin, ymax]), axis=alt.Axis(title=None, gridColor="#8a94a3", gridOpacity=0.18, labelColor="#8a94a3", domainOpacity=0, tickOpacity=0, format="$,.0f")))
    hover = alt.selection_point(fields=["date"], nearest=True, on="mouseover", empty=False)
    pts = base.mark_circle(size=60, color="#4a90d9").encode(y="close:Q", opacity=alt.condition(hover, alt.value(1), alt.value(0)),
                                                             tooltip=[alt.Tooltip("date:T", title="Date"), alt.Tooltip("close:Q", title="Close", format="$,.2f")]).add_params(hover)
    rule = base.mark_rule(color="#8a94a3", strokeWidth=1, opacity=0.5).encode(opacity=alt.condition(hover, alt.value(0.5), alt.value(0)))
    layers = [line, pts, rule]
    if refs:
        rdf = pd.DataFrame(refs)
        layers.append(alt.Chart(rdf).mark_rule(strokeDash=[6, 4], strokeWidth=2, opacity=0.95).encode(
            y="y:Q", color=alt.Color("color:N", scale=None), tooltip=[alt.Tooltip("label:N", title="Reference")]))
        rdf["date"] = d["date"].max()
        layers.append(alt.Chart(rdf).mark_text(align="right", dx=-2, dy=-9, fontSize=12, fontWeight="bold").encode(
            x="date:T", y="y:Q", text="label:N", color=alt.Color("color:N", scale=None)))
    st.altair_chart(alt.layer(*layers).properties(height=300).configure_view(strokeOpacity=0), use_container_width=True)
    st.caption("Dashed lines — bear / base / bull 12-month implied prices and the analyst mean target: arithmetic on stated assumptions, not forecasts.")


md = st.session_state.get("report_md")
if md:
    st.divider()
    tk = st.session_state.get("report_ticker", "report")
    b1, b2, b3, b4 = st.columns([1, 1, 1, 3])
    b1.download_button("⬇ Markdown", md, file_name=f"{tk}_second_opinion.md", mime="text/markdown", use_container_width=True)
    b2.download_button("⬇ HTML", to_html(md), file_name=f"{tk}_second_opinion.html", mime="text/html", use_container_width=True)
    pdf_path = st.session_state.get("pdf_path")
    if pdf_path and Path(pdf_path).exists():
        b3.download_button("⬇ PDF", Path(pdf_path).read_bytes(), file_name=Path(pdf_path).name, mime="application/pdf", use_container_width=True)
    else:
        b3.caption("PDF: enable in sidebar")
    crit = st.session_state.get("critiques") or []
    if crit:
        with b4.expander(f"Risk Critic: {' → '.join(c['verdict'] for c in crit)}"):
            for c in crit:
                st.markdown(f"**Round {c['round']} — {c['verdict']}**")
                for i in c.get("issues", []):
                    st.markdown(f"- {i}")
                if c.get("strengths"):
                    st.caption("Strengths: " + "; ".join(c["strengths"]))
    # header + snapshot on the left, price chart on the right; rest of the report full-width below
    split = md.find("## What the company does")
    if split > 0 and st.session_state.get("report_mode") == "full_report":
        left, right = st.columns([1.05, 1])
        with left:
            st.markdown(_for_display(md[:split]))
        with right:
            st.markdown("#### Price & scenario map")
            price_chart(tk, st.session_state.get("scenarios"), st.session_state.get("analyst"))
        st.markdown(_for_display(md[split:]))
    else:
        st.markdown(_for_display(md))
    if st.session_state.get("errors"):
        st.caption("Data-quality notes: " + "; ".join(st.session_state["errors"]))

    # follow-up: routed to the focused-question path with the ticker prefilled
    if st.session_state.get("report_mode") == "full_report":
        st.divider()
        f1, f2 = st.columns([5, 1])
        fq = f1.text_input("Ask a follow-up about this company", placeholder=f"e.g. what are the biggest risks in the latest 10-K?",
                           key="followup", label_visibility="collapsed")
        if f2.button("Ask ▸", use_container_width=True) and fq.strip():
            ws2 = run_pipeline(f"{tk} — {fq.strip()}")
            st.markdown(_for_display(ws2.report_md))
