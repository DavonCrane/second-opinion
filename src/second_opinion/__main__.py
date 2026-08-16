"""CLI entry point.

  python -m second_opinion NVDA                       full report
  python -m second_opinion "nvidia"                   company name -> ticker resolved
  python -m second_opinion "NVDA — what's their debt situation?"   focused question (routed)
  python -m second_opinion NVDA --no-critic           ablation: skip the Risk Critic
  python -m second_opinion NVDA --pdf                 also export a styled PDF
  python -m second_opinion                            interactive prompt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import settings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="second-opinion", description="The Second Opinion — cited one-page equity research.")
    ap.add_argument("query", nargs="*", help="ticker, company name, or 'TICKER — question'")
    ap.add_argument("--no-critic", action="store_true", help="disable the Risk Critic (eval ablation)")
    ap.add_argument("--rounds", type=int, default=None, help="max critic rounds (default from .env, 2)")
    ap.add_argument("--pdf", action="store_true", help="also export a styled PDF next to the markdown report")
    ap.add_argument("--offline", action="store_true", help="never hit the network; use cache/fixtures only")
    ap.add_argument("--sequential", action="store_true", help="run analysts one at a time (debugging)")
    ap.add_argument("--quiet", action="store_true", help="suppress the live agent log")
    args = ap.parse_args(argv)

    if args.offline:
        settings.offline = True
    query = " ".join(args.query).strip()
    if not query:
        try:
            query = input("Ticker or company name (or 'TICKER — question'): ").strip()
        except (EOFError, KeyboardInterrupt):
            return 1
    if not query:
        print("Nothing to do.")
        return 1

    try:
        from rich.console import Console
        console = Console()
        say = (lambda m: None) if args.quiet else (lambda m: console.print(f"[dim]{m}[/dim]", highlight=False))
    except ImportError:  # rich is in requirements, but never make it fatal
        console = None
        say = (lambda m: None) if args.quiet else print

    from .orchestrator import Orchestrator
    try:
        orch = Orchestrator(critic_enabled=not args.no_critic, max_critic_rounds=args.rounds,
                            on_progress=say, parallel=not args.sequential)
    except RuntimeError as e:
        print(f"Setup problem: {e}")
        return 2
    try:
        ws = orch.run(query)
    except Exception as e:  # noqa: BLE001
        print(f"Run failed: {type(e).__name__}: {e}")
        return 3

    print()
    print(ws.report_md)
    if ws.mode == "refused":
        return 0
    if ws.facts.get("report_path"):
        print(f"\nSaved: {ws.facts['report_path']}")
        if args.pdf:
            from .report import export_pdf
            pdf = export_pdf(ws.report_md, Path(ws.facts["report_path"]).with_suffix(".pdf"))
            print(f"PDF:   {pdf}" if pdf else "PDF:   skipped (no Chrome/Edge/Chromium found; HTML written instead)")
    u = ws.facts.get("usage") or {}
    if u:
        print(f"LLM usage: {u.get('calls')} calls, {u.get('input_tokens')} in / {u.get('output_tokens')} out tokens, est. ${u.get('cost_usd', 0):.3f}")
    if ws.errors:
        print("Notes: " + "; ".join(ws.errors))
    return 0


if __name__ == "__main__":
    sys.exit(main())
