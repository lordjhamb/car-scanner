"""OpenAI provider ("Codex" option in the UI), via the OpenAI Responses API.

Kept separate from the Claude provider; both implement the same ``run`` interface.
"""
from __future__ import annotations

import json

import openai

from .base import AIError, Message, RunResult, Tool, describe_call


class OpenAIProvider:
    name = "openai"

    def __init__(self, api_key: str, model: str = "gpt-5", client=None):
        if not api_key and client is None:
            raise AIError("Add your OpenAI API key in Settings to use Codex.")
        self.model = model
        self.client = client or openai.OpenAI(api_key=api_key, max_retries=2, timeout=120.0)

    @staticmethod
    def _input(m: Message):
        if m.role == "assistant":
            return {"role": "assistant", "content": m.text}
        if not m.images:
            return {"role": "user", "content": m.text}
        parts = [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{b}"} for b in m.images]
        return {"role": "user", "content": parts + [{"type": "input_text", "text": m.text}]}

    def run(self, system: str, messages: list[Message], tools: list[Tool], *,
            web_search: bool = True, effort: str = "medium", max_turns: int = 10) -> RunResult:
        by_name = {t.name: t for t in tools}
        api_tools = [{"type": "function", "name": t.name, "description": t.description, "parameters": t.schema}
                     for t in tools]
        if web_search:
            api_tools.append({"type": "web_search"})
        pending = [self._input(m) for m in messages]
        previous = None
        log, sources = [], []
        for _ in range(max_turns):
            try:
                kwargs = {"model": self.model, "instructions": system, "input": pending, "tools": api_tools}
                if previous:
                    kwargs["previous_response_id"] = previous
                resp = self.client.responses.create(**kwargs)
            except openai.AuthenticationError as e:
                raise AIError("OpenAI rejected the API key. Check it in Settings.") from e
            except openai.NotFoundError as e:
                raise AIError(f"Model {self.model!r} wasn't found. Pick another model in Settings.") from e
            except openai.RateLimitError as e:
                raise AIError("OpenAI's rate limit was reached. Wait a minute and try again.") from e
            except openai.APIStatusError as e:
                raise AIError(f"OpenAI returned an error ({e.status_code}). Try again shortly.") from e
            except openai.APIConnectionError as e:
                raise AIError("Couldn't reach OpenAI. Check the internet connection.") from e
            previous = resp.id
            calls = []
            for item in resp.output or []:
                if item.type == "function_call":
                    calls.append(item)
                elif item.type == "web_search_call":
                    q = getattr(getattr(item, "action", None), "query", "") or ""
                    log.append({"tool": "web_search", "label": "Searched the web", "detail": q, "ok": True})
                elif item.type == "message":
                    for part in getattr(item, "content", []) or []:
                        for ann in getattr(part, "annotations", []) or []:
                            url = getattr(ann, "url", None)
                            if url and all(s["url"] != url for s in sources):
                                sources.append({"title": getattr(ann, "title", "") or url, "url": url})
            if not calls:
                return RunResult(text=(resp.output_text or "").strip(), tool_log=log, sources=sources,
                                 stop_reason="end_turn", model=self.model)
            pending = []
            for c in calls:
                tool = by_name.get(c.name)
                try:
                    args = json.loads(c.arguments or "{}")
                except ValueError:
                    args = {}
                if tool is None:
                    out, ok = f"Unknown tool {c.name}", False
                else:
                    try:
                        out, ok = tool.call(args), True
                    except Exception as e:
                        out, ok = f"Error: {e}", False
                log.append({"tool": c.name, "label": tool.label if tool else c.name,
                            "detail": describe_call(tool, args) if tool else "", "ok": ok})
                pending.append({"type": "function_call_output", "call_id": c.call_id, "output": out})
        raise AIError("The assistant took too many steps. Try a narrower question.")
