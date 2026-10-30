# JDQI Eval Observatory — Design Spec

**Date:** 2026-07-01
**Project root:** `/Users/vrln/jdqi-eval` (copied from `/Users/vrln/agent-orchestration`)
**Branch:** `feature/eval-observatory` (from `master`)
**Supersedes/adapts:** `JDQI_EVAL_OBSERVATORY_SPEC.md` (handover spec v1.0)
**Status:** Approved design — ready for implementation plan

---

## 1. What We Are Building

A third tab — **🔬 Eval Observatory** — added to the JDQI Streamlit app (`app.py`). It places the
JDQI pipeline's own advisor reports under a **4-panel evaluation harness** that runs four evaluator
types and surfaces where they agree and where they disagree.

**Thesis:** The existing app tells users whether their JD is good. This tab tells users whether they
can *trust the pipeline's judgment* — and makes visible the exact failure modes each evaluator type
catches.

**Demo-first.** The tab loads pre-cached JD scenarios from `data/eval_scenarios/` for instant,
reliable results during a 20-minute presentation. A live path (run against the real pipeline) is a
later, dev-only addition and is out of scope until all cached functionality works end-to-end.

---

## 2. Two Decisions That Shape This Design

This design differs from the handover spec (`JDQI_EVAL_OBSERVATORY_SPEC.md`) in two approved ways:

### 2.1 Build location — copy the app into `jdqi-eval`

The handover spec named `/Users/vrln/jdqi-eval` as project root, but every integration point
(`app.py`, `agents/`, `models/context.py`) lives in `/Users/vrln/agent-orchestration`. Resolution:
the full `agent-orchestration` app was **copied into `jdqi-eval`** (excluding `.env`, `.venv`,
`__pycache__`, `.pytest_cache`). The reference app stays pristine. All new work happens in
`jdqi-eval` on branch `feature/eval-observatory`.

### 2.2 Report shape — adapt to the real `AdvisorReport`

The handover spec assumed a report shape (`dimension_results` dict, 0–5 scores, `overall_score`,
string `suggested_additions`) that does **not** match the live pipeline. We build against the **real**
`AdvisorReport` (`models/context.py`):

```python
class AdvisorReport(TypedDict):
    jdqi_score: int                              # 0–100, weighted composite
    benchmark_comparison: str
    dimension_breakdown: list[DimensionBreakdown]  # [{dimension, score 0–100, narrative}] × 6
    suggested_additions: list[SuggestedAddition]   # [{section, suggestion, impact}]
```

Consequences: the code checker's checks, the reference scorer's thresholds, the scenario JSONs, and
all tests are written against this shape. `cache_scenario.py` can save live pipeline output directly
with no adapter.

The **six dimensions** (canonical names): `completeness`, `skill_specificity`, `role_coherence`,
`cognitive_load`, `inclusion_signals`, `compensation`.

---

## 3. Files to Create

```
agents/
  eval_code_checker.py        # Evaluator 1: deterministic, no LLM
  eval_llm_judge.py           # Evaluator 2: LLM-as-judge (Sonnet)
  eval_reference_scorer.py    # Evaluator 3: reference-based scoring, no LLM
  eval_orchestrator.py        # Runs evaluators 1–3 in parallel → EvalResult
data/
  eval_scenarios/
    scenario_weak.json
    scenario_average.json
    scenario_strong.json
models/
  eval_context.py             # New TypedDicts for the eval domain
pages/
  eval_observatory.py         # Streamlit tab UI
tests/
  test_eval_code_checker.py
  test_eval_reference_scorer.py
  test_eval_llm_judge.py
scripts/
  cache_scenario.py           # Dev-only CLI: live pipeline → scenario JSON
```

**Do NOT modify** any existing agent file (`parser.py`, `specialists.py`, `synthesizer.py`,
`advisor.py`, `jd_builder_*.py`, `guardrails.py`). The only edit to existing code is adding `tab3`
to `app.py`. Extend `models/` — never edit `context.py`.

---

## 4. Data Contracts — `models/eval_context.py`

```python
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
    score_delta_avg: float               # mean abs diff, 0–100 scale
    score_deltas: dict[str, float]       # per-dimension deltas
    text_similarity: float               # 0.0–1.0, ROUGE-1 recall
    duration_ms: float

class HumanEvalResult(TypedDict):
    verdict: EvalVerdict                 # set by reviewer in UI
    notes: str
    labeled_at: str                      # ISO timestamp

class GoldReference(TypedDict):
    dimension_scores: dict[str, float]   # human-verified, 0–100 per dimension
    suggested_additions: list[str]       # hand-written ideal suggestions
    notes: str

class EvalResult(TypedDict):
    scenario_id: str
    jd_text: str
    advisor_report: dict                 # real AdvisorReport dict
    code_check: CodeCheckResult
    llm_judge: LLMJudgeResult
    reference_score: ReferenceScoreResult
    human_eval: Optional[HumanEvalResult]
    agreement_matrix: dict[str, EvalVerdict]  # {"code":..., "llm_judge":..., "reference":...}
    disagreement_count: int              # evaluators diverging from the majority verdict

class EvalScenario(TypedDict):
    scenario_id: str                     # "weak" | "average" | "strong"
    label: str
    jd_text: str
    industry: str
    cached_advisor_report: dict          # full real AdvisorReport
    gold_reference: GoldReference
```

`GoldReference` and `EvalScenario` must be defined before the results types that reference them.

---

## 5. Evaluator Implementations

### 5.1 Code Checker — `agents/eval_code_checker.py`

Deterministic validation of the advisor report's structure and internal consistency. No LLM. <50ms.

```python
def run_code_check(advisor_report: dict) -> CodeCheckResult:
```

`dimension_breakdown` is a list; build a name→entry map once at the top. Checks run in order:

| # | Check | Pass condition | Failure label | Level |
|---|-------|----------------|---------------|-------|
| 1 | Schema completeness | All 6 dimension names present in `dimension_breakdown` | `"Missing dimension: {name}"` | fail |
| 2 | Score range | Each dimension `score` is a number 0–100 inclusive | `"Score out of range for {dim}: {val}"` | fail |
| 3 | Weighted total | Weighted sum of dimension scores is within **±2.0** of `jdqi_score` | `"Weighted total {computed:.2f} diverges from reported {reported:.2f}"` | fail |
| 4 | Evidence present | Each dimension `narrative` is a string ≥10 chars | `"No narrative for dimension: {dim}"` | warn |
| 5 | Additions non-empty | `suggested_additions` has ≥1 item | `"No suggested additions returned"` | warn |
| 6 | Additions well-formed | Each addition is a dict with a non-empty `suggestion` string | `"suggested_additions contains malformed or empty item"` | warn |
| 7 | Overall score present | `jdqi_score` exists and is a number 0–100 | `"jdqi_score missing or out of range"` | fail |

**Dimension weights** (from `CLAUDE.md`; on the 0–100 scale the weighted sum is directly comparable
to `jdqi_score`):

```python
DIMENSION_WEIGHTS = {
    "completeness": 0.25, "skill_specificity": 0.20, "role_coherence": 0.20,
    "cognitive_load": 0.15, "inclusion_signals": 0.10, "compensation": 0.10,
}
```

**Verdict logic:**
- `"fail"` if any of checks 1, 2, 3, 7 fails (structural / math integrity)
- `"warn"` if only checks 4–6 fail (content quality) and no fail-level check failed
- `"pass"` if all 7 pass

`checks_passed` counts the checks that passed (0–7). `duration_ms` via `time.perf_counter()`.
If checks 1/2/7 fail such that check 3 cannot compute, skip check 3 gracefully (still a fail overall).

### 5.2 LLM-as-Judge — `agents/eval_llm_judge.py`

A separate Sonnet call scrutinising the report against a rubric. Catches present-but-unsupported
scores and vague suggestions. Target ~3s.

```python
def run_llm_judge(jd_text: str, advisor_report: dict) -> LLMJudgeResult:
```

- **Model:** `claude-sonnet-4-6` · **max_tokens:** 1024 · **JSON-only** response.
- Build the client internally (mirror `app.py:_get_client` — `anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))`), since the orchestrator submits this function with no client argument.
- Parse with a local copy of `_extract_json()` (copy from `agents/advisor.py` — do not import across agent modules; keep evaluators self-contained).
- The prompt directs the judge at each dimension's **`narrative`** field (real shape) when assessing whether a score is grounded in the JD.

**System prompt:**
```
You are a strict evaluator of job description quality reports. You receive a JD and an AI-generated
quality report. Your job is to assess whether each dimension score in the report is supported by
concrete evidence from the JD text.

Return ONLY a JSON object. No preamble, no explanation outside the JSON.
```

**User prompt** (template): includes `jd_text` and `json.dumps(advisor_report)`, then the four
criteria — (1) DIMENSION SUPPORT: for each of the 6 dimensions, does the `narrative` reference
specific JD content? true/false; (2) VAGUENESS FLAGS: list `suggested_additions` whose `suggestion`
text is vague (doesn't tell the hiring manager exactly what to add); (3) REASONING: one paragraph;
(4) VERDICT: `"pass"` if ≥5 dimensions supported and ≤1 vagueness flag; `"warn"` if 3–4 supported or
2–3 flags; `"fail"` if ≤2 supported or ≥4 flags. Return the exact JSON structure with keys
`dimension_support`, `unsupported_scores`, `vagueness_flags`, `reasoning`, `verdict`.

**Error handling:** on API failure or unparseable JSON after 2 attempts, return `verdict="warn"`,
all `dimension_support` False, `unsupported_scores=[]`, `vagueness_flags=[]`,
`reasoning="Judge call failed: {error}"`. Never raise.

**Threshold-tuning note:** if during dev the judge passes the weak scenario or fails the strong one,
adjust only the VERDICT numbers in the prompt — never the JSON structure, never upgrade to Opus.

### 5.3 Reference Scorer — `agents/eval_reference_scorer.py`

Numeric comparison of the report's dimension scores against a hand-verified gold reference, plus
rough text overlap of suggestions. No LLM. <20ms.

```python
def run_reference_score(advisor_report: dict, gold: GoldReference) -> ReferenceScoreResult:
```

**Score deltas** (0–100 scale): for each of the 6 dimensions, `abs(pipeline_score - gold_score)`
where pipeline scores come from `dimension_breakdown`. `score_delta_avg = mean(deltas)`.

**Text similarity — ROUGE-1 recall, no external library:**
- Pipeline text = `" ".join(a["suggestion"] for a in advisor_report["suggested_additions"])`
- Gold text = `" ".join(gold["suggested_additions"])`
- Tokenise on whitespace, lowercase, strip punctuation. Remove stopwords:
  `{"a","an","the","and","or","to","in","of","for","with","that","is","are","be"}`
- `recall = |tokens(pipeline) ∩ tokens(gold)| / |tokens(gold)|`; 0.0 if gold tokenset empty.

**Verdict logic (rescaled to 0–100):**
- `"pass"` if `score_delta_avg <= 10` **and** `text_similarity >= 0.4`
- `"warn"` if `score_delta_avg <= 20` **and** `text_similarity >= 0.2`
- `"fail"` otherwise

### 5.4 Orchestrator — `agents/eval_orchestrator.py`

Runs evaluators 1–3 in parallel via `ThreadPoolExecutor(max_workers=3)` (mirror
`specialists.py`'s `future_to_key` + `as_completed` pattern), then assembles `EvalResult`.
Human eval is added later in the UI, not here.

```python
def run_eval(scenario: EvalScenario) -> EvalResult:
```

- Submit `run_code_check(report)`, `run_llm_judge(jd_text, report)`, `run_reference_score(report, gold)`.
- `agreement_matrix = {"code":..., "llm_judge":..., "reference":...}`.
- `majority = max(set(verdicts), key=verdicts.count)`; `disagreement_count = count of verdicts != majority`.
- `human_eval=None`.

---

## 6. Pre-Cached Scenarios — `data/eval_scenarios/scenario_*.json`

Each file is a serialised `EvalScenario`. `cached_advisor_report` uses the **real `AdvisorReport`
shape** (`jdqi_score`, `dimension_breakdown` list, structured `suggested_additions`,
`benchmark_comparison`). `gold_reference.dimension_scores` are 0–100. JDs are realistic India-market
tech roles. Files are committed fully filled — never stubs.

| Scenario | Role | JD problems | Expected: Code / Judge / Reference |
|----------|------|-------------|-------------------------------------|
| `scenario_weak` | IT/SaaS mid-level full-stack | no salary band; "10+ years React" for a mid role; skills as bare nouns; "rockstar"/"ninja"; no growth path; 800+ words, FK grade >14 | ✅ pass (7/7) / ❌ fail (≤2 dims supported, ≥3 vagueness flags) / ❌ fail (delta_avg >20, sim <0.2) |
| `scenario_average` | High-tech mfg embedded systems | salary "competitive" (vague); Cadence/Altium, no versions; unclear reporting; mixed inclusion signals | ✅ pass (7/7) / ⚠️ warn (2 dims unsupported, 1 flag) / ⚠️ warn (delta_avg ~15, sim ~0.3) |
| `scenario_strong` | GCC India data engineering lead | explicit INR salary band; each tool + version; clear seniority; FK <10; EEO statement; career progression | ✅ pass / ✅ pass / ✅ pass |

**Key demo moment (weak):** code check passes while judge + reference fail — "valid JSON ≠ trustworthy
output." Because the code checker always passes on structurally valid JSON, it *diverges* from the
majority verdict on both weak and average scenarios. With `majority = max(set(verdicts), key=count)`:
- weak → `[pass, fail, fail]` → majority `fail`, **disagreement_count = 1** (code diverges)
- average → `[pass, warn, warn]` → majority `warn`, **disagreement_count = 1** (code diverges)
- strong → `[pass, pass, pass]` → majority `pass`, **disagreement_count = 0**

(The handover spec §10 stated 0 disagreements for weak/average; that is incorrect given code always
passes — this design uses the computed values above. That very divergence is the demo's point:
"the code checker cannot see what the judge and reference catch.")

The gold references' `dimension_scores` are set close to the cached report's scores for `average`/
`strong` (so reference passes/warns) and far from them on the weakest dimensions for `weak` (so
reference fails). Gold `suggested_additions` are specific, actionable strings whose token overlap
with the cached report's suggestions produces the target similarity band.

---

## 7. UI — `pages/eval_observatory.py`

### 7.1 Tab integration (`app.py`)

Change the existing `tab1, tab2 = st.tabs([...])` (currently `app.py:466`) to:

```python
tab1, tab2, tab3 = st.tabs(["🔍 Analyse JD", "📝 Build JD", "🔬 Eval Observatory"])
with tab3:
    from pages.eval_observatory import render_eval_observatory
    render_eval_observatory()
```

### 7.2 Layout — three sections, top to bottom

**A — Scenario selector.** Dropdown of the three scenarios (show `label`); cached/live toggle
(default cached; live is stubbed/disabled until the live path is built); **Run Eval** button →
`run_eval()`, store in `st.session_state["eval_result"]`. Spinner: "Running 3 evaluators in parallel…".

**B — Four evaluator panels**, 2×2 grid via `st.columns([1,1])` twice:
1. **Code / rule-based** — verdict badge, `checks_passed/checks_total`, per-check list, duration.
2. **LLM-as-judge** — verdict, `unsupported_scores`, vagueness flag count, reasoning in an expander, duration.
3. **Reference-based** — verdict, `score_delta_avg`, worst dimension delta, `text_similarity`, duration.
4. **Human eval** — pass/warn/fail selector + notes text area + Submit; always interactive; does
   **not** re-trigger the automated evaluators. On submit, writes `HumanEvalResult` into
   `st.session_state["human_labels"][scenario_id]` and appends `(llm_verdict, human_verdict)` to
   `st.session_state["kappa_data"]`.

**Panel styling:** verdict badge via `st.success`/`st.warning`/`st.error` for pass/warn/fail;
duration in small gray caption; detail behind `st.expander`.

**C — Agreement summary** (after panels): one-line verdict row (Code / LLM Judge / Reference / Human
with emoji). The count reads `{disagreement_count} of 3 automated evaluators diverge` — this is the
`EvalResult.disagreement_count` computed over the three automated verdicts only (human is shown in
the row for context but is not part of the stored count, since it is labeled after `run_eval`). Then
**Cohen's Kappa** (LLM judge vs human) computed over `st.session_state["kappa_data"]`.

### 7.3 Cohen's Kappa

```python
def compute_kappa(pairs: list[tuple[str, str]]) -> float:
    """Cohen's Kappa, two raters, three categories pass/warn/fail. (p_o - p_e)/(1 - p_e).
    Return 0.0 if fewer than 2 pairs, or if p_e == 1.0 (avoid div-by-zero)."""
```

Qualitative label: ≥0.8 "Excellent" (green) · 0.6–0.79 "Good" (green) · 0.4–0.59 "Moderate" (orange)
· <0.4 "Poor — evaluator needs recalibration" (red). Shown with sample count.

### 7.4 Session-state keys

| Key | Type | Purpose |
|-----|------|---------|
| `eval_result` | `Optional[EvalResult]` | current result |
| `eval_scenario_id` | `str` | selected scenario |
| `kappa_data` | `list[tuple[str,str]]` | (llm_verdict, human_verdict) history |
| `human_labels` | `dict[str, HumanEvalResult]` | keyed by scenario_id |

All in-memory; resets on reload. No cross-session persistence.

---

## 8. `scripts/cache_scenario.py` (dev-only, built last)

```
python scripts/cache_scenario.py --jd path/to/jd.txt --industry "IT/SaaS" --id weak
```

Runs the live JDQI pipeline (parser → specialists → synthesizer → advisor — the tab-1 flow, skipping
guardrails since guardrails only gate live UI runs) and writes a scenario JSON to
`data/eval_scenarios/`. Because we build to the real `AdvisorReport`, the pipeline's output is saved
directly with no shape adapter. `gold_reference` is left as a hand-fill TODO in the emitted file
(the committed scenarios are hand-authored, not machine-generated). `--help` must work without error.

---

## 9. Tests

Run inside the project venv. All existing 21 tests must still pass; new tests added:

**`tests/test_eval_code_checker.py`** (≥8):
1. Valid report → `verdict="pass"`, `checks_passed=7`
2. Missing dimension → `verdict="fail"`, failure mentions the name
3. Score out of range (e.g. 120) → `fail`
4. Weighted total diverges by >2.0 → `fail`
5. Missing narrative only → `warn` (not fail)
6. Empty `suggested_additions` → `warn`
7. `jdqi_score` missing → `fail`
8. Only warn-level failures → `warn`

**`tests/test_eval_reference_scorer.py`** (≥5):
1. Identical scores + suggestions → `pass`, delta 0.0, similarity 1.0
2. Large deltas → `fail`
3. Close scores, divergent suggestions → `warn`
4. Empty pipeline suggestions → similarity 0.0
5. Stopword-only suggestions → similarity 0.0

**`tests/test_eval_llm_judge.py`** (≥3, mock the Anthropic client — no live calls):
1. Valid JSON response → parsed into `LLMJudgeResult`
2. Malformed JSON → warn fallback with error in reasoning
3. API exception → warn fallback

---

## 10. Constraints (What Not to Build)

- No asyncio — `ThreadPoolExecutor` only.
- No database / file writes beyond the scenario JSONs.
- No auth. No cross-session history. No streaming in the judge (blocking call).
- Do not modify existing agent files; only `app.py` gets the `tab3` edit.
- Do not build the live pipeline path until all cached functionality works end-to-end.
- Guardrails are not invoked on cached evals.

---

## 11. Definition of Done

- `streamlit run app.py` shows three tabs; the third loads with no error.
- All three scenarios load from JSON and render the 4-panel layout.
- Code checker <100ms; reference scorer <100ms; LLM judge <6s on cached reports.
- Human eval accepts verdict + notes and updates the Kappa display; Kappa appears after the first
  label and updates across multiple labels in one session.
- Weak scenario shows code **pass** / judge **fail** / reference **fail** (key demo moment).
- Strong scenario shows all three automated evaluators pass.
- `python -m pytest tests/ -v` — all existing 21 tests pass, all new tests pass.
- No import errors or unhandled exceptions on any scenario.
- `python scripts/cache_scenario.py --help` runs without error.

---

## 12. Build Order

1. `models/eval_context.py` — TypedDicts.
2. `agents/eval_code_checker.py` + tests.
3. `agents/eval_reference_scorer.py` + tests.
4. `data/eval_scenarios/*.json` — three hand-authored scenarios.
5. `agents/eval_orchestrator.py`.
6. `pages/eval_observatory.py` + `app.py` tab wiring.
7. `agents/eval_llm_judge.py` + mocked tests; tune thresholds against scenarios.
8. `scripts/cache_scenario.py`.
9. End-to-end verification of all three scenarios; full test suite.

---

## 13. Demo Talking Points the App Must Support

1. **"Code says pass, but the pipeline is still wrong"** — weak scenario: code 7/7 pass, judge fail.
2. **"The judge caught what the code missed"** — `skill_specificity` scored but flagged unsupported;
   the code checker had no way to catch a generic narrative.
3. **"Disagreement is information"** — average scenario: all warn; agreement on warn is signal too.
4. **"Cohen's Kappa — is the judge calibrated?"** — after labeling all three, kappa ≥0.6 shows the
   judge is in good agreement with human judgment before trusting it at scale.

*End of spec.*
