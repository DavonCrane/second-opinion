"""Router: decide which workflow a request needs (a graded pattern).

  full_report       "NVDA", "research nvidia"           -> all four analysts in parallel, writer, critic
  focused_question  "NVDA — what's their debt like?"    -> answer from workspace/RAG/tools, no full pipeline

Cheap heuristics first (a bare ticker is obviously a full report); the FAST model adjudicates anything
with a question in it. Also extracts the ticker/company from free text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TICKER = re.compile(r"\b[A-Z]{1,5}(?:\.[A-Z])?\b")
_QUESTION_HINTS = re.compile(r"\?|\bwhat|\bhow|\bwhy|\bis (their|its|the)\b|\bexplain\b|\btell me about (their|its)\b", re.I)


@dataclass
class Route:
    mode: str               # "full_report" | "focused_question"
    subject: str            # ticker or company name to resolve
    question: str = ""      # the focused question, if any
    via: str = "heuristic"  # "heuristic" | "llm"


def route(query: str, llm=None) -> Route:
    q = query.strip()
    # "NVDA — what's their debt situation?" / "nvidia: how fast is revenue growing"
    parts = re.split(r"\s+[—–-]\s+|:\s+", q, maxsplit=1)
    if len(parts) == 2 and _QUESTION_HINTS.search(parts[1]):
        return Route("focused_question", parts[0].strip(), parts[1].strip())
    words = q.split()
    if len(words) <= 3 and not _QUESTION_HINTS.search(q):
        subject = q
        for w in words:
            if w.lower() in {"research", "analyze", "analyse", "report", "on", "for", "the", "stock"}:
                continue
            subject = w
            break
        return Route("full_report", subject.strip("\"'"))
    if llm is not None:
        try:
            v = llm.complete_json(
                "You route requests for an equity research tool. Reply JSON: {\"mode\": \"full_report\"|\"focused_question\", "
                "\"subject\": \"<ticker or company name>\", \"question\": \"<the specific question, or empty>\"}. "
                "full_report = user wants a general analysis/report of a company. focused_question = user asks one "
                "specific thing (debt, risks, margins, a metric, a recent event).\n\nRequest: " + q, tier="fast", max_tokens=120)
            mode = v.get("mode") if v.get("mode") in ("full_report", "focused_question") else "full_report"
            return Route(mode, str(v.get("subject") or _guess_subject(q)), str(v.get("question") or ""), via="llm")
        except Exception:  # noqa: BLE001
            pass
    if _QUESTION_HINTS.search(q):
        return Route("focused_question", _guess_subject(q), q)
    return Route("full_report", _guess_subject(q))


def _guess_subject(q: str) -> str:
    m = _TICKER.search(q)
    if m and m.group(0) not in {"I", "A", "THE", "AND", "OR", "IS", "ARE"}:
        return m.group(0)
    for w in q.split():
        if w[:1].isupper() and w.lower() not in {"what", "how", "why", "is", "are", "the", "research", "analyze", "tell"}:
            return w.strip("?,.")
    return q.split()[0] if q.split() else q
