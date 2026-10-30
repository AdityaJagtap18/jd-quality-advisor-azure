"""Evaluator 3 — reference-based scorer.

No LLM call. Compares the advisor report's dimension scores against a
hand-verified gold reference (0-100 scale) and computes rough ROUGE-1 recall
overlap between the pipeline's suggestions and the gold suggestions. Target: < 20ms.

Verdict logic (0-100 scale):
  - "pass" if score_delta_avg <= 10 and text_similarity >= 0.4
  - "warn" if score_delta_avg <= 20 and text_similarity >= 0.2
  - "fail" otherwise
"""

import re
import time

from models.eval_context import GoldReference, ReferenceScoreResult

DIMENSIONS = ["completeness", "skill_specificity", "role_coherence",
              "cognitive_load", "inclusion_signals", "compensation"]

_STOPWORDS = {"a", "an", "the", "and", "or", "to", "in", "of", "for",
              "with", "that", "is", "are", "be"}


def _tokenize(text: str) -> set[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and empties."""
    tokens = re.split(r"[^a-z0-9]+", text.lower())
    return {t for t in tokens if t and t not in _STOPWORDS}


def _rouge1_recall(pipeline_text: str, gold_text: str) -> float:
    gold_tokens = _tokenize(gold_text)
    if not gold_tokens:
        return 0.0
    pipeline_tokens = _tokenize(pipeline_text)
    overlap = pipeline_tokens & gold_tokens
    return len(overlap) / len(gold_tokens)


def run_reference_score(advisor_report: dict, gold: GoldReference) -> ReferenceScoreResult:
    start = time.perf_counter()

    by_name = {d.get("dimension"): d for d in advisor_report.get("dimension_breakdown", [])
               if isinstance(d, dict)}
    gold_scores = gold.get("dimension_scores", {})

    score_deltas: dict[str, float] = {}
    for dim in DIMENSIONS:
        pipeline_score = by_name.get(dim, {}).get("score")
        gold_score = gold_scores.get(dim)
        if pipeline_score is None or gold_score is None:
            continue
        score_deltas[dim] = abs(float(pipeline_score) - float(gold_score))

    score_delta_avg = (sum(score_deltas.values()) / len(score_deltas)) if score_deltas else 0.0

    pipeline_text = " ".join(
        a.get("suggestion", "") for a in advisor_report.get("suggested_additions", [])
        if isinstance(a, dict)
    )
    gold_text = " ".join(gold.get("suggested_additions", []))
    text_similarity = _rouge1_recall(pipeline_text, gold_text)

    if score_delta_avg <= 10 and text_similarity >= 0.4:
        verdict = "pass"
    elif score_delta_avg <= 20 and text_similarity >= 0.2:
        verdict = "warn"
    else:
        verdict = "fail"

    duration_ms = (time.perf_counter() - start) * 1000.0
    return ReferenceScoreResult(
        verdict=verdict,
        score_delta_avg=score_delta_avg,
        score_deltas=score_deltas,
        text_similarity=text_similarity,
        duration_ms=duration_ms,
    )
