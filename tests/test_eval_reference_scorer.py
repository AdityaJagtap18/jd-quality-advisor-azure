"""Tests for the reference scorer (agents/eval_reference_scorer.py).

Score deltas are on the 0-100 scale. Text similarity is ROUGE-1 recall of the
pipeline's suggestion text against the gold suggestion text.
"""

from agents.eval_reference_scorer import run_reference_score

DIMS = ["completeness", "skill_specificity", "role_coherence",
        "cognitive_load", "inclusion_signals", "compensation"]


def _report(scores: dict, suggestions: list[str]) -> dict:
    return {
        "jdqi_score": 70,
        "benchmark_comparison": "x",
        "dimension_breakdown": [
            {"dimension": d, "score": scores[d], "narrative": "n"} for d in DIMS
        ],
        "suggested_additions": [
            {"section": "S", "suggestion": s, "impact": "i"} for s in suggestions
        ],
    }


def _gold(scores: dict, suggestions: list[str]) -> dict:
    return {"dimension_scores": scores, "suggested_additions": suggestions, "notes": ""}


def test_identical_scores_and_suggestions_pass():
    scores = {d: 80 for d in DIMS}
    sugg = ["Add explicit INR salary band to the compensation section"]
    result = run_reference_score(_report(scores, sugg), _gold(scores, sugg))
    assert result["verdict"] == "pass"
    assert result["score_delta_avg"] == 0.0
    assert result["text_similarity"] == 1.0


def test_large_deltas_fail():
    report_scores = {d: 90 for d in DIMS}
    gold_scores = {d: 20 for d in DIMS}   # delta 70 everywhere
    sugg = ["completely different unrelated words here"]
    result = run_reference_score(
        _report(report_scores, sugg), _gold(gold_scores, ["nothing matches xyz"]))
    assert result["verdict"] == "fail"
    assert result["score_delta_avg"] > 20


def test_close_scores_divergent_suggestions_warn():
    report_scores = {d: 80 for d in DIMS}
    gold_scores = {d: 92 for d in DIMS}   # delta 12 -> within warn band (<=20)
    result = run_reference_score(
        _report(report_scores, ["specify react version and typescript requirement"]),
        _gold(gold_scores, ["mention react version somewhere in listing"]),
    )
    assert result["verdict"] == "warn"


def test_empty_pipeline_suggestions_zero_similarity():
    scores = {d: 80 for d in DIMS}
    result = run_reference_score(
        _report(scores, []), _gold(scores, ["add a salary band please"]))
    assert result["text_similarity"] == 0.0


def test_stopword_only_suggestions_zero_similarity():
    scores = {d: 80 for d in DIMS}
    result = run_reference_score(
        _report(scores, ["the and or to in of"]),
        _gold(scores, ["a an the and for with"]),
    )
    assert result["text_similarity"] == 0.0


def test_per_dimension_deltas_reported():
    report_scores = {d: 80 for d in DIMS}
    gold_scores = {d: 80 for d in DIMS}
    gold_scores["skill_specificity"] = 50   # delta 30 on one dim
    result = run_reference_score(
        _report(report_scores, ["x y z"]), _gold(gold_scores, ["x y z"]))
    assert result["score_deltas"]["skill_specificity"] == 30.0
    assert result["score_deltas"]["completeness"] == 0.0
