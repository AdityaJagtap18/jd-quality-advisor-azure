"""Azure OpenAI client exposing the subset of the Anthropic `messages.create` API
that the agents use, so every agent runs on an Azure OpenAI deployment (e.g.
gpt-4.1-mini) with no changes to its call sites.

The `model=` argument passed by the agents is ignored; the deployment named by
AZURE_OPENAI_DEPLOYMENT is always used.
"""
import json
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from openai import AzureOpenAI

_DEFAULT_API_VERSION = "2024-12-01-preview"


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict
    type: str = "tool_use"


@dataclass
class Response:
    content: list = field(default_factory=list)
    stop_reason: str = "end_turn"


def _get(block: Any, key: str, default: Any = None) -> Any:
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def _convert_messages(system: Optional[str], messages: list) -> list:
    out: list = []
    if system:
        out.append({"role": "system", "content": system})
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        texts, tool_calls = [], []
        for block in content:
            btype = _get(block, "type")
            if btype == "text":
                texts.append(_get(block, "text", ""))
            elif btype == "tool_use":
                tool_calls.append({
                    "id": _get(block, "id"),
                    "type": "function",
                    "function": {
                        "name": _get(block, "name"),
                        "arguments": json.dumps(_get(block, "input", {})),
                    },
                })
            elif btype == "tool_result":
                result = _get(block, "content", "")
                if not isinstance(result, str):
                    result = json.dumps(result)
                out.append({
                    "role": "tool",
                    "tool_call_id": _get(block, "tool_use_id"),
                    "content": result,
                })
        if texts or tool_calls:
            entry: dict = {"role": role, "content": "\n".join(texts) or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            out.append(entry)
    return out


def _convert_tools(tools: list) -> list:
    return [{
        "type": "function",
        "function": {
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        },
    } for t in tools]


class _Messages:
    def __init__(self, client: AzureOpenAI, deployment: str):
        self._client = client
        self._deployment = deployment

    def create(self, *, max_tokens: int, messages: list, system: Optional[str] = None,
               tools: Optional[list] = None, tool_choice: Optional[dict] = None,
               model: Optional[str] = None, **_: Any) -> Response:
        kwargs: dict = {
            "model": self._deployment,
            "messages": _convert_messages(system, messages),
            "max_completion_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = _convert_tools(tools)
            kwargs["tool_choice"] = (
                "required" if (tool_choice or {}).get("type") in ("any", "tool") else "auto"
            )

        choice = self._client.chat.completions.create(**kwargs).choices[0]
        content: list = []
        if choice.message.content:
            content.append(TextBlock(text=choice.message.content))
        for call in choice.message.tool_calls or []:
            content.append(ToolUseBlock(
                id=call.id,
                name=call.function.name,
                input=json.loads(call.function.arguments or "{}"),
            ))
        has_tool = any(b.type == "tool_use" for b in content)
        return Response(content=content, stop_reason="tool_use" if has_tool else "end_turn")


class AzureLLMClient:
    """Drop-in stand-in for `anthropic.Anthropic` backed by Azure OpenAI."""

    def __init__(self, endpoint: str, api_key: str, deployment: str,
                 api_version: str = _DEFAULT_API_VERSION):
        raw = AzureOpenAI(azure_endpoint=endpoint, api_key=api_key, api_version=api_version)
        self.messages = _Messages(raw, deployment)


def build_client() -> AzureLLMClient:
    """Build the client from env vars; raises RuntimeError naming any that are missing."""
    values = {
        "AZURE_OPENAI_ENDPOINT": os.getenv("AZURE_OPENAI_ENDPOINT"),
        "AZURE_OPENAI_API_KEY": os.getenv("AZURE_OPENAI_API_KEY"),
        "AZURE_OPENAI_DEPLOYMENT": os.getenv("AZURE_OPENAI_DEPLOYMENT"),
    }
    missing = [k for k, v in values.items() if not v]
    if missing:
        raise RuntimeError(f"Missing env var(s): {', '.join(missing)}")
    return AzureLLMClient(
        endpoint=values["AZURE_OPENAI_ENDPOINT"],
        api_key=values["AZURE_OPENAI_API_KEY"],
        deployment=values["AZURE_OPENAI_DEPLOYMENT"],
        api_version=os.getenv("AZURE_OPENAI_API_VERSION", _DEFAULT_API_VERSION),
    )
