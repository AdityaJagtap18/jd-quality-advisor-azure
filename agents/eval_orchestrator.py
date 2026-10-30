"""Runs the three automated evaluators in parallel and assembles an EvalResult.

Mirrors the ThreadPoolExecutor pattern in agents/specialists.py (submit +
as_completed, keyed by evaluator name). Human eval is not included here — it is
added later in the UI layer.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed

from agents.eval_code_checker import run_code_check
from agents.eval_llm_judge import run_llm_judge
from agents.eval_reference_scorer import run_reference_score
from models.eval_context import EvalResult, EvalScenario


def run_eval(scenario: EvalScenario) -> EvalResult:
    report = scenario["cached_advisor_report"]
    gold = scenario["gold_reference"]
    jd_text = scenario["jd_text"]

    results: dict = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_code_check, report): "code_check",
            executor.submit(run_llm_judge, jd_text, report): "llm_judge",
            executor.submit(run_reference_score, report, gold): "reference_score",
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    agreement_matrix = {
        "code": results["code_check"]["verdict"],
        "llm_judge": results["llm_judge"]["verdict"],
        "reference": results["reference_score"]["verdict"],
    }
    verdicts = list(agreement_matrix.values())
    majority = max(set(verdicts), key=verdicts.count)
    disagreement_count = sum(1 for v in verdicts if v != majority)

    return EvalResult(
        scenario_id=scenario["scenario_id"],
        jd_text=jd_text,
        advisor_report=report,
        code_check=results["code_check"],
        llm_judge=results["llm_judge"],
        reference_score=results["reference_score"],
        human_eval=None,
        agreement_matrix=agreement_matrix,
        disagreement_count=disagreement_count,
    )


def run_eval_live(scenario: EvalScenario, client) -> EvalResult:
    """Live path — run the real JDQI pipeline on the scenario's JD, then evaluate
    the FRESH advisor report against the scenario's gold reference.

    Mirrors scripts/cache_scenario.py: parser -> specialists -> synthesizer ->
    advisor. Guardrails are intentionally skipped (they gate the live UI tabs,
    not eval runs). The fresh report replaces cached_advisor_report before the
    three evaluators run via run_eval.
    """
    from agents.parser import run_parser
    from agents.specialists import run_specialists
    from agents.synthesizer import run_synthesizer

    context = {"jd_text": scenario["jd_text"], "industry": scenario["industry"]}
    context["parsed_jd"] = run_parser(scenario["jd_text"], scenario["industry"], client)
    context["dimension_results"] = run_specialists(
        scenario["jd_text"], context["parsed_jd"], client)
    fresh_report = run_synthesizer(context, client)

    live_scenario = {**scenario, "cached_advisor_report": fresh_report}
    return run_eval(live_scenario)
