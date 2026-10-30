"""Tests for the orchestrator's live path (agents/eval_orchestrator.run_eval_live).

The live pipeline functions and the LLM judge are patched — no live API calls.
Verifies that run_eval_live runs the pipeline, splices the FRESH advisor report
into the evaluation, and assembles an EvalResult against the scenario's gold.
"""

import agents.eval_orchestrator as orch


DIMS = ["completeness", "skill_specificity", "role_coherence",
        "cognitive_load", "inclusion_signals", "compensation"]


def _fresh_report():
    return {
        "jdqi_score": 80,
        "benchmark_comparison": "fresh live output",
        "dimension_breakdown": [
            {"dimension": d, "score": 80, "narrative": f"grounded narrative for {d}"}
            for d in DIMS
        ],
        "suggested_additions": [
            {"section": "S", "suggestion": "add an explicit salary band", "impact": "i"},
        ],
    }


def _scenario():
    return {
        "scenario_id": "strong",
        "label": "x",
        "industry": "IT/SaaS",
        "jd_text": "some jd text",
        "cached_advisor_report": {"jdqi_score": 1, "dimension_breakdown": [],
                                  "suggested_additions": []},  # should NOT be used
        "gold_reference": {
            "dimension_scores": {d: 80 for d in DIMS},
            "suggested_additions": ["add an explicit salary band"],
            "notes": "",
        },
    }


def test_run_eval_live_uses_fresh_pipeline_report(monkeypatch):
    import agents.parser
    import agents.specialists
    import agents.synthesizer
    monkeypatch.setattr(agents.parser, "run_parser",
                        lambda jd, ind, client: {"role_title": "Eng", "seniority_level": "mid"})
    monkeypatch.setattr(agents.specialists, "run_specialists",
                        lambda jd, parsed, client: {})
    monkeypatch.setattr(agents.synthesizer, "run_synthesizer",
                        lambda ctx, client: _fresh_report())
    # keep the judge offline
    monkeypatch.setattr(orch, "run_llm_judge",
                        lambda jd, report, **kw: {"verdict": "pass", "dimension_support": {},
                                                  "unsupported_scores": [], "vagueness_flags": [],
                                                  "reasoning": "", "duration_ms": 0.0})

    result = orch.run_eval_live(_scenario(), client=object())

    # the fresh report (jdqi 80), not the cached stub (jdqi 1), was evaluated
    assert result["advisor_report"]["jdqi_score"] == 80
    assert result["code_check"]["verdict"] == "pass"
    assert result["reference_score"]["verdict"] == "pass"   # scores match gold exactly
    assert result["scenario_id"] == "strong"
    assert result["human_eval"] is None
