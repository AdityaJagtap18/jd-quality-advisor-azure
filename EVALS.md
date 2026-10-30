# EVALS.md — How to Interpret the JDQI Eval Observatory

This guide explains the **🔬 Eval Observatory** tab: what it measures, how to read every
number and badge on the screen, and the three demo scenarios shipped in
`data/eval_scenarios/`.

If you only read one thing, read [The Core Idea](#1-the-core-idea) and
[Reading a Verdict at a Glance](#3-reading-a-verdict-at-a-glance).

---

## Table of Contents

1. [The Core Idea](#1-the-core-idea)
2. [What Gets Evaluated](#2-what-gets-evaluated)
3. [Reading a Verdict at a Glance](#3-reading-a-verdict-at-a-glance)
4. [The Four Evaluators in Detail](#4-the-four-evaluators-in-detail)
5. [The Agreement Panel & Cohen's Kappa](#5-the-agreement-panel--cohens-kappa)
6. [The Three Demo Scenarios](#6-the-three-demo-scenarios)
7. [Cached vs Live Mode](#7-cached-vs-live-mode)
8. [Running the Evals](#8-running-the-evals)
9. [Adding Your Own Scenario](#9-adding-your-own-scenario)
10. [Interpretation Cheat-Sheet](#10-interpretation-cheat-sheet)

---

## 1. The Core Idea

The **Analyse JD** and **Build JD** tabs answer: *"Is this job description any good?"*

The **Eval Observatory** answers a different, harder question:

> **"Can I trust the pipeline's answer?"**

An AI pipeline can return a perfectly-formatted, internally-consistent report that is still
**wrong** — scores that aren't backed by the JD, suggestions too vague to act on, or numbers
that drift from what a human expert would give. A single evaluator can't catch all of these,
because each evaluator type has a different blind spot.

So we run **four evaluators of different kinds** over the same advisor report and show where
they **agree** and where they **disagree**. The design principle:

> **Agreement is signal. Disagreement is information.**
> When four independent evaluators all say *pass*, you can trust it. When they split, the
> split tells you exactly *which* failure mode is in play.

The single most important demo moment: on the **weak** scenario, the deterministic code
checker says **PASS** while the LLM judge and reference scorer say **FAIL**. That is the
whole point — **valid JSON is not the same as trustworthy output.**

---

## 2. What Gets Evaluated

The object under evaluation is one **AdvisorReport** — the output of the Analyse JD pipeline
(Agent 9, Opus). Its real shape:

```jsonc
{
  "jdqi_score": 39,                      // overall score, 0–100
  "benchmark_comparison": "…",           // prose comparison vs an ideal JD
  "dimension_breakdown": [               // one entry per JDQI dimension
    { "dimension": "completeness",      "score": 45, "narrative": "…" },
    { "dimension": "skill_specificity", "score": 40, "narrative": "…" },
    { "dimension": "role_coherence",    "score": 35, "narrative": "…" },
    { "dimension": "cognitive_load",    "score": 40, "narrative": "…" },
    { "dimension": "inclusion_signals", "score": 55, "narrative": "…" },
    { "dimension": "compensation",      "score": 10, "narrative": "…" }
  ],
  "suggested_additions": [               // actionable fixes
    { "section": "Skills", "suggestion": "…", "impact": "…" }
  ]
}
```

> **Scoring scale:** every dimension score **and** `jdqi_score` are on a **0–100** scale.
> (The original spec drafted a 0–5 scale; the shipped implementation uses 0–100 to match the
> real AdvisorReport. All thresholds in this doc are 0–100.)

Each scenario also carries a **gold reference** — a human-verified answer key used by the
reference scorer:

```jsonc
"gold_reference": {
  "dimension_scores": {                  // what an expert would score, 0–100
    "completeness": 20, "skill_specificity": 10, "role_coherence": 15,
    "cognitive_load": 25, "inclusion_signals": 30, "compensation": 0
  },
  "suggested_additions": [               // hand-written *ideal* suggestions (plain strings)
    "Specify the exact React version (17 or 18) and whether TypeScript is required",
    "Add an explicit INR salary band — the JD is invisible to salary-filtered searches"
  ],
  "notes": "why this is the gold answer"
}
```

---

## 3. Reading a Verdict at a Glance

Every evaluator returns one of three verdicts:

| Badge | Verdict | Meaning | What to do |
|:-----:|---------|---------|------------|
| ✅ | **PASS** | Output is trustworthy on this evaluator's axis | Ship / trust it |
| ⚠️ | **WARN** | Usable but has quality gaps; not a hard failure | Review the flagged items before trusting |
| ❌ | **FAIL** | Output is not trustworthy on this axis | Do **not** trust; investigate the failure |

The verdict is **per-evaluator**, not global. A report can be ✅ on the code checker and ❌
on the reference scorer at the same time — that combination is itself the diagnosis (the
report is *well-formed but inaccurate*).

---

## 4. The Four Evaluators in Detail

Three run automatically and in parallel (`ThreadPoolExecutor`, `max_workers=3`); the fourth
is the human reviewer.

```
                        ┌─ 1. Code Checker     (rules, no LLM, <50ms)
run_eval(scenario) ─────┼─ 2. LLM-as-Judge     (Sonnet, ~3s)
                        └─ 3. Reference Scorer (numeric vs gold, <20ms)
                             + 4. Human Eval    (you, in the UI)
```

### Evaluator 1 — Code Checker  (`agents/eval_code_checker.py`)

**Kind:** deterministic rules. **No LLM.** Runs in well under 50 ms.
**Question it answers:** *Is the report structurally valid and internally consistent?*

It runs **7 checks**. Four are **fail-level** (structural / math integrity) and three are
**warn-level** (content quality):

| # | Check | Passes when | Level |
|---|-------|-------------|:-----:|
| 1 | Schema completeness | all 6 dimensions present in `dimension_breakdown` | **fail** |
| 2 | Score range | every dimension `score` is a number in `0–100` | **fail** |
| 3 | Weighted-total consistency | `Σ(score × weight)` is within **±2.0** of `jdqi_score` | **fail** |
| 7 | Overall score present | `jdqi_score` exists and is a number in `0–100` | **fail** |
| 4 | Evidence present | every dimension has a `narrative` string ≥ 10 chars | warn |
| 5 | Suggestions non-empty | `suggested_additions` has ≥ 1 item | warn |
| 6 | Suggestions well-formed | every addition is a dict with a non-empty `suggestion` string | warn |

**Dimension weights** (must match `CLAUDE.md`):

```
completeness 0.25 · skill_specificity 0.20 · role_coherence 0.20
cognitive_load 0.15 · inclusion_signals 0.10 · compensation 0.10
```

**Verdict logic:**
- **FAIL** if *any* of checks 1, 2, 3, 7 fail (a broken or math-inconsistent report).
- **WARN** if only checks 4–6 fail (report is valid, but thin on evidence or suggestions).
- **PASS** if all 7 pass. The UI shows `checks_passed / 7`.

**Blind spot:** it validates *form*, never *truth*. A report can score every dimension
wildly wrong and still pass all 7 checks, as long as the numbers are in range and the
weighted total adds up. This is exactly why the weak scenario passes here.

---

### Evaluator 2 — LLM-as-Judge  (`agents/eval_llm_judge.py`)

**Kind:** a **separate Sonnet call** (`claude-sonnet-4-6`, `max_tokens=1024`, up to 2 JSON
parse attempts). **Question it answers:** *Are the scores actually grounded in the JD, and
are the suggestions specific enough to act on?*

It is given the raw JD **and** the advisor report, and returns strict JSON:

- **`dimension_support`** — per dimension, `true` if the report's `narrative` cites concrete
  content from the JD, `false` if it's generic boilerplate that "could apply to any JD."
- **`unsupported_scores`** — the dimensions marked `false`.
- **`vagueness_flags`** — suggestions too vague to act on
  (*"Add more detail about skills"* is vague; *"Add the required proficiency level per tool"*
  is specific).
- **`reasoning`** — one paragraph explaining the verdict.

**Verdict logic (from the rubric in the prompt):**
- **PASS** — ≥ 5 dimensions supported **and** ≤ 1 vagueness flag.
- **WARN** — 3–4 dimensions supported **or** 2–3 vagueness flags.
- **FAIL** — ≤ 2 dimensions supported **or** ≥ 4 vagueness flags.

**Design note:** the judge deliberately uses the **same model class** as the pipeline's
synthesis executor — *not* Opus. The point of the demo is that even a same-class model,
given a structured rubric, can catch its own pipeline's failures.

**Fail-safe:** the judge must never crash the harness. If the API call fails or the JSON
can't be parsed after 2 attempts, it returns a **WARN** with all `dimension_support` set to
`false` and `reasoning = "Judge call failed: …"`. **A WARN whose reasoning starts with
"Judge call failed" is an infrastructure error, not a real quality warning** — check your
`ANTHROPIC_API_KEY` and network.

**Blind spot:** it has no answer key, so it can't tell you whether a *supported* score is
numerically *right* — only whether it's *justified*. That's the reference scorer's job.

---

### Evaluator 3 — Reference Scorer  (`agents/eval_reference_scorer.py`)

**Kind:** numeric comparison against the gold reference. **No LLM.** Runs under 20 ms.
**Question it answers:** *How far is the pipeline from a human expert's answer key?*

Two measurements:

1. **`score_delta_avg`** — the mean absolute difference between the pipeline's dimension
   scores and the gold scores, on the 0–100 scale. Lower is better.
   `score_deltas` holds the per-dimension breakdown so you can see *which* dimension drifted.
2. **`text_similarity`** — a lightweight **ROUGE-1 recall** (no external library): tokenize
   both suggestion sets, drop stopwords, and compute
   `|pipeline_tokens ∩ gold_tokens| / |gold_tokens|` — i.e. how much of the gold's
   suggestion vocabulary the pipeline actually covered. Range `0.0–1.0`.

**Verdict logic:**
- **PASS** — `score_delta_avg ≤ 10` **and** `text_similarity ≥ 0.4`.
- **WARN** — `score_delta_avg ≤ 20` **and** `text_similarity ≥ 0.2`.
- **FAIL** — anything else.

**Reading it:** a low delta with low similarity means "right numbers, wrong advice"; a high
delta means the pipeline mis-scored the JD relative to an expert. The UI surfaces the average
delta, the single worst dimension, and the similarity.

**Blind spot:** it's only as good as the gold reference, and ROUGE-1 rewards vocabulary
overlap, not semantic equivalence — a suggestion that's *correct but phrased differently*
from the gold can score low. Treat similarity as a rough signal, not a grade.

---

### Evaluator 4 — Human Eval  (in `pages/eval_observatory.py`)

**Kind:** you. **Question it answers:** *Do I, a human expert, agree?*

The human panel is always interactive, independent of which scenario is loaded, and does
**not** re-trigger the automated evaluators. You pick a verdict (pass / warn / fail) and add
free-text notes; the label is stored per scenario and timestamped. Its real job is to
**calibrate the LLM judge** — see the next section.

---

## 5. The Agreement Panel & Cohen's Kappa

Below the four panels, the **agreement summary** ties everything together.

### Disagreement count

```
Code ✅   LLM Judge ❌   Reference ❌
Disagreement count: 1 of 3 automated evaluators diverge from the majority verdict.
```

Computed over the **three automated** evaluators. The majority verdict is the mode; the
disagreement count is how many diverge from it.

- **0 disagreements** → strong signal. All three agree — whether on pass, warn, *or* fail.
  (Three-way *fail* agreement is just as informative as three-way *pass*.)
- **1 disagreement** → the odd one out names the failure mode. If the code checker is the
  lone ✅ while the other two ❌, you have a *well-formed but untrustworthy* report — the
  headline demo case.
- **2+ disagreements** → genuinely ambiguous; lean on the human verdict.

### Cohen's Kappa — "is the judge calibrated?"

Cohen's Kappa measures how well the **LLM judge** agrees with the **human** reviewer,
*correcting for agreement that would happen by chance*. It's computed across every scenario
you've labeled **this session** (as `(llm_verdict, human_verdict)` pairs), over the three
categories pass/warn/fail:

```
κ = (pₒ − pₑ) / (1 − pₑ)
      pₒ = observed agreement (fraction of pairs where judge == human)
      pₑ = agreement expected by chance
```

It needs **at least 2 labeled pairs** to display. Qualitative bands shown in the UI:

| κ | Label | Colour | Reading |
|---|-------|:------:|---------|
| ≥ 0.80 | **Excellent** | green | Judge tracks human judgment almost perfectly |
| 0.60–0.79 | **Good** | green | Judge is trustworthy on this task |
| 0.40–0.59 | **Moderate** | orange | Usable but watch the disagreements |
| 0.01–0.39 | **Poor — needs recalibration** | red | Judge is weakly aligned; retune its thresholds |
| ≤ 0.00 | **None — no better than chance** | red | Judge doesn't track the human at all (negative = systematically opposite) |

Every displayed κ is now accompanied by a **"Why:" line** explaining the verdict in plain
English — how many samples judge and human agreed on, and what that means for trusting the
judge (e.g. *"Agreement is substantial and clearly above chance — spot-check the
disagreements."*).

> **The "not measurable yet" case.** If **all** your labeled samples share the *same* verdict
> (e.g. you only labeled two "pass" scenarios), chance agreement is already 100%, κ is
> mathematically **undefined**, and the raw formula floors to `0.00`. The UI now shows
> **"not measurable yet"** with a *no-variety* explanation — **not** "Poor / needs
> recalibration", because a perfect-but-single-category run is not a miscalibrated judge.
> To get a meaningful κ, label scenarios with **differing** verdicts — the demo trio is built
> for exactly this: **Weak → fail, Average → warn, Strong → pass** (labeling all three in
> agreement yields **κ = 1.00, Excellent**).

**Why it matters:** the LLM judge is what would run *unattended at scale*. Kappa is the
evidence that it earns that trust. A **Good/Excellent** kappa across the three scenarios is
the "this is what alignment looks like before you trust it at scale" moment. To move kappa,
you tune the **verdict thresholds in the judge's prompt** — never the JSON structure.

> **Kappa is session-scoped and in-memory.** It resets on page reload; there is no
> cross-session history by design.

---

## 6. The Three Demo Scenarios

Three pre-cached scenarios live in `data/eval_scenarios/`. Each is a realistic India-market
tech role, chosen so the evaluators land on a specific, teachable pattern.

| Scenario | Role / Industry | `jdqi_score` | The point it makes |
|----------|-----------------|:------------:|--------------------|
| **Weak** (`scenario_weak.json`) | Mid-level full-stack engineer · IT/SaaS | **39** | Valid JSON ≠ trustworthy output |
| **Average** (`scenario_average.json`) | Embedded systems engineer · High-tech mfg | **59** | Agreement on *warn* is real signal |
| **Strong** (`scenario_strong.json`) | Data engineering lead · GCC (India) | **90** | What "trustworthy" looks like across all four |

### Expected evaluator outputs

| Evaluator | Weak | Average | Strong |
|-----------|:----:|:-------:|:------:|
| **1. Code check** | ✅ pass (7/7) | ✅ pass (7/7) | ✅ pass (7/7) |
| **2. LLM judge** | ❌ fail | ⚠️ warn | ✅ pass |
| **3. Reference score** | ❌ fail | ⚠️ warn | ✅ pass |
| **4. Human eval** | ❌ fail | ⚠️ warn | ✅ pass |
| **Disagreement count** | 1 (code is the lone pass) | 0 (all warn) | 0 (all pass) |

### Why each scenario lands where it does

**Weak — the headline case.**
The cached report is *internally consistent* (scores in range, weighted total ≈ `jdqi_score`
of 39, suggestions well-formed) so the **code checker passes 7/7**. But:
- Its suggestions are generic (*"Add more detail about the required skills."*) → the **LLM
  judge** raises vagueness flags and finds unsupported narratives → **fail**.
- Its dimension scores sit well above the gold (e.g. completeness 45 vs gold 20,
  skill_specificity 40 vs gold 10) → `score_delta_avg` clears the 20-point line and
  similarity is low → **reference scorer fails**.

  > The lone ✅ from the code checker against two ❌ is the demo's money shot: *the report is
  > well-formed and completely untrustworthy.*

**Average — agreement on a middle verdict.**
Vague compensation ("competitive"), missing tool versions, and an unclear reporting line put
the report squarely in the middle. Judge and reference scorer both land on **warn**; mark the
human **warn** too and the disagreement count is **0**. Lesson: three evaluators agreeing on
*warn* is as much signal as three agreeing on *pass* — it says "usable, but fix these first."

**Strong — trustworthy across the board.**
Explicit INR salary band, every tool with a version, clear seniority alignment, an EEO
statement, low reading grade. Scores track the gold closely and narratives cite the JD, so
**all four** land on **pass** with **0 disagreements**. This is the calibration anchor for a
high kappa.

---

## 7. Cached vs Live Mode

The scenario selector has a toggle:

- **Use cached (default, instant).** Evaluators run against the `cached_advisor_report`
  baked into the scenario JSON. Deterministic (except the LLM judge, which still calls
  Sonnet), fast, and reliable for a live demo. This is `run_eval()` in
  `agents/eval_orchestrator.py`.
- **Run live pipeline (~15 s).** Re-runs the real Analyse JD pipeline
  (parser → specialists → synthesizer) on the scenario's JD to produce a **fresh** advisor
  report, then evaluates *that* against the same gold reference. This is `run_eval_live()`.
  **Guardrails are intentionally skipped** on eval runs — they gate the live UI tabs, not the
  harness.

**Why run live?** It turns the harness into a **regression detector**: if a pipeline change
makes the fresh report drift from the gold, the reference scorer's verdict degrades from pass
→ warn → fail, and you'll see it immediately. Cached mode shows the *designed* story; live
mode shows the *current* truth.

---

## 8. Running the Evals

### In the app (interactive)

```bash
cd /Users/vrln/jdqi-eval
source .venv/bin/activate
streamlit run app.py --server.port 8501
# → open http://localhost:8501 → 🔬 Eval Observatory tab
```

Then: pick a scenario → choose cached/live → **Run Eval** → read the four panels and the
agreement summary → optionally submit a human label to update the kappa.

### As unit tests (headless, no app)

```bash
source .venv/bin/activate

# All eval evaluators
python -m pytest tests/test_eval_code_checker.py tests/test_eval_reference_scorer.py \
                 tests/test_eval_llm_judge.py tests/test_eval_orchestrator.py -v

# Everything (docx + guardrail highlighter + evals)
python -m pytest tests/ -v      # 43 passed
```

The LLM-judge tests **mock** the Anthropic client — no live API calls, no key needed for the
test suite. Live evaluation in the app *does* require a valid `ANTHROPIC_API_KEY`.

---

## 9. Adding Your Own Scenario

Use the dev CLI to run the real pipeline on any JD and freeze its output as a new scenario:

```bash
source .venv/bin/activate
python scripts/cache_scenario.py \
  --jd path/to/your_jd.txt \
  --industry "IT/SaaS" \
  --id my_scenario \
  --label "My scenario — one-line description"
# writes data/eval_scenarios/scenario_my_scenario.json
```

Flags: `--jd` (path to a `.txt` JD, required), `--industry` (required), `--id` (required),
`--label` (optional; defaults to the id), `--out-dir` (optional).

Then **hand-write the `gold_reference`** in the generated JSON — the CLI can produce the
pipeline's report, but the *answer key* must be human-verified:

- `dimension_scores` — what an expert would score each dimension, 0–100.
- `suggested_additions` — the *ideal* fixes, as plain strings, phrased with the same key
  vocabulary you'd expect a good report to use (this drives the ROUGE-1 similarity).
- `notes` — why this is the gold answer.

The scenario appears in the dropdown automatically on the next app reload.

> **Tuning tip:** if the LLM judge passes a JD you consider weak (or fails one you consider
> strong), adjust only the **numeric verdict thresholds in the judge's prompt**
> (`agents/eval_llm_judge.py`) — do not change the JSON structure it returns.

---

## 10. Interpretation Cheat-Sheet

**Read the panels in this order:** Code → Reference → LLM Judge → your Human verdict → the
agreement summary.

| What you see | What it means | Action |
|--------------|---------------|--------|
| All 4 ✅, 0 disagreements | Trustworthy report | Trust / ship it |
| All 4 ⚠️, 0 disagreements | Usable but flagged | Fix the flagged items, then trust |
| All 4 ❌, 0 disagreements | Confidently untrustworthy | Reject; the JD or pipeline needs work |
| Code ✅ but Reference/Judge ❌ | **Well-formed but wrong** | Don't trust — form ≠ truth (the weak case) |
| Judge ❌ but Reference ✅ | Scores are accurate but *unsupported* / suggestions vague | Improve narratives & suggestions |
| Judge ✅ but Reference ❌ | Scores are *justified* but numerically off vs expert | Recalibrate the pipeline's scoring |
| Judge WARN, reasoning = "Judge call failed…" | Infra error, **not** a quality warning | Check `ANTHROPIC_API_KEY` / network |
| Kappa **Good/Excellent** (≥0.6) | Judge agrees with humans | Safe to lean on the judge at scale |
| Kappa **Poor / None** (≤0.4) | Judge is miscalibrated | Retune judge prompt thresholds before trusting |
| Kappa **"not measurable yet"** | All labels share one verdict — no variety | Label scenarios with differing verdicts (weak/avg/strong) |
| Live mode verdict worse than cached | Pipeline regression since the scenario was frozen | Investigate the recent pipeline change |

**One-line summary:** the code checker proves the report is *valid*, the reference scorer
proves it's *accurate*, the LLM judge proves it's *justified*, and the human + kappa prove
the judge itself is *trustworthy*. You need all four axes because each one is blind to what
the others catch.
