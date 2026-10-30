"""Tests for the LLM-as-judge (agents/eval_llm_judge.py).

The Anthropic client is mocked — no live API calls. run_llm_judge accepts an
optional client for injection; production/orchestrator calls build one internally.
"""

import json
from types import SimpleNamespace

from agents.eval_llm_judge import run_llm_judge


def _mock_client(text=None, raise_exc=None):
    """A stand-in Anthropic client whose messages.create returns `text` or raises."""
    def create(*args, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(content=[SimpleNamespace(text=text)])
    return SimpleNamespace(messages=SimpleNamespace(create=create))


_VALID_JSON = json.dumps({
    "dimension_support": {
        "completeness": True, "skill_specificity": False, "role_coherence": True,
        "cognitive_load": True, "inclusion_signals": True, "compensation": False,
    },
    "unsupported_scores": ["skill_specificity", "compensation"],
    "vagueness_flags": ["Add more detail about compensation"],
    "reasoning": "Most dimensions are supported but two lack grounding.",
    "verdict": "warn",
})


def test_valid_json_parsed_into_result():
    result = run_llm_judge("jd", {"x": 1}, client=_mock_client(text=_VALID_JSON))
    assert result["verdict"] == "warn"
    assert result["dimension_support"]["completeness"] is True
    assert result["dimension_support"]["skill_specificity"] is False
    assert result["unsupported_scores"] == ["skill_specificity", "compensation"]
    assert result["vagueness_flags"] == ["Add more detail about compensation"]
    assert "supported" in result["reasoning"]
    assert result["duration_ms"] >= 0.0


def test_malformed_json_falls_back_to_warn():
    result = run_llm_judge("jd", {"x": 1}, client=_mock_client(text="not json at all {broken"))
    assert result["verdict"] == "warn"
    assert all(v is False for v in result["dimension_support"].values())
    assert "failed" in result["reasoning"].lower()


def test_api_exception_falls_back_to_warn():
    result = run_llm_judge("jd", {"x": 1},
                           client=_mock_client(raise_exc=RuntimeError("boom")))
    assert result["verdict"] == "warn"
    assert "boom" in result["reasoning"]
    assert all(v is False for v in result["dimension_support"].values())


def test_json_with_trailing_prose_is_extracted():
    text = _VALID_JSON + "\n\nHope that helps!"
    result = run_llm_judge("jd", {"x": 1}, client=_mock_client(text=text))
    assert result["verdict"] == "warn"
    assert result["unsupported_scores"] == ["skill_specificity", "compensation"]
