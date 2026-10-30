"""TypedDicts for the Eval Observatory domain.

Extends (never replaces) models/context.py. These schemas describe the four
evaluator results, the assembled EvalResult, and the pre-cached EvalScenario
files under data/eval_scenarios/.

All scoring is on the real AdvisorReport scale (dimension scores 0-100,
jdqi_score 0-100, suggested_additions as {section, suggestion, impact} dicts).
"""

from typing import TypedDict, Optional, Literal

EvalVerdict = Literal["pass", "fail", "warn"]


class CodeCheckResult(TypedDict):
    verdict: EvalVerdict
    checks_passed: int
    checks_total: int                    # always 7
    failures: list[str]                  # failure descriptions, empty on pass
    duration_ms: float


class LLMJudgeResult(TypedDict):
    verdict: EvalVerdict
    dimension_support: dict[str, bool]   # keyed by dimension name
    unsupported_scores: list[str]        # dimensions whose score lacks evidence
    vagueness_flags: list[str]           # suggested additions flagged as vague
    reasoning: str                       # judge's one-paragraph explanation
    duration_ms: float


class ReferenceScoreResult(TypedDict):
    verdict: EvalVerdict
    score_delta_avg: float               # mean abs diff, 0-100 scale
    score_deltas: dict[str, float]       # per-dimension deltas
    text_similarity: float               # 0.0-1.0, ROUGE-1 recall
    duration_ms: float


class HumanEvalResult(TypedDict):
    verdict: EvalVerdict                 # set by reviewer in UI
    notes: str
    labeled_at: str                      # ISO timestamp


class GoldReference(TypedDict):
    dimension_scores: dict[str, float]   # human-verified, 0-100 per dimension
    suggested_additions: list[str]       # hand-written ideal suggestions
    notes: str


class EvalScenario(TypedDict):
    scenario_id: str                     # "weak" | "average" | "strong"
    label: str
    jd_text: str
    industry: str
    cached_advisor_report: dict          # full real AdvisorReport
    gold_reference: GoldReference


class EvalResult(TypedDict):
    scenario_id: str
    jd_text: str
    advisor_report: dict                 # real AdvisorReport dict
    code_check: CodeCheckResult
    llm_judge: LLMJudgeResult
    reference_score: ReferenceScoreResult
    human_eval: Optional[HumanEvalResult]
    agreement_matrix: dict[str, EvalVerdict]  # {"code":..., "llm_judge":..., "reference":...}
    disagreement_count: int              # automated evaluators diverging from the majority
