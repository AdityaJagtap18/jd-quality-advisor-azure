"""Evaluator 2 — LLM-as-judge.

A separate Sonnet call that scrutinises the advisor report against a rubric,
catching scores that are present but unsupported by evidence and suggestions
that are vague. Target: ~3s.

Model: claude-sonnet-4-6 (deliberately the same class as the pipeline's
synthesis executor — the point is that a structured rubric lets the same model
class catch its own pipeline's failures). JSON-only response, parsed with a
local copy of _extract_json (evaluators stay self-contained — no cross-imports
from agents/advisor.py).
"""

import json
import os
import time
from typing import Optional

import anthropic

from agents.llm_client import build_client
from models.eval_context import LLMJudgeResult

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_MAX_ATTEMPTS = 2

DIMENSIONS = ["completeness", "skill_specificity", "role_coherence",
              "cognitive_load", "inclusion_signals", "compensation"]

_SYSTEM = """You are a strict evaluator of job description quality reports. You receive a JD and an AI-generated quality report. Your job is to assess whether each dimension score in the report is supported by concrete evidence from the JD text.

Return ONLY a JSON object. No preamble, no explanation outside the JSON."""

_USER_TEMPLATE = """JD TEXT:
{jd_text}

ADVISOR REPORT:
{advisor_report_json}

Evaluate the report against these criteria:

1. DIMENSION SUPPORT: For each of the 6 dimensions (completeness, skill_specificity, role_coherence, cognitive_load, inclusion_signals, compensation), does the `narrative` for that dimension in the report actually reference specific content from the JD text? Mark true if yes, false if the narrative appears unsupported, generic, or could apply to any JD.

2. VAGUENESS FLAGS: List any items in suggested_additions whose `suggestion` text is vague — i.e., it does not tell the hiring manager exactly what to add. Example of vague: "Add more detail about skills." Example of specific: "Add required proficiency level (beginner/intermediate/expert) for each tool listed."

3. REASONING: Write one paragraph explaining your overall verdict.

4. VERDICT: "pass" if >=5 dimensions are supported and <=1 vagueness flag. "warn" if 3-4 dimensions supported or 2-3 vagueness flags. "fail" if <=2 dimensions supported or >=4 vagueness flags.

Return this exact JSON structure:
{{
  "dimension_support": {{
    "completeness": true,
    "skill_specificity": false,
    "role_coherence": true,
    "cognitive_load": true,
    "inclusion_signals": true,
    "compensation": false
  }},
  "unsupported_scores": ["skill_specificity", "compensation"],
  "vagueness_flags": ["Add more detail about compensation"],
  "reasoning": "...",
  "verdict": "warn"
}}"""


def _extract_json(text: str) -> str:
    """Strip markdown fences then extract the first complete JSON object."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end]).strip()

    start = text.find("{")
    if start == -1:
        return text
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _get_client():
    return build_client()


def _fallback(error: str, duration_ms: float) -> LLMJudgeResult:
    return LLMJudgeResult(
        verdict="warn",
        dimension_support={d: False for d in DIMENSIONS},
        unsupported_scores=[],
        vagueness_flags=[],
        reasoning=f"Judge call failed: {error}",
        duration_ms=duration_ms,
    )


def run_llm_judge(jd_text: str, advisor_report: dict,
                  client: Optional[anthropic.Anthropic] = None) -> LLMJudgeResult:
    start = time.perf_counter()
    user = _USER_TEMPLATE.format(
        jd_text=jd_text,
        advisor_report_json=json.dumps(advisor_report, indent=2),
    )

    last_error = "unknown error"
    for _ in range(_MAX_ATTEMPTS):
        try:
            active = client if client is not None else _get_client()
            response = active.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM,
                messages=[{"role": "user", "content": user}],
            )
            data = json.loads(_extract_json(response.content[0].text))

            verdict = data.get("verdict")
            if verdict not in ("pass", "warn", "fail"):
                verdict = "warn"
            support = data.get("dimension_support", {})
            dimension_support = {d: bool(support.get(d, False)) for d in DIMENSIONS}

            return LLMJudgeResult(
                verdict=verdict,
                dimension_support=dimension_support,
                unsupported_scores=list(data.get("unsupported_scores", [])),
                vagueness_flags=list(data.get("vagueness_flags", [])),
                reasoning=str(data.get("reasoning", "")),
                duration_ms=(time.perf_counter() - start) * 1000.0,
            )
        except Exception as e:  # noqa: BLE001 — judge must never raise
            last_error = str(e)
            continue

    return _fallback(last_error, (time.perf_counter() - start) * 1000.0)
