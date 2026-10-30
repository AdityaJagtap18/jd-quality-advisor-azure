# JDQI Eval Observatory — Technical Spec

**Handover to:** Claude Code  
**Project root:** `/Users/vrln/jdqi-eval`  
**Project reference to use for example code:** `/Users/vrln/agent-orchestration` 
**Branch:** create `feature/eval-observatory` from `master`  
**Spec version:** 1.0  

---

## 1. What We Are Building

A third tab — **"🔬 Eval Observatory"** — added to the existing Streamlit app (`app.py`). It places the JDQI pipeline's own advisor reports under a 4-panel evaluation harness. The harness runs four evaluator types in parallel and surfaces where they agree and where they disagree.

**The thesis:** The existing app tells users whether their JD is good. This tab tells users whether they can trust the pipeline's judgment — and makes visible the exact failure modes each evaluator type catches.

This tab is **demo-first**. It loads pre-cached JD scenarios from `data/eval_scenarios/` to ensure instant, reliable results during a 20-minute presentation. It can also run live against the real pipeline, but the default is cached.

---

## 2. Repo Context

Read `CLAUDE.md` and `README.md` before writing any code from Project Referenced here. Key facts:

- Existing pipeline lives in `agents/` — do not modify any existing agent files
- Shared TypedDicts are in `models/context.py` — extend, do not replace
- `ThreadPoolExecutor` is the parallelism pattern — reuse it, do not introduce asyncio
- Guardrails (`agents/guardrails.py`) are only invoked on live pipeline runs, not on cached evals
- Models in use: `claude-sonnet-4-6` (Sonnet), `claude-haiku-4-5-20251001` (Haiku), `claude-opus-4-6` (Opus)
- All new agents follow the existing pattern: plain function, returns a TypedDict, no classes

---

## 3. New Files to Create in Project Root

```
agents/
  eval_code_checker.py        # Evaluator 1: rule/code-based
  eval_llm_judge.py           # Evaluator 2: LLM-as-judge (Sonnet)
  eval_reference_scorer.py    # Evaluator 3: reference-based scoring
  eval_orchestrator.py        # Runs all 4 evaluators in parallel, returns EvalResult

data/
  eval_scenarios/
    scenario_weak.json         # Pre-cached: weak JD + cached advisor report + gold reference
    scenario_average.json      # Pre-cached: average JD + cached advisor report + gold reference
    scenario_strong.json       # Pre-cached: strong JD + cached advisor report + gold reference

models/
  eval_context.py              # New TypedDicts for eval domain

pages/
  eval_observatory.py          # Streamlit tab UI (imported in app.py)

tests/
  test_eval_code_checker.py
  test_eval_llm_judge.py
  test_eval_reference_scorer.py

scripts/
  cache_scenario.py            # One-off CLI: runs live JDQI pipeline on a JD, saves output as scenario JSON
```

---

## 4. Data Contracts

### 4.1 New TypedDicts — `models/eval_context.py`

```python
from typing import TypedDict, Optional, Literal

EvalVerdict = Literal["pass", "fail", "warn"]

class CodeCheckResult(TypedDict):
    verdict: EvalVerdict             # "pass" | "fail" | "warn"
    checks_passed: int
    checks_total: int
    failures: list[str]              # list of failure descriptions, empty on pass
    duration_ms: float

class LLMJudgeResult(TypedDict):
    verdict: EvalVerdict
    dimension_support: dict[str, bool]  # keyed by dimension name, True = score is supported by evidence
    unsupported_scores: list[str]        # dimension names where score lacks evidence
    vagueness_flags: list[str]           # suggested additions flagged as vague
    reasoning: str                       # judge's one-paragraph explanation
    duration_ms: float

class ReferenceScoreResult(TypedDict):
    verdict: EvalVerdict
    score_delta_avg: float               # mean absolute diff between pipeline scores and gold scores
    score_deltas: dict[str, float]       # per-dimension deltas
    text_similarity: float               # 0.0–1.0, rough ROUGE-1 recall of suggestions vs gold
    duration_ms: float

class HumanEvalResult(TypedDict):
    verdict: EvalVerdict                 # set by reviewer in UI
    notes: str                           # free text from reviewer
    labeled_at: str                      # ISO timestamp

class EvalResult(TypedDict):
    scenario_id: str
    jd_text: str
    advisor_report: dict                 # the JDQIContext advisor_report field (or cached equivalent)
    code_check: CodeCheckResult
    llm_judge: LLMJudgeResult
    reference_score: ReferenceScoreResult
    human_eval: Optional[HumanEvalResult]   # None until reviewer submits
    agreement_matrix: dict[str, EvalVerdict]  # {"code": "pass", "llm_judge": "fail", ...}
    disagreement_count: int              # how many evaluators diverge from the majority verdict

class EvalScenario(TypedDict):
    scenario_id: str                     # "weak" | "average" | "strong"
    label: str                           # display name, e.g. "Weak JD — Vague + Inflated"
    jd_text: str
    industry: str
    cached_advisor_report: dict          # full JDQIContext as produced by the live pipeline
    gold_reference: GoldReference

class GoldReference(TypedDict):
    dimension_scores: dict[str, float]   # human-verified scores, 0–5 per dimension
    suggested_additions: list[str]       # hand-written ideal suggestions
    notes: str                           # why this is the gold answer
```

---

## 5. Evaluator Implementations

### 5.1 Code Checker — `agents/eval_code_checker.py`

**Purpose:** Deterministic validation of the advisor report's structure and internal consistency. No LLM call. Runs in under 50ms.

**Function signature:**
```python
def run_code_check(advisor_report: dict) -> CodeCheckResult:
```

**Checks to implement (in order):**

| # | Check | Pass condition | Failure label |
|---|-------|---------------|---------------|
| 1 | Schema completeness | All 6 dimension keys present in report: `completeness`, `skill_specificity`, `role_coherence`, `cognitive_load`, `inclusion_signals`, `compensation` | `"Missing dimension key: {key}"` |
| 2 | Score range | Each dimension score is a float between 0.0 and 5.0 inclusive | `"Score out of range for {dim}: {val}"` |
| 3 | Weighted total consistency | Weighted sum of dimension scores matches reported `overall_score` within ±0.15 | `"Weighted total {computed:.2f} diverges from reported {reported:.2f}"` |
| 4 | Evidence present | Each dimension result contains a non-empty `evidence` or `reasoning` string (≥10 chars) | `"No evidence string for dimension: {dim}"` |
| 5 | Suggested additions non-empty | `suggested_additions` list contains at least 1 item | `"No suggested additions returned"` |
| 6 | Suggested additions are strings | All items in `suggested_additions` are non-empty strings | `"suggested_additions contains non-string or empty item"` |
| 7 | Overall score present | `overall_score` key exists and is a float 0–100 | `"overall_score missing or out of range"` |

**Dimension weights** (must match `CLAUDE.md`):
```python
DIMENSION_WEIGHTS = {
    "completeness": 0.25,
    "skill_specificity": 0.20,
    "role_coherence": 0.20,
    "cognitive_load": 0.15,
    "inclusion_signals": 0.10,
    "compensation": 0.10,
}
```

**Verdict logic:**
- `"fail"` if any check 1–3 or 7 fails (structural/math integrity)
- `"warn"` if only checks 4–6 fail (content quality issues)
- `"pass"` if all checks pass

---

### 5.2 LLM-as-Judge — `agents/eval_llm_judge.py`

**Purpose:** A separate Sonnet call that scrutinises the advisor report against a rubric. Catches cases where scores are present but unsupported by evidence, and where suggestions are vague. Target: ~3s.

**Function signature:**
```python
def run_llm_judge(jd_text: str, advisor_report: dict) -> LLMJudgeResult:
```

**Model:** `claude-sonnet-4-6`  
**Max tokens:** `1024`  
**Response format:** JSON only (instruct strictly, parse with `_extract_json()` — copy the helper from `agents/advisor.py`)

**System prompt:**
```
You are a strict evaluator of job description quality reports. You receive a JD and an AI-generated quality report. Your job is to assess whether each dimension score in the report is supported by concrete evidence from the JD text.

Return ONLY a JSON object. No preamble, no explanation outside the JSON.
```

**User prompt template:**
```
JD TEXT:
{jd_text}

ADVISOR REPORT:
{advisor_report_json}

Evaluate the report against these criteria:

1. DIMENSION SUPPORT: For each of the 6 dimensions (completeness, skill_specificity, role_coherence, cognitive_load, inclusion_signals, compensation), does the evidence/reasoning in the report actually reference specific content from the JD? Mark true if yes, false if the score appears unsupported or generic.

2. VAGUENESS FLAGS: List any items in suggested_additions that are vague — i.e., they don't tell the hiring manager exactly what to add. Example of vague: "Add more detail about skills." Example of specific: "Add required proficiency level (beginner/intermediate/expert) for each tool listed."

3. REASONING: Write one paragraph explaining your overall verdict.

4. VERDICT: "pass" if ≥5 dimensions are supported and ≤1 vagueness flag. "warn" if 3–4 dimensions supported or 2–3 vagueness flags. "fail" if ≤2 dimensions supported or ≥4 vagueness flags.

Return this exact JSON structure:
{
  "dimension_support": {
    "completeness": true,
    "skill_specificity": false,
    "role_coherence": true,
    "cognitive_load": true,
    "inclusion_signals": true,
    "compensation": false
  },
  "unsupported_scores": ["skill_specificity", "compensation"],
  "vagueness_flags": ["Add more detail about compensation"],
  "reasoning": "...",
  "verdict": "warn"
}
```

**Error handling:** If the API call fails or JSON cannot be parsed after 2 attempts, return a `LLMJudgeResult` with `verdict="warn"`, all `dimension_support` values `False`, and `reasoning="Judge call failed: {error}"`.

---

### 5.3 Reference Scorer — `agents/eval_reference_scorer.py`

**Purpose:** Numeric comparison of the advisor report's dimension scores against a hand-verified gold reference. Also computes rough text overlap between the pipeline's suggested additions and the gold additions. No LLM call. Runs in under 20ms.

**Function signature:**
```python
def run_reference_score(advisor_report: dict, gold: GoldReference) -> ReferenceScoreResult:
```

**Score delta calculation:**
```python
# For each dimension, compute absolute difference between pipeline score and gold score
# Pipeline scores are 0–5; gold scores are 0–5
score_deltas = {dim: abs(pipeline_score - gold_score) for dim, ... }
score_delta_avg = mean(score_deltas.values())
```

**Text similarity (ROUGE-1 recall, no external library):**
```python
# Tokenise both texts by splitting on whitespace and lowercasing
# Remove stopwords: {"a", "an", "the", "and", "or", "to", "in", "of", "for", "with", "that", "is", "are", "be"}
# ROUGE-1 recall = |tokens(pipeline) ∩ tokens(gold)| / |tokens(gold)|
# Run against: " ".join(pipeline suggested_additions) vs " ".join(gold suggested_additions)
```

**Verdict logic:**
- `"pass"` if `score_delta_avg <= 0.5` and `text_similarity >= 0.4`
- `"warn"` if `score_delta_avg <= 1.0` and `text_similarity >= 0.2`
- `"fail"` otherwise

---

### 5.4 Eval Orchestrator — `agents/eval_orchestrator.py`

**Purpose:** Runs the three automated evaluators in parallel using `ThreadPoolExecutor`, then assembles the `EvalResult`. Human eval is not included here — it is added later in the UI layer.

**Function signature:**
```python
def run_eval(scenario: EvalScenario) -> EvalResult:
```

**Implementation pattern:** Mirror the pattern in `agents/specialists.py` (ThreadPoolExecutor with `max_workers=3`).

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

def run_eval(scenario: EvalScenario) -> EvalResult:
    report = scenario["cached_advisor_report"]
    gold = scenario["gold_reference"]
    jd_text = scenario["jd_text"]

    results = {}
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(run_code_check, report): "code_check",
            executor.submit(run_llm_judge, jd_text, report): "llm_judge",
            executor.submit(run_reference_score, report, gold): "reference_score",
        }
        for future in as_completed(futures):
            key = futures[future]
            results[key] = future.result()

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
```

---

## 6. Pre-Cached Scenario Files

### 6.1 Schema — `data/eval_scenarios/scenario_*.json`

Each file is a serialised `EvalScenario`. Example structure:

```json
{
  "scenario_id": "weak",
  "label": "Weak JD — Vague skills, no compensation, seniority mismatch",
  "industry": "IT/SaaS",
  "jd_text": "...",
  "cached_advisor_report": {
    "dimension_results": {
      "completeness": {"score": 1.5, "evidence": "...", "suggestions": ["..."]},
      "skill_specificity": {"score": 1.0, "evidence": "...", "suggestions": ["..."]},
      "role_coherence": {"score": 2.0, "evidence": "...", "suggestions": ["..."]},
      "cognitive_load": {"score": 3.5, "evidence": "...", "suggestions": ["..."]},
      "inclusion_signals": {"score": 2.5, "evidence": "...", "suggestions": ["..."]},
      "compensation": {"score": 0.0, "evidence": "...", "suggestions": ["..."]}
    },
    "overall_score": 42.5,
    "suggested_additions": ["...", "..."],
    "advisor_report": "..."
  },
  "gold_reference": {
    "dimension_scores": {
      "completeness": 1.5,
      "skill_specificity": 1.0,
      "role_coherence": 2.0,
      "cognitive_load": 3.5,
      "inclusion_signals": 2.5,
      "compensation": 0.0
    },
    "suggested_additions": [
      "Specify React version (16, 17, or 18) and whether TypeScript is required",
      "Add salary band — current JD will be invisible to candidates using salary filters",
      "Remove '10+ years experience' for a mid-level role — this is a legal risk in some jurisdictions"
    ],
    "notes": "Gold reference hand-written by Lax. Scores match pipeline output. Additions are specific and actionable."
  }
}
```

### 6.2 Three Scenarios to Create

Write all three JDs as realistic, India-market tech roles (matching `CLAUDE.md` target segments). Use these profiles:

**`scenario_weak.json`** — IT/SaaS mid-level full-stack engineer  
JD problems: no salary band, "10+ years React" for a mid role, skills listed as nouns only (no proficiency/version), uses "rockstar developer" and "ninja", no growth path, 800+ words with Flesch-Kincaid grade > 14.  
Expected: code check passes (valid JSON), LLM judge fails (unsupported scores), reference score fails (large delta on skill_specificity).

**`scenario_average.json`** — High-tech manufacturing embedded systems engineer  
JD problems: salary band present but vague ("competitive"), Cadence/Altium listed but no version, reporting structure unclear, inclusion signals mixed.  
Expected: code check passes, LLM judge warns (2 unsupported dimensions), reference score warns.

**`scenario_strong.json`** — GCC (India) data engineering lead  
JD: explicit salary band (INR range), each tool with version, clear seniority alignment, Flesch-Kincaid < 10, EEO statement present, career progression described.  
Expected: all three automated evaluators pass. Human eval expected pass.

### 6.3 How to Generate — `scripts/cache_scenario.py`

Write a CLI script that takes a JD text file and industry as arguments, runs the full live JDQI pipeline (tab 1 flow), and saves the output as a scenario JSON to `data/eval_scenarios/`. This is a one-off dev utility, not user-facing.

```python
# Usage:
# python scripts/cache_scenario.py --jd path/to/jd.txt --industry "IT/SaaS" --id weak
```

The script is for dev use only. The three scenario files should be committed with realistic, pre-filled `cached_advisor_report` dicts. Do not leave them empty or as stubs — they are the demo foundation.

---

## 7. UI — `pages/eval_observatory.py`

### 7.1 Tab Integration

In `app.py`, add the third tab in the existing tab declaration:

```python
tab1, tab2, tab3 = st.tabs(["🔍 Analyse JD", "📝 Build JD", "🔬 Eval Observatory"])
with tab3:
    from pages.eval_observatory import render_eval_observatory
    render_eval_observatory()
```

### 7.2 Layout

The page has three sections rendered top-to-bottom:

**Section A — Scenario Selector (top)**

```
┌──────────────────────────────────────────────────────────┐
│  Select scenario          [Weak JD ▼]  [Run Eval]        │
│  ○ Use cached (instant)   ● Run live pipeline (~15s)      │
└──────────────────────────────────────────────────────────┘
```

- Dropdown: three scenarios from `data/eval_scenarios/` — show `label` field
- Toggle: cached vs live (default: cached)
- "Run Eval" button triggers `run_eval()` and stores result in `st.session_state["eval_result"]`
- Show a spinner during eval: "Running 3 evaluators in parallel…"

**Section B — Four Evaluator Panels (main)**

Rendered in a 2×2 grid using `st.columns([1,1])` twice:

```
┌─────────────────────────┬─────────────────────────┐
│  1. Code / rule-based   │  2. LLM-as-judge         │
│  ✅ PASS  7/7 checks    │  ⚠️  WARN                │
│                         │                          │
│  • Weighted total ✓     │  Unsupported scores:     │
│  • All 6 dims present ✓ │  • skill_specificity     │
│  • Score range ✓        │  • compensation          │
│  Duration: 12ms         │                          │
│                         │  Vagueness flags: 1      │
│                         │  Duration: 2.8s          │
├─────────────────────────┼─────────────────────────┤
│  3. Reference-based     │  4. Human eval           │
│  ❌ FAIL                │  [ ] Pass  [ ] Fail      │
│                         │                          │
│  Avg score delta: 1.2   │  Notes:                  │
│  Worst: skill_spec 2.0  │  [________________]      │
│  Text similarity: 0.18  │                          │
│  Duration: 8ms          │  [Submit label]          │
└─────────────────────────┴─────────────────────────┘
```

**Panel styling rules:**
- Verdict badge: use `st.success` / `st.warning` / `st.error` for pass/warn/fail
- Show duration in small gray text
- Expand/collapse detail with `st.expander`
- Human eval panel is always interactive regardless of scenario; does not re-trigger the evaluators

**Section C — Agreement Summary (bottom)**

Rendered after all panels:

```
┌──────────────────────────────────────────────────────────┐
│  Evaluator agreement                                     │
│                                                          │
│  Code ✅   LLM Judge ⚠️   Reference ❌   Human ⚠️       │
│                                                          │
│  Disagreement count: 2 of 4 evaluators diverge          │
│                                                          │
│  Cohen's Kappa (LLM judge vs human, session): 0.73      │
│  Based on 4 labeled samples this session                 │
└──────────────────────────────────────────────────────────┘
```

**Cohen's Kappa implementation:**

Store human labels and LLM judge verdicts in `st.session_state["kappa_data"]` as a list of `(llm_verdict, human_verdict)` tuples. Compute kappa after each new human label:

```python
def compute_kappa(pairs: list[tuple[str, str]]) -> float:
    """Simple Cohen's Kappa for two raters, three categories: pass/warn/fail."""
    # Standard kappa formula: (p_o - p_e) / (1 - p_e)
    # Where p_o = observed agreement, p_e = expected agreement by chance
    # Return 0.0 if fewer than 2 pairs
```

Display the kappa value with a qualitative label:
- ≥ 0.8: "Excellent" (green)
- 0.6–0.79: "Good" (green)
- 0.4–0.59: "Moderate" (orange)
- < 0.4: "Poor — evaluator needs recalibration" (red)

### 7.3 Session State Keys (eval-specific)

| Key | Type | Purpose |
|-----|------|---------|
| `eval_result` | `Optional[EvalResult]` | Current eval result |
| `eval_scenario_id` | `str` | Currently selected scenario |
| `kappa_data` | `list[tuple[str,str]]` | (llm_verdict, human_verdict) history |
| `human_labels` | `dict[str, HumanEvalResult]` | keyed by scenario_id |

---

## 8. LLM-as-Judge Prompt: Alignment Design Note

The LLM judge rubric is intentionally conservative. It should produce `"warn"` or `"fail"` on the weak scenario and `"pass"` on the strong scenario. If during dev the judge passes the weak scenario or fails the strong scenario, adjust only the verdict thresholds in the prompt (the numbers in the `VERDICT` rule), not the JSON structure.

The judge is calling `claude-sonnet-4-6` — same model as the pipeline's synthesis executor. This is deliberate: the point of the demo is that even the same model class can catch its own pipeline's failures when given a structured rubric. Do not upgrade the judge to Opus.

---

## 9. Tests

### `tests/test_eval_code_checker.py`

Minimum 8 tests:
1. All checks pass on a valid report → `verdict="pass"`, `checks_passed=7`
2. Missing dimension key → `verdict="fail"`, failure message contains the key name
3. Score out of range (6.0) → `verdict="fail"`
4. Weighted total diverges by 0.20 → `verdict="fail"`
5. Evidence string missing → `verdict="warn"` (not fail)
6. Empty suggested_additions → `verdict="warn"`
7. overall_score missing → `verdict="fail"`
8. All warn-level failures but no fail-level failures → `verdict="warn"`

### `tests/test_eval_reference_scorer.py`

Minimum 5 tests:
1. Identical scores and suggestions → `verdict="pass"`, delta=0.0, similarity=1.0
2. Large score deltas → `verdict="fail"`
3. Scores close, suggestions divergent → `verdict="warn"`
4. Empty pipeline suggestions → similarity=0.0
5. Stopword-only suggestions → similarity=0.0 (stopwords excluded)

### `tests/test_eval_llm_judge.py`

Mock the Anthropic SDK client (do not make live API calls in tests). Minimum 3 tests:
1. Valid JSON response → parsed correctly into `LLMJudgeResult`
2. Malformed JSON response → falls back to warn verdict with error in reasoning
3. API exception → falls back to warn verdict

---

## 10. Demo Scenarios — Expected Evaluator Outputs

This table drives the three scenario JSON files. Use these expected outputs as the ground truth when writing the scenario files and validating the tests.

| Evaluator | Weak JD | Average JD | Strong JD |
|-----------|---------|------------|-----------|
| Code check | ✅ pass (7/7) | ✅ pass (7/7) | ✅ pass (7/7) |
| LLM judge | ❌ fail (2 dims unsupported, 3 vagueness flags) | ⚠️ warn (2 dims unsupported, 1 vagueness flag) | ✅ pass |
| Reference score | ❌ fail (delta avg >1.0, similarity <0.2) | ⚠️ warn (delta avg ~0.8, similarity ~0.3) | ✅ pass |
| Human eval | ❌ fail | ⚠️ warn | ✅ pass |
| Disagreements | 0 (all fail) | 0 (all warn) | 0 (all pass) |

The weak scenario intentionally has code check pass while the other two fail — this is the key demo moment. It shows that valid JSON does not mean trustworthy output.

---

## 11. What Not to Build

- Do not modify `agents/parser.py`, `agents/specialists.py`, `agents/synthesizer.py`, `agents/advisor.py`, `agents/jd_builder_gather.py`, `agents/jd_builder_draft.py`, or `agents/guardrails.py`
- Do not add any database or file-write functionality beyond the scenario JSONs
- Do not use asyncio — ThreadPoolExecutor only
- Do not add authentication
- Do not build a "history across sessions" feature — session state is in-memory only, resets on page reload
- Do not add streaming to the LLM judge — blocking call, result shown once complete
- Do not add the live pipeline path until all cached functionality works end-to-end

---

## 12. Definition of Done

- [ ] `streamlit run app.py` shows three tabs; third tab loads without error
- [ ] All three scenarios load from JSON and display the 4-panel layout
- [ ] Code checker runs in < 100ms on cached report
- [ ] LLM judge runs in < 6s on cached report
- [ ] Reference scorer runs in < 100ms on cached report
- [ ] Human eval panel accepts a verdict and notes; updates kappa display
- [ ] Cohen's Kappa displays after first human label is submitted
- [ ] Kappa updates correctly across multiple labeled scenarios in one session
- [ ] Weak JD scenario shows code pass / LLM fail / reference fail (key demo moment)
- [ ] Strong JD scenario shows all three automated evaluators pass
- [ ] `python -m pytest tests/ -v` — all existing 21 tests still pass, all new tests pass
- [ ] No import errors, no unhandled exceptions on any scenario
- [ ] `scripts/cache_scenario.py --help` runs without error

---

## 13. Suggested Build Order

1. `models/eval_context.py` — TypedDicts first, everything else depends on them
2. `agents/eval_code_checker.py` + tests — fastest to build, validates the TypedDicts
3. `agents/eval_reference_scorer.py` + tests — no LLM dependency
4. Write the three `data/eval_scenarios/*.json` files — needed before UI and orchestrator can run
5. `agents/eval_orchestrator.py` — wires the three evaluators together
6. `pages/eval_observatory.py` — UI, all evaluators running against cached data
7. `agents/eval_llm_judge.py` + tests — add last; requires mocked tests and live prompt tuning
8. `scripts/cache_scenario.py` — dev utility, can be last
9. End-to-end test of all three scenarios; tune LLM judge thresholds if needed

---

## 14. Key Talking Points the App Must Support (Demo Script)

The app must make these moments possible during the 20-minute presentation:

**Moment 1 — "Code says pass, but the pipeline is still wrong"**  
Load weak scenario. Show code check: 7/7, pass. Immediately show LLM judge: fail, 2 unsupported scores. Say: "Valid JSON is not the same as trustworthy output."

**Moment 2 — "The judge caught what the code missed"**  
Point to `skill_specificity` in LLM judge panel. The score is 1.0 in the advisor report, but the judge flags it as unsupported — the evidence string in the report is generic, not grounded in the JD text. The code checker had no way to catch this.

**Moment 3 — "Disagreement is information"**  
Load average scenario. Reference score says warn. LLM judge says warn. Code says pass. Human eval: mark as warn. Disagreement count = 0, all warn. Say: "When evaluators agree, you have signal. Agreement on warn is as useful as agreement on pass."

**Moment 4 — "Cohen's Kappa — is the judge calibrated?"**  
After labeling all three scenarios, show the kappa display. If kappa is ≥ 0.6, say: "Our LLM judge is in good agreement with human judgment on this task. This is what alignment looks like before you trust it at scale."

---

*End of spec.*
