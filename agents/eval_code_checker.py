"""Evaluator 1 — deterministic code/rule-based checker.

No LLM call. Validates the structure and internal consistency of a real
AdvisorReport. Target: < 50ms.

Verdict logic:
  - "fail" if any of checks 1, 2, 3, 7 fails (structural / math integrity)
  - "warn" if only checks 4-6 fail (content quality) and no fail-level check failed
  - "pass" if all 7 checks pass
"""

import time

from models.eval_context import CodeCheckResult

DIMENSIONS = ["completeness", "skill_specificity", "role_coherence",
              "cognitive_load", "inclusion_signals", "compensation"]

DIMENSION_WEIGHTS = {
    "completeness": 0.25,
    "skill_specificity": 0.20,
    "role_coherence": 0.20,
    "cognitive_load": 0.15,
    "inclusion_signals": 0.10,
    "compensation": 0.10,
}

_TOLERANCE = 2.0  # weighted-total divergence allowed vs jdqi_score, 0-100 scale


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def run_code_check(advisor_report: dict) -> CodeCheckResult:
    start = time.perf_counter()
    failures: list[str] = []
    fail_level = False   # any of checks 1,2,3,7 failed
    passed = 0

    breakdown = advisor_report.get("dimension_breakdown") or []
    by_name = {d.get("dimension"): d for d in breakdown if isinstance(d, dict)}

    # Check 1 — schema completeness
    missing = [d for d in DIMENSIONS if d not in by_name]
    if missing:
        for name in missing:
            failures.append(f"Missing dimension: {name}")
        fail_level = True
    else:
        passed += 1

    # Check 2 — score range (0-100) for every present dimension
    range_ok = True
    for name in DIMENSIONS:
        entry = by_name.get(name)
        if entry is None:
            continue
        val = entry.get("score")
        if not _is_number(val) or not (0.0 <= val <= 100.0):
            failures.append(f"Score out of range for {name}: {val}")
            range_ok = False
    if range_ok:
        passed += 1
    else:
        fail_level = True

    # Check 7 — overall score present and in range (evaluated before check 3,
    # since check 3 depends on it)
    reported = advisor_report.get("jdqi_score")
    jdqi_ok = _is_number(reported) and 0.0 <= reported <= 100.0
    if jdqi_ok:
        passed += 1
    else:
        failures.append("jdqi_score missing or out of range")
        fail_level = True

    # Check 3 — weighted total consistency (skip gracefully if inputs unusable)
    if not missing and range_ok and jdqi_ok:
        computed = sum(by_name[d]["score"] * DIMENSION_WEIGHTS[d] for d in DIMENSIONS)
        if abs(computed - reported) <= _TOLERANCE:
            passed += 1
        else:
            failures.append(
                f"Weighted total {computed:.2f} diverges from reported {reported:.2f}")
            fail_level = True
    # else: check 3 cannot be computed — a fail-level check already fired.

    # Check 4 — evidence present (narrative >= 10 chars) for every present dimension
    narrative_ok = True
    for name in DIMENSIONS:
        entry = by_name.get(name)
        if entry is None:
            continue
        narrative = entry.get("narrative")
        if not isinstance(narrative, str) or len(narrative.strip()) < 10:
            failures.append(f"No narrative for dimension: {name}")
            narrative_ok = False
    if narrative_ok:
        passed += 1

    # Check 5 — suggested_additions non-empty
    additions = advisor_report.get("suggested_additions")
    if isinstance(additions, list) and len(additions) >= 1:
        passed += 1
    else:
        failures.append("No suggested additions returned")

    # Check 6 — each addition is a dict with a non-empty suggestion string
    additions_list = additions if isinstance(additions, list) else []
    well_formed = all(
        isinstance(a, dict)
        and isinstance(a.get("suggestion"), str)
        and a["suggestion"].strip()
        for a in additions_list
    )
    if well_formed:
        passed += 1
    else:
        failures.append("suggested_additions contains malformed or empty item")

    if fail_level:
        verdict = "fail"
    elif failures:
        verdict = "warn"
    else:
        verdict = "pass"

    duration_ms = (time.perf_counter() - start) * 1000.0
    return CodeCheckResult(
        verdict=verdict,
        checks_passed=passed,
        checks_total=7,
        failures=failures,
        duration_ms=duration_ms,
    )
