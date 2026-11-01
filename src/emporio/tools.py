"""Agent tools: typed commands, JSON schemas for the LLM and dispatch by name (D-03, D-21).

Tools only read data. The single write in the package is the escalation log line (NFR-01).
Money is always returned as formatted text, e.g. "R$ 2.199,00" (NFR-04).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from emporio.policies import PolicyBook
from emporio.store_data import StoreData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ToolContext:
    data: StoreData
    policies: PolicyBook
    now: Callable[[], datetime]  # injected clock (D-07)
    escalation_log: Path  # e.g. logs/escalations.jsonl (git-ignored)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, ensure_ascii=False)


def _error(code: str, message: str) -> ToolResult:
    return ToolResult(ok=False, payload={"error": code, "message": message})


class ToolError(Exception):
    """Raised by a tool to return a known error code to the model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _clean_schema(node: Any) -> Any:
    """Make Pydantic JSON schemas friendly to OpenAI-compatible providers.

    Removes titles and null defaults, and turns `X | None` (anyOf with null) into X.
    """
    if isinstance(node, list):
        return [_clean_schema(item) for item in node]
    if not isinstance(node, dict):
        return node
    cleaned: dict[str, Any] = {}
    for key, value in node.items():
        if key == "title" or (key == "default" and value is None):
            continue
        cleaned[key] = _clean_schema(value)
    variants = cleaned.get("anyOf")
    if isinstance(variants, list):
        non_null = [v for v in variants if v != {"type": "null"}]
        if len(non_null) == 1:
            del cleaned["anyOf"]
            cleaned = {**non_null[0], **cleaned}
    return cleaned


class Tool(ABC):
    name: ClassVar[str]
    description: ClassVar[str]  # tells the model WHEN to use the tool
    args_model: ClassVar[type[BaseModel]]

    def __init__(self, context: ToolContext) -> None:
        self.context = context

    @abstractmethod
    def run(self, args: BaseModel) -> dict[str, Any]: ...

    @classmethod
    def schema(cls) -> dict[str, Any]:
        parameters = _clean_schema(cls.args_model.model_json_schema())
        parameters.setdefault("additionalProperties", False)
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": cls.description,
                "parameters": parameters,
            },
        }


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def names(self) -> list[str]:
        return list(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def dispatch(self, name: str, raw_arguments: str | dict[str, Any]) -> ToolResult:
        """Never raises for model mistakes; errors go back to the model as results."""
        tool = self._tools.get(name)
        if tool is None:
            return _error("unknown_tool", f"No tool named {name!r}. Available: {self.names()}")
        try:
            arguments = (
                json.loads(raw_arguments) if isinstance(raw_arguments, str) else raw_arguments
            )
        except json.JSONDecodeError as exc:
            return _error("invalid_arguments", f"Arguments are not valid JSON: {exc.msg}")
        if not isinstance(arguments, dict):
            return _error("invalid_arguments", "Arguments must be a JSON object")
        try:
            args = tool.args_model.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or 'arguments'}: {err['msg']}"
                for err in exc.errors()
            )
            return _error("invalid_arguments", problems)
        try:
            return ToolResult(ok=True, payload=tool.run(args))
        except ToolError as exc:
            return _error(exc.code, exc.message)
        except Exception:
            logger.exception("Tool %s failed", name)
            return _error("internal_error", "The tool failed unexpectedly. Offer human help.")
