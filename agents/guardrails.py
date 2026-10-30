"""
Azure AI Content Safety integration ("Azure guardrails").

Validates text against an Azure AI Content Safety resource via the
`analyze_text` API. Used as an input gate (JD text / chat message) and an
output gate (advisor report narrative / drafted JD) in the JDQI pipeline.

Two layers of protection are applied on every check:
  1. Harm categories  — Hate, SelfHarm, Sexual, Violence (severity 0–7).
                         Any category whose severity ≥ the configured
                         threshold blocks the pipeline.
  2. Custom blocklist  — a managed list of exact terms/phrases (e.g.
                         discriminatory hiring language). Any match blocks.
                         See docs/azure-content-safety-setup.md for how to
                         create the blocklist and upload the term template.

Credential resolution order (first match wins):
  1. AZURE_CONTENT_SAFETY_KEY + AZURE_CONTENT_SAFETY_ENDPOINT
                            — key-based auth (preferred, mirrors the
                              Anthropic key model).
  2. AZURE_CONTENT_SAFETY_ENDPOINT only + `azure-identity` installed
                            — Entra ID (Azure AD) auth via
                              DefaultAzureCredential.
  3. Neither set            — the guardrail is a hard gate, so the check
                              fails closed with a helpful message.

The Azure region is encoded in the endpoint URL, so there is no separate
region variable (unlike the previous AWS Bedrock implementation).

Config env vars:
  AZURE_CONTENT_SAFETY_ENDPOINT            e.g. https://<resource>.cognitiveservices.azure.com/
  AZURE_CONTENT_SAFETY_KEY                 resource access key
  AZURE_CONTENT_SAFETY_BLOCKLIST           blocklist name (default: "jdqi-blocklist"; blank = skip)
  AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD  0–7, block at/above this severity (default: 4)
"""

import json
import os
from dataclasses import dataclass
from typing import Optional

import anthropic

try:
    from azure.ai.contentsafety import ContentSafetyClient
    from azure.ai.contentsafety.models import AnalyzeTextOptions, TextCategory
    from azure.core.credentials import AzureKeyCredential
    from azure.core.exceptions import HttpResponseError

    _AZURE_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover - import guard for environments without the SDK
    _AZURE_SDK_AVAILABLE = False

_DEFAULT_BLOCKLIST = "jdqi-blocklist"
_DEFAULT_SEVERITY_THRESHOLD = 4

# Cache the client per (endpoint, key) so we don't rebuild it on every call.
_client_cache: dict = {}


@dataclass
class GuardrailResult:
    passed: bool
    action: str           # "NONE", "BLOCKED", or "AZURE_ERROR"
    blocked_reason: Optional[str] = None


def _azure_configured() -> bool:
    """Return True if an endpoint (and a way to auth) is configured."""
    endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT", "").strip()
    if not endpoint:
        return False
    if os.getenv("AZURE_CONTENT_SAFETY_KEY", "").strip():
        return True
    # Endpoint present but no key — allow Entra ID auth if azure-identity is available.
    try:
        import azure.identity  # noqa: F401

        return True
    except ImportError:
        return False


def _severity_threshold() -> int:
    """Severity (0–7) at or above which a harm category blocks the pipeline."""
    raw = os.getenv("AZURE_CONTENT_SAFETY_SEVERITY_THRESHOLD", "").strip()
    try:
        return int(raw) if raw else _DEFAULT_SEVERITY_THRESHOLD
    except ValueError:
        return _DEFAULT_SEVERITY_THRESHOLD


def _blocklist_names() -> list[str]:
    """Configured blocklist name(s). Empty list disables blocklist matching."""
    raw = os.getenv("AZURE_CONTENT_SAFETY_BLOCKLIST", _DEFAULT_BLOCKLIST).strip()
    return [raw] if raw else []


def _get_client():
    """Build (or reuse) a ContentSafetyClient.

    Uses AZURE_CONTENT_SAFETY_KEY when present; otherwise falls back to
    Entra ID (DefaultAzureCredential) against the same endpoint.
    """
    endpoint = os.getenv("AZURE_CONTENT_SAFETY_ENDPOINT", "").strip()
    key = os.getenv("AZURE_CONTENT_SAFETY_KEY", "").strip()

    cache_key = (endpoint, key)
    if cache_key in _client_cache:
        return _client_cache[cache_key]

    if key:
        credential = AzureKeyCredential(key)
    else:
        from azure.identity import DefaultAzureCredential

        credential = DefaultAzureCredential()

    client = ContentSafetyClient(endpoint, credential)
    _client_cache[cache_key] = client
    return client


def _extract_assessment_reason(response, threshold: int) -> str:
    """Build a human-readable reason string from an analyze_text response."""
    parts: list[str] = []

    # Harm categories (Hate / SelfHarm / Sexual / Violence) at/above threshold.
    for cat in getattr(response, "categories_analysis", None) or []:
        severity = getattr(cat, "severity", None)
        if severity is not None and severity >= threshold:
            name = getattr(cat, "category", "?")
            parts.append(f"Content category: {name} [severity={severity}]")

    # Custom blocklist matches — the matched term is verbatim from the input,
    # which the JD highlighter (scan_jd_for_violations) can surface directly.
    for match in getattr(response, "blocklists_match", None) or []:
        term = getattr(match, "blocklist_item_text", "?")
        list_name = getattr(match, "blocklist_name", "?")
        parts.append(f"Blocked term ({list_name}): {term}")

    return "; ".join(parts) if parts else "Azure Content Safety intervened (no detailed assessment available)"


def check_guardrail(text: str, source: str) -> GuardrailResult:
    """
    Apply Azure AI Content Safety to `text`.

    Args:
        text:   The text to evaluate.
        source: "INPUT" or "OUTPUT" — kept for logging/labelling parity with
                the previous Bedrock gate. Azure Content Safety analyses input
                and output identically, so this only affects UI messages.

    Returns:
        GuardrailResult with passed=True if no category exceeded the severity
        threshold and no blocklist term matched; passed=False (with
        blocked_reason) otherwise. AWS/Azure/config errors also return
        passed=False so the pipeline fails closed (hard gate).
    """
    if not text or not text.strip():
        return GuardrailResult(passed=True, action="NONE")

    if not _AZURE_SDK_AVAILABLE:
        return GuardrailResult(
            passed=False,
            action="AZURE_ERROR",
            blocked_reason="azure-ai-contentsafety is not installed — run `pip install azure-ai-contentsafety`",
        )

    if not _azure_configured():
        return GuardrailResult(
            passed=False,
            action="AZURE_ERROR",
            blocked_reason=(
                "Azure Content Safety not configured — set AZURE_CONTENT_SAFETY_ENDPOINT and "
                "AZURE_CONTENT_SAFETY_KEY in .env"
            ),
        )

    threshold = _severity_threshold()

    try:
        client = _get_client()
        options = AnalyzeTextOptions(
            text=text[:10_000],                 # Content Safety single-request text limit
            categories=[
                TextCategory.HATE,
                TextCategory.SELF_HARM,
                TextCategory.SEXUAL,
                TextCategory.VIOLENCE,
            ],
            blocklist_names=_blocklist_names(),
            halt_on_blocklist_hit=False,        # collect all matches for a full reason
        )
        response = client.analyze_text(options)
    except HttpResponseError as exc:
        # Surface Azure errors as passed=False so the pipeline warns the user.
        detail = getattr(getattr(exc, "error", None), "message", None) or str(exc)
        return GuardrailResult(passed=False, action="AZURE_ERROR", blocked_reason=detail)
    except Exception as exc:  # credential/config/transport errors — fail closed
        return GuardrailResult(passed=False, action="AZURE_ERROR", blocked_reason=str(exc))

    # Determine whether anything crossed the block criteria.
    category_blocked = any(
        getattr(c, "severity", 0) >= threshold
        for c in (getattr(response, "categories_analysis", None) or [])
    )
    blocklist_blocked = bool(getattr(response, "blocklists_match", None))

    if category_blocked or blocklist_blocked:
        reason = _extract_assessment_reason(response, threshold)
        return GuardrailResult(passed=False, action="BLOCKED", blocked_reason=reason)

    return GuardrailResult(passed=True, action="NONE")


def extract_report_text(report: dict) -> str:
    """Flatten an AdvisorReport dict to a single string for output guardrail check."""
    parts: list[str] = []

    if bc := report.get("benchmark_comparison"):
        parts.append(bc)

    for item in report.get("dimension_breakdown", []):
        if n := item.get("narrative"):
            parts.append(n)

    for item in report.get("suggested_additions", []):
        if s := item.get("suggestion"):
            parts.append(s)
        if imp := item.get("impact"):
            parts.append(imp)

    return "\n\n".join(parts)


def scan_jd_for_violations(
    jd_text: str,
    blocked_reason: str,
    client: anthropic.Anthropic,
) -> list[str]:
    """Call Claude Haiku to find verbatim phrases in jd_text that match the block reason.

    Returns a list of exact quoted strings from jd_text, or [] on any failure.
    (Uses the Anthropic API — unchanged by the AWS→Azure guardrail migration.)
    """
    if not jd_text or not jd_text.strip():
        return []

    prompt = (
        f"You are analysing a job description that triggered an AI content guardrail.\n\n"
        f"Guardrail block reason: {blocked_reason}\n\n"
        "Scan the job description below and identify EXACT verbatim phrases or sentences "
        "that likely caused this guardrail violation.\n\n"
        "Guidelines by block type:\n"
        "- Discriminatory hiring language: age requirements, gender/race/religion/nationality "
        "preferences, physical appearance requirements, overly restrictive experience filters "
        "that exclude protected groups (e.g. 'young and energetic', 'candidates aged 25–35')\n"
        "- Content category (Hate/Violence/Sexual/SelfHarm): offensive language, slurs, explicit content\n"
        "- Blocked term: look for that exact word or phrase\n"
        "- Personal data: personal names, phone numbers, personal email addresses in the JD body\n\n"
        f"Job Description:\n{jd_text[:10_000]}\n\n"
        "Return ONLY a JSON array of verbatim strings copied exactly from the text above. "
        "If nothing specific is identifiable, return: []"
    )

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text if response.content else ""
        start = raw.find("[")
        end = raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            return []
        parsed = json.loads(raw[start : end + 1])
        if not isinstance(parsed, list):
            return []
        return [item for item in parsed if isinstance(item, str)]
    except Exception:
        return []
