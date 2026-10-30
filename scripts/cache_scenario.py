"""Dev-only CLI — run the live JDQI pipeline on a JD and save it as a scenario JSON.

Runs the tab-1 flow (parser -> specialists -> synthesizer -> advisor), skipping
guardrails (guardrails only gate live UI runs, not cached evals). Because the
Eval Observatory is built against the real AdvisorReport shape, the pipeline's
output is saved directly with no adapter.

The gold_reference is emitted as a hand-fill stub — the committed demo scenarios
are hand-authored, not machine-generated. Fill dimension_scores (0-100),
suggested_additions, and notes by hand after generating.

Usage:
    python scripts/cache_scenario.py --jd path/to/jd.txt --industry "IT/SaaS" --id weak
"""

import argparse
import json
import os
import sys

# allow running as `python scripts/cache_scenario.py` from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DIMENSIONS = ["completeness", "skill_specificity", "role_coherence",
              "cognitive_load", "inclusion_signals", "compensation"]

_OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "data", "eval_scenarios")


def _build_client():
    from agents.llm_client import build_client
    try:
        return build_client()
    except RuntimeError as e:
        sys.exit(f"{e}. Export them before running this script.")


def _run_pipeline(jd_text: str, industry: str) -> dict:
    from agents.parser import run_parser
    from agents.specialists import run_specialists
    from agents.synthesizer import run_synthesizer

    client = _build_client()
    context = {"jd_text": jd_text, "industry": industry}
    print("→ Agent 1: parser…", flush=True)
    context["parsed_jd"] = run_parser(jd_text, industry, client)
    print("→ Agents 2–7: specialists (parallel)…", flush=True)
    context["dimension_results"] = run_specialists(jd_text, context["parsed_jd"], client)
    print("→ Agents 8–9: synthesizer → advisor…", flush=True)
    context["advisor_report"] = run_synthesizer(context, client)
    return context["advisor_report"]


def _gold_stub() -> dict:
    return {
        "dimension_scores": {d: 0.0 for d in DIMENSIONS},
        "suggested_additions": ["TODO: hand-write ideal, specific suggestions"],
        "notes": "TODO: hand-fill gold reference. dimension_scores are 0-100.",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the live JDQI pipeline on a JD and save a scenario JSON.")
    parser.add_argument("--jd", required=True, help="Path to a .txt file with the JD text")
    parser.add_argument("--industry", required=True, help='e.g. "IT/SaaS"')
    parser.add_argument("--id", required=True, help='scenario id, e.g. "weak"')
    parser.add_argument("--label", default=None, help="display label (defaults to the id)")
    parser.add_argument("--out-dir", default=_OUT_DIR, help="output directory")
    args = parser.parse_args(argv)

    with open(args.jd) as f:
        jd_text = f.read().strip()

    advisor_report = _run_pipeline(jd_text, args.industry)

    scenario = {
        "scenario_id": args.id,
        "label": args.label or f"{args.id} JD",
        "industry": args.industry,
        "jd_text": jd_text,
        "cached_advisor_report": advisor_report,
        "gold_reference": _gold_stub(),
    }

    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"scenario_{args.id}.json")
    with open(out_path, "w") as f:
        json.dump(scenario, f, indent=2, ensure_ascii=False)

    print(f"\n✅ Wrote {out_path}")
    print("   Remember to hand-fill gold_reference before using this as a demo scenario.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
