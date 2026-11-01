"""Tool base and ToolRegistry: schemas and dispatch errors (D-03)."""

import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from emporio.tools import Tool, ToolRegistry


class EchoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Text to echo.")
    times: int | None = Field(default=None, ge=1, description="Repetitions.")


class EchoTool(Tool):
    name = "echo"
    description = "Echo the text back."
    args_model = EchoArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        assert isinstance(args, EchoArgs)
        return {"echo": args.text * (args.times or 1)}


class BrokenTool(Tool):
    name = "broken"
    description = "Always fails."
    args_model = EchoArgs

    def run(self, args: BaseModel) -> dict[str, Any]:
        raise RuntimeError("boom")


registry = ToolRegistry([EchoTool(None), BrokenTool(None)])  # type: ignore[arg-type]


def test_registry_names_in_order() -> None:
    assert registry.names() == ["echo", "broken"]


def test_schemas_are_valid_function_schemas() -> None:
    schema = registry.schemas()[0]
    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"] == "echo"
    assert function["description"] == "Echo the text back."
    params = function["parameters"]
    assert params["type"] == "object"
    assert params["additionalProperties"] is False
    assert params["required"] == ["text"]


def test_schemas_are_provider_friendly() -> None:
    # Optional fields become plain types; no titles or null defaults (OpenAI-compatible APIs)
    params = registry.schemas()[0]["function"]["parameters"]
    text = json.dumps(params)
    assert "title" not in text
    assert "anyOf" not in text
    assert params["properties"]["times"] == {
        "type": "integer",
        "minimum": 1,
        "description": "Repetitions.",
    }


def test_dispatch_unknown_tool_returns_error() -> None:
    result = registry.dispatch("get_weather", {})
    assert not result.ok
    assert result.payload["error"] == "unknown_tool"


def test_dispatch_invalid_json_returns_error() -> None:
    result = registry.dispatch("echo", "{bad")
    assert not result.ok
    assert result.payload["error"] == "invalid_arguments"


def test_dispatch_invalid_arguments_returns_error() -> None:
    result = registry.dispatch("echo", {"text": "hi", "times": "many"})
    assert not result.ok
    assert result.payload["error"] == "invalid_arguments"
    assert "times" in result.payload["message"]


def test_dispatch_rejects_unknown_fields() -> None:
    result = registry.dispatch("echo", {"text": "hi", "extra": 1})
    assert result.payload["error"] == "invalid_arguments"


def test_dispatch_accepts_dict_and_json_string() -> None:
    as_dict = registry.dispatch("echo", {"text": "hi", "times": 2})
    as_json = registry.dispatch("echo", '{"text": "hi", "times": 2}')
    assert as_dict == as_json
    assert as_dict.ok and as_dict.payload == {"echo": "hihi"}


def test_dispatch_empty_arguments_string() -> None:
    result = registry.dispatch("echo", "")
    assert result.payload["error"] == "invalid_arguments"


def test_tool_exception_becomes_internal_error() -> None:
    result = registry.dispatch("broken", {"text": "x"})
    assert not result.ok
    assert result.payload["error"] == "internal_error"


def test_result_to_json() -> None:
    result = registry.dispatch("echo", {"text": "R$ 2.199,00"})
    assert json.loads(result.to_json()) == {"echo": "R$ 2.199,00"}
    assert "R$ 2.199,00" in result.to_json()  # no ASCII escaping


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class PaintArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    color: Color = Field(description="Paint color.")


class PaintTool(EchoTool):
    name = "paint"
    args_model = PaintArgs  # type: ignore[assignment]


def test_schemas_inline_enum_references() -> None:
    params = ToolRegistry([PaintTool(None)]).schemas()[0]["function"]["parameters"]  # type: ignore[arg-type]
    text = json.dumps(params)
    assert "$ref" not in text and "$defs" not in text
    assert params["properties"]["color"] == {
        "enum": ["red", "blue"],
        "type": "string",
        "description": "Paint color.",
    }
