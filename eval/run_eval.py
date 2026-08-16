"""Evaluation harness for The Second Opinion.

Runs every case in eval/cases/*.json, records per-case metrics, and writes eval/results/<stamp>/summary.json +
summary.md. Supports the critic ablation (--no-critic) and repeated runs for weight-stability (--repeat N).

Metrics
  task_success       report has all template sections AND every metric row present AND output guard ok
  citation_coverage  % of factual sentences carrying a valid [n] citation (from guardrails.output_guard)
  weight_stability   across --repeat runs: max std-dev of the three scenario weights (lower = more stable)
  cost_usd / latency per run
  critic_rounds      how many rounds the reflection loop took

Usage
  python eval/run_eval.py                       # live (needs ANTHROPIC_API_KEY)
  python eval/run_eval.py --offline --fake      # offline smoke run with the scripted FakeLLM from tests/
  python eval/run_eval.py --no-critic           # ablation arm
  python eval/run_eval.py --repeat 5 --cases NVDA_full   # stability metric on one case
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from second_opinion.config import settings  # noqa: E402
from second_opinion.agents.writer import SECTIONS  # noqa: E402


def load_cases(names: list[str] | None) -> list[dict]:
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "eval" / "cases").glob("*.json"))]
    if names:
        cases = [c for c in cases if c["id"] in names]
    return cases


def score(case: dict, ws, elapsed: float) -> dict:
    md = ws.report_md or ""
    expect = case.get("expect", {})
    out = {"id": case["id"], "query": case["query"], "mode": ws.mode, "latency_s": round(elapsed, 1),
           "cost_usd": (ws.facts.get("usage") or {}).get("cost_usd"), "llm_calls": (ws.facts.get("usage") or {}).get("calls"),
           "errors": ws.errors, "critic_rounds": len(ws.critiques),
           "critic_verdicts": [c["verdict"] for c in ws.critiques]}
    og = ws.facts.get("output_guard") or {}
    out["citation_coverage"] = og.get("coverage")
    checks = {}
    if expect.get("mode"):
        checks["mode"] = ws.mode == expect["mode"]
    if ws.mode == "full_report":
        checks["sections_complete"] = all(f"## {s}" in md for s in SECTIONS)
        checks["guard_ok"] = bool(og.get("ok"))
        checks["has_scenarios"] = ("| **Bear** |" in md) if expect.get("scenarios", True) else True
        checks["bear_case_3"] = md.count("\n1. **") >= 2 and "## Bear case" in md
    if ws.mode == "focused_question":
        checks["cited_answer"] = "[1]" in md and bool(og.get("ok", og.get("coverage", 0) >= 0.8))
    if ws.mode == "refused":
        checks["refused"] = "not an adviser" in md
    for needle in expect.get("contains", []):
        checks[f"contains:{needle}"] = needle.lower() in md.lower()
    for needle in expect.get("not_contains", []):
        checks[f"not_contains:{needle}"] = needle.lower() not in md.lower()
    if expect.get("since_last"):
        checks["since_last_diff"] = ws.facts.get("episodic_diff") is not None
    out["checks"] = checks
    out["task_success"] = all(checks.values()) if checks else None
    sc = ws.facts.get("scenarios")
    out["weights"] = sc.get("weights") if sc else None
    out["claude_view"] = sc.get("claude_view") if sc else None
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", nargs="*", help="case ids to run (default all)")
    ap.add_argument("--no-critic", action="store_true")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--fake", action="store_true", help="use tests/conftest scripted FakeLLM (offline smoke)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    if args.offline:
        settings.offline = True

    from second_opinion.orchestrator import Orchestrator
    from second_opinion.memory import EpisodicMemory, SemanticMemory
    llm = None
    if args.fake:
        from conftest import scripted_llm
        llm = scripted_llm()

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S") + (f"_{args.tag}" if args.tag else "") + ("_nocritic" if args.no_critic else "")
    outdir = ROOT / "eval" / "results" / stamp
    outdir.mkdir(parents=True, exist_ok=True)
    # isolated memory per eval run so 'since last analysis' cases are deterministic
    ep, sm = EpisodicMemory(outdir / "mem_ep"), SemanticMemory(outdir / "mem_sem")

    results = []
    for case in load_cases(args.cases):
        for rep in range(args.repeat):
            if args.fake:
                from conftest import scripted_llm
                llm = scripted_llm()
            orch = Orchestrator(llm=llm, critic_enabled=not args.no_critic, episodic=ep, semantic=sm)
            for pre in case.get("pre_runs", []):          # e.g. run once before to seed memory
                try:
                    orch.run(pre)
                except Exception:  # noqa: BLE001
                    pass
            t0 = time.time()
            try:
                ws = orch.run(case["query"])
                r = score(case, ws, time.time() - t0)
                (outdir / f"{case['id']}_{rep}.md").write_text(ws.report_md or "", encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                r = {"id": case["id"], "query": case["query"], "error": f"{type(e).__name__}: {e}",
                     "task_success": case.get("expect", {}).get("error_ok", False), "latency_s": round(time.time() - t0, 1)}
            r["rep"] = rep
            results.append(r)
            print(f"{case['id']:<28} rep{rep} success={r.get('task_success')} cov={r.get('citation_coverage')} "
                  f"rounds={r.get('critic_rounds')} ${r.get('cost_usd')} {r.get('latency_s')}s {r.get('error','')}")

    # aggregate
    succ = [r["task_success"] for r in results if r.get("task_success") is not None]
    cov = [r["citation_coverage"] for r in results if r.get("citation_coverage") is not None]
    cost = [r["cost_usd"] for r in results if r.get("cost_usd")]
    stability = None
    by_case: dict[str, list[dict]] = {}
    for r in results:
        if r.get("weights"):
            by_case.setdefault(r["id"], []).append(r["weights"])
    stab = {}
    for cid, ws_ in by_case.items():
        if len(ws_) > 1:
            stab[cid] = round(max(statistics.pstdev([w[k] for w in ws_]) for k in ("bear", "base", "bull")), 3)
    if stab:
        stability = stab
    summary = {"stamp": stamp, "critic_enabled": not args.no_critic, "n_runs": len(results),
               "task_success_rate": round(sum(succ) / len(succ), 3) if succ else None,
               "mean_citation_coverage": round(sum(cov) / len(cov), 3) if cov else None,
               "mean_cost_usd": round(sum(cost) / len(cost), 4) if cost else None,
               "weight_stability_maxstd": stability, "results": results}
    (outdir / "summary.json").write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    lines = ["| case | mode | success | coverage | critic rounds | cost $ | latency s |", "|---|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} (rep{r['rep']}) | {r.get('mode','—')} | {r.get('task_success')} | {r.get('citation_coverage')} | "
                     f"{r.get('critic_rounds','—')} | {r.get('cost_usd','—')} | {r.get('latency_s')} |")
    (outdir / "summary.md").write_text("\n".join(lines) + f"\n\n**Task success:** {summary['task_success_rate']} · "
                                       f"**Mean citation coverage:** {summary['mean_citation_coverage']} · **Stability (max std):** {stability}\n", encoding="utf-8")
    print(f"\nTask success {summary['task_success_rate']} · coverage {summary['mean_citation_coverage']} · results in {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
