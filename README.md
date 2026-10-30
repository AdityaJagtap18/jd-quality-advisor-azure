# JD Quality Intelligence (JDQI)

A Streamlit demo of the **Claude Advisor Strategy** applied to hiring — two tools in one app:

| Tab | What it does |
|-----|-------------|
| 🔍 **Analyse JD** | Paste an existing JD, get a scored quality report across 6 dimensions |
| 📝 **Build JD** | Chat with an AI consultant to build a JDQI-compliant JD, download as branded `.docx` |
| 🔬 **Eval Observatory** | Put the pipeline's own advisor reports under a 4-evaluator harness — see whether you can *trust* the pipeline's judgment |

Both analysis pipelines are gated by **Azure AI Content Safety** ("Azure guardrails") on input and output.

> **Migrated from AWS Bedrock Guardrails → Azure AI Content Safety.** The LLM
> calls still use the **Anthropic API**; only the guardrail gate changed. Full
> setup (resource, blocklist, data template, upload) is in
> **[docs/azure-content-safety-setup.md](docs/azure-content-safety-setup.md)**.
> See [What changed in the migration](#what-changed-aws--azure) below.

> **New to the evaluation harness?** Read **[EVALS.md](EVALS.md)** — a full guide to what each evaluator measures, how to read the verdicts and the agreement panel, and the three demo scenarios.

---

## Why This Exists

AI amplifies whatever signal the JD carries. A vague or biased JD fed into an AI hiring pipeline produces vague, biased shortlists — at scale. This tool acts as a quality gate before the JD becomes the source of truth.

---

## Architecture

### Analyse JD — 9-Agent Pipeline (Advisor Strategy)

```
JD Text + Industry
    │
    ▼  🛡️ Azure Guardrail (INPUT)
    │
    ▼
Agent 1: JD Parser (Sonnet)           → parsed_jd
    │
    ▼
Agents 2–7: Specialist Analyzers      → dimension_results  (parallel, ThreadPoolExecutor)
    Completeness · Skill Specificity · Cognitive Load
    Inclusion Signals · Compensation · Role Coherence
    │
    ▼
Agent 8: Synthesis Executor (Sonnet)  → calls consult_advisor tool
    │  tool call
    ▼
Agent 9: Advisor (Opus, on-demand)    → advisor_report
    │
    ▼  🛡️ Azure Guardrail (OUTPUT)
    ├── passed → JDQI Report + Suggested Additions
    └── blocked → Haiku scans JD → highlights problematic phrases
```

### Build JD — Two-Phase Chat Pipeline

```
User Chat Message
    │
    ▼  🛡️ Azure Guardrail (INPUT)
    │
    ▼
Phase 1: Gather Agent (Sonnet)
    Adaptive conversation — covers all 6 JDQI dimensions
    Warns user on JDQI score impact before skipping any dimension
    Calls signal_ready tool when brief is complete
    │
    ▼  User confirms → "Generate JD now"
    │
    ▼
Phase 2: Draft Agent (Sonnet)          ┐
    Receives clean JDQIBrief            │  up to 3 attempts
    Writes full JD prose                │
    │                                   │
    ▼  🛡️ Azure Guardrail (OUTPUT)   │
    │                                   │
    ├── passed ─────────────────────────┘
    │
    └── blocked → extract block reasons (topics, words, PII)
                  feed back to Draft Agent as explicit corrections
                  redraft avoiding all flagged content
                  re-check guardrail  (loop, max 3 attempts)
    │
    ▼
Branded .docx download
    (configurable color, optional logo, header/footer)
```

### Eval Observatory — 4-Evaluator Harness

```
Scenario (cached JD + advisor report + gold reference)
    │        └── or: "Run live pipeline" re-runs Analyse JD to produce a fresh report
    ▼
run_eval()  →  3 automated evaluators in parallel (ThreadPoolExecutor)
    │
    ├── 1. Code Checker      (rules, no LLM, <50ms)  → pass / warn / fail
    ├── 2. LLM-as-Judge      (Sonnet, ~3s)           → pass / warn / fail
    └── 3. Reference Scorer  (numeric vs gold, <20ms)→ pass / warn / fail
    │
    ▼
    4. Human Eval  (reviewer clicks a verdict in the UI)
    │
    ▼
Agreement panel  →  disagreement count  +  Cohen's Kappa (LLM judge vs human)
```

The thesis: the other two tabs tell you whether your **JD** is good. This tab tells you
whether you can trust the **pipeline's own judgment** — and makes each evaluator type's
blind spots visible. Full walkthrough in **[EVALS.md](EVALS.md)**.

---

## JDQI Dimensions

| Dimension | What's checked | Weight |
|-----------|---------------|--------|
| Completeness | required/preferred skills, success criteria, reporting structure, growth path | 25% |
| Skill Specificity | tools/versions/proficiency levels explicit vs vague | 20% |
| Role Coherence | seniority vs experience/responsibility alignment | 20% |
| Cognitive Load | word count, Flesch-Kincaid grade, jargon density | 15% |
| Inclusion Signals | exclusionary language flags | 10% |
| Compensation | salary band present/absent | 10% |

---

## File Map

| File | Purpose |
|------|---------|
| `app.py` | Streamlit UI — two tabs, session state, pipeline orchestration |
| `agents/parser.py` | Agent 1 — extract structured fields from raw JD text |
| `agents/specialists.py` | Agents 2–7 — 6 dimension analyzers, ThreadPoolExecutor |
| `agents/synthesizer.py` | Agent 8 — Sonnet tool loop, calls `consult_advisor` |
| `agents/advisor.py` | Agent 9 — Opus benchmark comparison + JDQI scoring |
| `agents/jd_builder_gather.py` | Phase 1 gather agent — adaptive chat, `signal_ready` tool |
| `agents/jd_builder_draft.py` | Phase 2 draft agent — JDQIBrief → JDDocument prose |
| `agents/jd_docx.py` | python-docx builder — branded `.docx` with color + logo |
| `agents/guardrails.py` | Azure AI Content Safety `analyze_text` — input + output gate; `scan_jd_for_violations()` Haiku phrase scan |
| `scripts/upload_blocklist.py` | Create/update the Azure blocklist and upload terms from the CSV template |
| `data/blocklist_terms.csv` | Data template — discriminatory/exclusionary hiring terms to block |
| `docs/azure-content-safety-setup.md` | Full Azure Content Safety setup: resource, blocklist, upload, troubleshooting |
| `models/context.py` | TypedDict schemas — JDQIContext, JDQIBrief, JDDocument, etc. |
| `agents/eval_code_checker.py` | Evaluator 1 — deterministic rule/schema checks (no LLM) |
| `agents/eval_llm_judge.py` | Evaluator 2 — Sonnet LLM-as-judge against a rubric |
| `agents/eval_reference_scorer.py` | Evaluator 3 — numeric score deltas + ROUGE-1 vs gold (no LLM) |
| `agents/eval_orchestrator.py` | Runs the 3 automated evaluators in parallel → `EvalResult` (cached + live paths) |
| `models/eval_context.py` | TypedDict schemas for the eval domain (EvalScenario, EvalResult, …) |
| `pages/eval_observatory.py` | Streamlit tab 3 — 4-panel harness, agreement + Cohen's Kappa |
| `data/eval_scenarios/*.json` | Three pre-cached scenarios (weak / average / strong) |
| `scripts/cache_scenario.py` | Dev CLI — run the live pipeline on a JD, save it as a scenario JSON |
| `tests/test_jd_docx.py` | Unit tests for the docx builder |
| `tests/test_guardrail_highlighter.py` | Unit tests for guardrail scan + HTML highlighter |
| `tests/test_eval_*.py` | Unit tests for the code checker, reference scorer, LLM judge, orchestrator |

---

## Quick Start

The project ships with a Python **3.11** virtual environment at `.venv/` in the repo root.

### First-time setup (no `.venv` yet)

```bash
# 1. Enter the project
cd /Users/vrln/jdqi-eval-azure-gr

# 2. Create the venv with Python 3.11 and install deps
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Set environment variables
cp .env.example .env   # then fill in values (see below)

# 4. One-time: create the Azure Content Safety resource + upload the blocklist
#    (full walkthrough: docs/azure-content-safety-setup.md)
python scripts/upload_blocklist.py     # creates 'jdqi-blocklist' from data/blocklist_terms.csv
```

### Every session (venv already exists)

```bash
cd /Users/vrln/jdqi-eval-azure-gr
source .venv/bin/activate          # activates Python 3.11 + Streamlit 1.58
streamlit run app.py --server.port 8501
# → http://localhost:8501  (three tabs: Analyse JD · Build JD · Eval Observatory)
```

> **Headless / server launch:** add `--server.headless true` to skip the browser auto-open
> (useful when launching in the background). Health check: `curl localhost:8501/_stcore/health` → `ok`.

You can also invoke the venv binaries directly without activating, e.g.
`.venv/bin/streamlit run app.py` or `.venv/bin/python -m pytest tests/ -v`.

### `.env` variables

```
ANTHROPIC_API_KEY=sk-ant-...

# Azure AI Content Safety ("Azure guardrails")
AZURE_CONTENT_SAFETY_ENDPOINT=https://<your-resource>.cognitiveservices.azure.com/
AZURE_CONTENT_SAFETY_KEY=<content-safety-key>

# Optional overrides (defaults shown):
AZURE_CONTENT_SAFETY_BLOCKLIST=jdqi-blocklist          # blank = skip blocklist
AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD=4              # 0–7; block at/above
```

> **Guardrail behaviour (fails closed):** If Azure credentials are missing/invalid,
> the SDK isn't installed, or Azure returns an error, both INPUT and OUTPUT checks
> block the pipeline and show a red error. Key-based auth is default; omit the key
> (with `azure-identity` installed) to use Entra ID via `DefaultAzureCredential`.
>
> **First-time setup is a one-time step** — create the resource and upload the
> blocklist per **[docs/azure-content-safety-setup.md](docs/azure-content-safety-setup.md)**.

---

## Azure Content Safety Config

| Setting | Value / env var |
|---------|-----------------|
| Service | Azure AI Content Safety (`analyze_text`) |
| Endpoint | `AZURE_CONTENT_SAFETY_ENDPOINT` (region is in the URL) |
| Key | `AZURE_CONTENT_SAFETY_KEY` (or Entra ID) |
| Harm categories | Hate · SelfHarm · Sexual · Violence (severity 0–7, built in) |
| Severity threshold | `AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD` (default `4`) |
| Custom blocklist | `AZURE_CONTENT_SAFETY_BLOCKLIST` (default `jdqi-blocklist`) |

**Block rule:** any harm-category severity `≥ threshold` **or** any blocklist term match.

| Path | Behaviour on block |
|------|--------------------|
| INPUT (both tabs) | Pipeline halts, red error, no LLM call |
| OUTPUT (Build JD) | Course-correction loop — redraft up to 3× with block reasons as feedback |
| OUTPUT (Analyse JD) | Red error + Haiku scans the JD and highlights the offending phrases in amber |

---

## What changed (AWS → Azure)

Only the **guardrail gate** was migrated; the Anthropic LLM pipeline is untouched.

| Area | Before (AWS Bedrock) | After (Azure AI Content Safety) |
|------|----------------------|---------------------------------|
| Service call | `bedrock-runtime.apply_guardrail(source=INPUT/OUTPUT)` | `ContentSafetyClient.analyze_text(...)` |
| SDK / dep | `boto3` | `azure-ai-contentsafety` (+ optional `azure-identity`) |
| Auth | `AWS_ACCESS_KEY_ID` / `_SECRET_ACCESS_KEY` / `AWS_PROFILE` / `AWS_REGION` | `AZURE_CONTENT_SAFETY_ENDPOINT` + `_KEY` (or Entra ID) |
| "Denied topics" (discriminatory hiring) | Bedrock topic policy `Discriminatory_Hiring_Criteria` | Custom **blocklist** (`data/blocklist_terms.csv` → `scripts/upload_blocklist.py`) |
| Content filters | Bedrock managed filters | Built-in harm categories + severity threshold |
| Code touched | — | `agents/guardrails.py` (rewritten), `app.py` + `agents/jd_docx.py` (label/comment only) |

The public interface (`check_guardrail`, `GuardrailResult`, `extract_report_text`,
`scan_jd_for_violations`) is **unchanged**, so the rest of the app was label-only edits.
New files: `docs/azure-content-safety-setup.md`, `data/blocklist_terms.csv`,
`scripts/upload_blocklist.py`.

---

## Tech Stack

| Layer | Tech |
|-------|------|
| UI | Streamlit 1.43+ |
| LLM | Anthropic SDK 0.49+ (Sonnet 4.6 + Opus 4.6) |
| Guardrails | Azure AI Content Safety `analyze_text` via `azure-ai-contentsafety` 1.0+ |
| Docx generation | python-docx 1.1+ |
| Parallelism | ThreadPoolExecutor (6 specialist agents · 3 eval agents) |
| Evaluation | 4-evaluator harness — code rules, Sonnet judge, gold-reference scorer, human-in-the-loop + Cohen's Kappa |
| Config | python-dotenv |
| Runtime | Python 3.11 (`.venv/`) · Streamlit 1.58 |

---

## Running Tests

```bash
source .venv/bin/activate
python -m pytest tests/ -v
# 43 passed  (docx + guardrail highlighter + all four eval evaluators)
```

Run only the evaluation suites:

```bash
python -m pytest tests/test_eval_code_checker.py tests/test_eval_reference_scorer.py \
                 tests/test_eval_llm_judge.py tests/test_eval_orchestrator.py -v
```

---

## Evaluation

The **🔬 Eval Observatory** tab and the four evaluators behind it are documented in full in
**[EVALS.md](EVALS.md)** — what each evaluator measures, the 0–100 scoring scale, verdict
thresholds, how to read the agreement panel and Cohen's Kappa, the three demo scenarios with
their expected outputs, and how to add your own scenario with `scripts/cache_scenario.py`.
