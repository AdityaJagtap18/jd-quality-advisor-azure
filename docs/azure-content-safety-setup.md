# Azure AI Content Safety — Setup Guide ("Azure Guardrails")

This guide replaces the previous **AWS Bedrock Guardrails** gate with **Azure AI
Content Safety**. It covers, end to end:

1. [What maps to what](#1-what-maps-to-what-bedrock--azure)
2. [Create the Content Safety resource](#2-create-the-content-safety-resource)
3. [Get the endpoint + key](#3-get-the-endpoint--key)
4. [Configure `.env`](#4-configure-env)
5. [Create the "guardrail": blocklist + severity policy](#5-create-the-guardrail-blocklist--severity-policy)
6. [Data template + upload steps](#6-data-template--upload-steps)
7. [How the code uses it](#7-how-the-code-uses-it)
8. [Test / verify](#8-test--verify)
9. [Troubleshooting](#9-troubleshooting)

> **LLM calls are unchanged** — the pipeline still uses the Anthropic API
> (`ANTHROPIC_API_KEY`). Only the guardrail gate moved from AWS to Azure.

---

## 1. What maps to what (Bedrock → Azure)

Azure AI Content Safety has no single "Guardrail" object like Bedrock. A Bedrock
guardrail bundles several policies; on Azure those policies are separate
features that `analyze_text` evaluates together in one call:

| Bedrock Guardrail policy | Azure AI Content Safety equivalent |
|--------------------------|------------------------------------|
| Content filters (hate / violence / sexual / misconduct) | **Harm categories** — `Hate`, `Violence`, `Sexual`, `SelfHarm`, each scored severity `0–7`. Built in, nothing to configure. |
| Denied topics (e.g. `Discriminatory_Hiring_Criteria`) | **Custom blocklist** of exact terms/phrases (this repo ships one) — or **custom categories** (preview) if you want a trained classifier instead. |
| Word / phrase filters | **Blocklist** items. |
| Managed word lists (profanity) | Covered by the **Hate/Sexual** categories + your blocklist. |
| Sensitive info / PII | Not part of Content Safety — use **Azure AI Language – PII detection** if you need it. (The JDQI demo does not gate on PII.) |
| `apply_guardrail(source=INPUT/OUTPUT)` | `analyze_text(...)` — the same call is used for both the input and output gate. `source` is kept in the code only for UI labelling. |

**Block decision** (implemented in `agents/guardrails.py`): a text is blocked if
**any** harm category severity `≥ AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD`
(default `4`) **or** **any** blocklist term matches.

---

## 2. Create the Content Safety resource

### Option A — Azure Portal (click-through)

1. Sign in to <https://portal.azure.com>.
2. **Create a resource** → search **"Content Safety"** → **Create**.
3. Fill in:
   - **Subscription** / **Resource group** (create one, e.g. `jdqi-rg`).
   - **Region** — pick one near you (e.g. `Central India`, `East US`). The
     region is baked into the endpoint URL.
   - **Name** — e.g. `jdqi-contentsafety`.
   - **Pricing tier** — `Free F0` is enough for the demo; `Standard S0` for load.
4. **Review + create** → **Create**. Wait for deployment to finish.

### Option B — Azure CLI

```bash
az login
az group create --name jdqi-rg --location centralindia

az cognitiveservices account create \
  --name jdqi-contentsafety \
  --resource-group jdqi-rg \
  --kind ContentSafety \
  --sku F0 \
  --location centralindia \
  --yes
```

---

## 3. Get the endpoint + key

### Portal
Open the resource → **Resource Management → Keys and Endpoint**. Copy
**Endpoint** and **KEY 1**.

### CLI
```bash
az cognitiveservices account show \
  --name jdqi-contentsafety --resource-group jdqi-rg \
  --query properties.endpoint -o tsv

az cognitiveservices account keys list \
  --name jdqi-contentsafety --resource-group jdqi-rg \
  --query key1 -o tsv
```

The endpoint looks like `https://jdqi-contentsafety.cognitiveservices.azure.com/`.

---

## 4. Configure `.env`

```dotenv
ANTHROPIC_API_KEY=sk-ant-...

AZURE_CONTENT_SAFETY_ENDPOINT=https://jdqi-contentsafety.cognitiveservices.azure.com/
AZURE_CONTENT_SAFETY_KEY=<KEY 1>

# Optional overrides (defaults shown):
AZURE_CONTENT_SAFETY_BLOCKLIST=jdqi-blocklist          # blank = skip blocklist
AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD=4              # 0–7; block at/above
```

> **Auth model:** key-based by default (mirrors the Anthropic key). If you omit
> `AZURE_CONTENT_SAFETY_KEY` but keep the endpoint and have `azure-identity`
> installed, the code falls back to **Entra ID** via `DefaultAzureCredential`
> (`az login`, managed identity, etc.). Give that identity the
> **Cognitive Services User** role on the resource.

---

## 5. Create the "guardrail": blocklist + severity policy

The **severity policy** needs no setup — it is enforced in code by
`AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD` at analysis time.

The **blocklist** is the custom part. It is a named list of exact terms that
`analyze_text` checks against. This is where the JDQI-specific discriminatory /
exclusionary hiring language lives (the Bedrock `Discriminatory_Hiring_Criteria`
denied-topic equivalent).

You create and populate the blocklist with the shipped script (recommended) or
by hand (portal). Both are below.

---

## 6. Data template + upload steps

### The template

`data/blocklist_terms.csv` — a two-column template you edit and upload. Header
row is required:

```csv
term,description
young and energetic,Age-coded exclusion (favours younger candidates)
candidates aged 25-35,Explicit age range requirement
male candidates preferred,Gender preference
Christian only,Religious preference
local candidates only,Nationality/origin proxy exclusion
...
```

- **term** — the exact word/phrase to block (matching is case-insensitive).
- **description** — free-text note (stored on the item; helps auditing).

Add/remove rows for your own policy. Keep terms specific — a single word like
`experienced` would over-block; a phrase like `young and energetic` is precise.

### Upload (recommended — one command)

```bash
source .venv/bin/activate
python scripts/upload_blocklist.py                     # uses data/blocklist_terms.csv + env blocklist name
```

The script (`scripts/upload_blocklist.py`):
1. reads `AZURE_CONTENT_SAFETY_ENDPOINT` / `_KEY` from `.env`,
2. **creates or updates** the blocklist named by `--blocklist`
   (default = `AZURE_CONTENT_SAFETY_BLOCKLIST` or `jdqi-blocklist`),
3. **uploads every row** as a blocklist item, printing each one.

Custom file / list name:

```bash
python scripts/upload_blocklist.py \
  --file data/blocklist_terms.csv \
  --blocklist jdqi-blocklist
```

> **Propagation delay:** after any blocklist edit, Azure can take up to
> **~5 minutes** before `analyze_text` reflects the change.

### Upload (manual — Azure portal, no script)

1. Open your Content Safety resource in the portal.
2. Go to **Content Safety Studio** (or **content-safety.cognitive.azure.com**)
   → **Moderate text content** → **Use blocklist** → **Manage / Create blocklist**.
3. Create a blocklist named exactly `jdqi-blocklist` (match your env var).
4. Add each term from `data/blocklist_terms.csv` as a blocklist item.
5. Save. Wait ~5 minutes.

---

## 7. How the code uses it

`agents/guardrails.py`:

- `check_guardrail(text, source)` builds a `ContentSafetyClient` and calls
  `analyze_text(AnalyzeTextOptions(text=..., categories=[Hate, SelfHarm, Sexual,
  Violence], blocklist_names=[<your list>], halt_on_blocklist_hit=False))`.
- It blocks when any category severity `≥ threshold` **or** any blocklist term
  matches, returning `GuardrailResult(passed=False, blocked_reason=...)`.
- The `blocked_reason` string names the tripped categories and the **verbatim
  matched blocklist terms** — which the Analyse-JD tab feeds to
  `scan_jd_for_violations()` (Anthropic Haiku) to highlight the offending
  phrases in the JD.
- The gate **fails closed**: missing config, an un-installed SDK, or any Azure
  error returns `passed=False` so the pipeline never proceeds unchecked.

The public interface (`check_guardrail`, `GuardrailResult`,
`extract_report_text`, `scan_jd_for_violations`) is **unchanged** from the
Bedrock version, so `app.py` needed only label changes.

---

## 8. Test / verify

**Config + block logic (no Azure round-trip needed for the config path):**

```bash
source .venv/bin/activate
python -m pytest tests/ -q          # 43 passed
```

**Live round-trip** — confirm your resource + blocklist actually block:

```bash
source .venv/bin/activate
python - <<'PY'
from dotenv import load_dotenv; load_dotenv(override=True)
from agents.guardrails import check_guardrail
print(check_guardrail("We want young and energetic candidates aged 25-35.", "INPUT"))
print(check_guardrail("A collaborative senior backend engineer.", "INPUT"))
PY
```

Expected: the first line is blocked (blocklist term match), the second passes.
If the first passes, your blocklist upload hasn't propagated yet (wait ~5 min)
or the blocklist name doesn't match `AZURE_CONTENT_SAFETY_BLOCKLIST`.

---

## 9. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `Azure Content Safety not configured` error in the UI | `AZURE_CONTENT_SAFETY_ENDPOINT` / `_KEY` missing in `.env`. |
| `azure-ai-contentsafety is not installed` | `pip install -r requirements.txt` into the active venv. |
| Blocklist terms not blocking | (a) Wait ~5 min after upload. (b) `AZURE_CONTENT_SAFETY_BLOCKLIST` must equal the uploaded list name. (c) Re-run `scripts/upload_blocklist.py`. |
| `401` / `Access denied` | Wrong key, or (Entra ID) the identity lacks **Cognitive Services User** on the resource. |
| `404` `ResourceNotFound` on analyze | Endpoint URL wrong/region mismatch — copy it again from **Keys and Endpoint**. |
| Harmless JDs get blocked | Lower sensitivity: raise `AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD` (e.g. `6`), or trim over-broad blocklist terms. |
| Nothing ever blocks on harm categories | JDs rarely contain hate/violence — that's expected. The blocklist is the primary JDQI gate; categories are the safety net. |
