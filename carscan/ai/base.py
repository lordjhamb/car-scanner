"""Provider-neutral types for the AI layer."""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol


class AIError(Exception):
    """A failure that is safe to show to the user as-is."""


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], Any]
    label: str = ""                 # how the step is shown in the UI ("Read codes")

    def call(self, args: dict) -> str:
        result = self.handler(args or {})
        return result if isinstance(result, str) else json.dumps(result, default=str)


@dataclass
class Message:
    role: str                       # user | assistant
    text: str
    images: list[str] = field(default_factory=list)   # base64 JPEGs (user turns only)


@dataclass
class RunResult:
    text: str
    tool_log: list[dict] = field(default_factory=list)   # [{"tool", "label", "detail", "ok"}]
    sources: list[dict] = field(default_factory=list)    # [{"title", "url"}]
    stop_reason: str = ""
    model: str = ""


class Provider(Protocol):
    name: str
    model: str

    def run(self, system: str, messages: list[Message], tools: list[Tool], *,
            web_search: bool = True, effort: str = "medium", max_turns: int = 10) -> RunResult: ...


def describe_call(tool: Tool, args: dict) -> str:
    if not args:
        return ""
    parts = [f"{k}={v}" for k, v in args.items() if k not in ("plan", "report")]
    return ", ".join(parts)[:160]
