"""Risk Critic: reflection / self-critique (a graded pattern, and the eval's ablation target).

Attacks the draft on four fronts and returns APPROVE or REJECT with concrete issues:
  1. Bear case weaker/vaguer than the bull case
  2. Claims not supported by (or not cited to) the workspace findings; numbers not in the findings
  3. Stale, missing, or contradicted evidence; sentiment/fundamentals divergence ignored
  4. Scenario weights: tilt not justified by cited evidence (must sign off explicitly if tilt > 0.20)
Deterministic checks run first (citation coverage, weight tilt); the model judges quality.
"""
from __future__ import annotations

from ..guardrails import output_guard
from ..memory.workspace import Workspace
from ..tools import calculator as calc
from .base import Agent

CRITIC_SYSTEM = (
    "You are the Risk Critic on an equity research desk. Your job is to find what is wrong, weak, unsupported, or "
    "one-sided in a draft report before it reaches an investor. You are rigorous and specific: every objection names "
    "the sentence or section and says exactly what would fix it. You approve only drafts you would sign your name to."
)


class CriticAgent(Agent):
    name = "critic"
    section = "critique"

    def run(self, ws: Workspace, draft: str) -> dict:
        issues: list[str] = []
        og = output_guard(draft, len(ws.sources))
        if not og.ok:
            issues.append(f"Guardrail: {og.reason}. Uncited examples: {og.details.get('uncited_examples')}")
        sc = ws.facts.get("scenarios")
        tilt = calc.weight_tilt(sc["weights"]) if sc else 0.0
        prompt = f"""Draft report for {ws.ticker}:
---
{draft}
---
Workspace findings the draft must be grounded in (source ids in brackets):
{ws.findings_text()}

Scenario weights: {sc['weights'] if sc else 'n/a'} (tilt from neutral 25/50/25 = {tilt:.2f}); rationale: {sc.get('weight_rationale') if sc else ''}

Review strictly:
1. Is the bear case as specific, evidenced, and cited as the bull case? Quote any bear bullet that is vaguer than its bull counterpart.
2. Does any sentence state a number or fact that is NOT in the findings? List them.
3. Is any important cross-check missing (e.g. sentiment vs. fundamentals divergence, growth deceleration vs. multiple, new risk language, guidance vs. consensus)?
4. Are the scenario weights justified by the cited evidence? If tilt > 0.20, you must explicitly sign off or reject.
5. Any advice-like language, hype, or hedging-into-mush?
JSON: {{"verdict": "APPROVE"|"REJECT", "issues": ["specific, actionable issue", ...], "weights_signed_off": true|false, "strengths": ["..."]}}
REJECT if there is any issue in 1, 2 or 4. Minor style points alone -> APPROVE with issues listed as suggestions."""
        try:
            out = self.llm.complete_json(prompt, system=CRITIC_SYSTEM, tier="strong", max_tokens=900)
        except Exception as e:  # noqa: BLE001
            out = {"verdict": "APPROVE", "issues": [f"critic LLM failed ({e}); deterministic checks only"], "weights_signed_off": False}
        verdict = str(out.get("verdict", "APPROVE")).upper()
        issues = issues + [str(i) for i in out.get("issues", [])]
        signed = bool(out.get("weights_signed_off"))
        if tilt > calc.MAX_TILT and not signed:
            verdict = "REJECT"
            issues.append(f"Scenario weight tilt {tilt:.2f} exceeds {calc.MAX_TILT} and the critic did not sign off — "
                          "reduce the tilt or cite stronger evidence.")
        if not og.ok:
            verdict = "REJECT"
        result = {"round": len(ws.critiques) + 1, "verdict": verdict, "issues": issues, "weights_signed_off": signed,
                  "strengths": out.get("strengths", []), "guard": og.details}
        ws.critiques.append(result)
        ws.note(f"[critic] round {result['round']}: {verdict}" + (f" — {len(issues)} issue(s)" if issues else ""))
        return result
