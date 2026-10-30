"""Tests for the deterministic code checker (agents/eval_code_checker.py).

Operates on the real AdvisorReport shape:
  {jdqi_score, benchmark_comparison, dimension_breakdown:[{dimension,score,narrative}],
   suggested_additions:[{section,suggestion,impact}]}
"""

import copy

from agents.eval_code_checker import run_code_check, DIMENSION_WEIGHTS

DIMS = ["completeness", "skill_specificity", "role_coherence",
        "cognitive_load", "inclusion_signals", "compensation"]


def _valid_report() -> dict:
    """A structurally valid report where every dimension scores 80.

    Weighted sum = 80 * sum(weights) = 80, so jdqi_score = 80 keeps check 3 happy.
    """
    return {
        "jdqi_score": 80,
        "benchmark_comparison": "Comparable to strong market benchmarks for the role.",
        "dimension_breakdown": [
            {"dimension": d, "score": 80,
             "narrative": f"The {d} dimension is well supported by explicit JD content."}
            for d in DIMS
        ],
        "suggested_additions": [
            {"section": "Compensation", "suggestion": "Add an explicit INR salary band.",
             "impact": "Salary-filtered candidates will see the role."},
        ],
    }


def test_valid_report_passes_all_seven_checks():
    result = run_code_check(_valid_report())
    assert result["verdict"] == "pass"
    assert result["checks_passed"] == 7
    assert result["checks_total"] == 7
    assert result["failures"] == []


def test_missing_dimension_fails_with_name():
    report = _valid_report()
    report["dimension_breakdown"] = [d for d in report["dimension_breakdown"]
                                      if d["dimension"] != "compensation"]
    result = run_code_check(report)
    assert result["verdict"] == "fail"
    assert any("compensation" in f for f in result["failures"])


def test_score_out_of_range_fails():
    report = _valid_report()
    report["dimension_breakdown"][0]["score"] = 120
    result = run_code_check(report)
    assert result["verdict"] == "fail"
    assert any("out of range" in f for f in result["failures"])


def test_weighted_total_divergence_fails():
    report = _valid_report()
    report["jdqi_score"] = 90  # weighted sum is 80, diverges by 10 > 2.0
    result = run_code_check(report)
    assert result["verdict"] == "fail"
    assert any("diverges" in f for f in result["failures"])


def test_missing_narrative_only_warns():
    report = _valid_report()
    report["dimension_breakdown"][0]["narrative"] = "short"  # < 10 chars
    result = run_code_check(report)
    assert result["verdict"] == "warn"


def test_empty_suggested_additions_warns():
    report = _valid_report()
    report["suggested_additions"] = []
    result = run_code_check(report)
    assert result["verdict"] == "warn"
    assert any("suggested additions" in f.lower() for f in result["failures"])


def test_missing_jdqi_score_fails():
    report = _valid_report()
    del report["jdqi_score"]
    result = run_code_check(report)
    assert result["verdict"] == "fail"
    assert any("jdqi_score" in f for f in result["failures"])


def test_only_warn_level_failures_yields_warn():
    report = _valid_report()
    report["dimension_breakdown"][0]["narrative"] = ""      # check 4 warn
    report["suggested_additions"] = []                       # check 5 warn
    result = run_code_check(report)
    assert result["verdict"] == "warn"


def test_malformed_addition_warns():
    report = _valid_report()
    report["suggested_additions"] = [{"section": "X", "suggestion": "", "impact": "y"}]
    result = run_code_check(report)
    assert result["verdict"] == "warn"


def test_dimension_weights_sum_to_one():
    assert abs(sum(DIMENSION_WEIGHTS.values()) - 1.0) < 1e-9


def test_duration_is_reported():
    result = run_code_check(_valid_report())
    assert result["duration_ms"] >= 0.0
