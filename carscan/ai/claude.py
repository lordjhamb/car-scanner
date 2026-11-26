"""Claude provider (Anthropic Python SDK, Messages API with a manual tool loop).

The manual loop (rather than the beta tool runner) lets the app record every tool
call for the UI, gate actions that change the car behind user approval, and keep
the provider interface identical to the OpenAI one.
"""
from __future__ import annotations

import anthropic

from .base import AIError, Message, RunResult, Tool, describe_call

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side refusal fallbacks ("default" routing).
_FALLBACK_PREFIXES = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable-5")


class ClaudeProvider:
    name = "claude"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", client=None):
        if not api_key and client is None:
            raise AIError("Add your Anthropic API key in Settings to use Claude.")
        self.model = model
        self.client = client or anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)

    @staticmethod
    def _content(m: Message):
        if not m.images:
            return m.text
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b}}
                  for b in m.images]
        return blocks + [{"type": "text", "text": m.text}]

    def run(self, system: str, messages: list[Message], tools: list[Tool], *,
            web_search: bool = True, effort: str = "medium", max_turns: int = 10) -> RunResult:
        by_name = {t.name: t for t in tools}
        api_tools = [{"name": t.name, "description": t.description, "input_schema": t.schema} for t in tools]
        if web_search:
            api_tools.append(dict(WEB_SEARCH_TOOL))
        msgs = [{"role": m.role, "content": self._content(m)} for m in messages]
        log, sources = [], []
        extra = {}
        if self.model.startswith(_FALLBACK_PREFIXES):
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}

        for _ in range(max_turns):
            try:
                resp = self.client.beta.messages.create(
                    model=self.model, max_tokens=16000, system=system, messages=msgs,
                    tools=api_tools, thinking={"type": "adaptive"}, output_config={"effort": effort}, **extra)
            except anthropic.AuthenticationError as e:
                raise AIError("Anthropic rejected the API key. Check it in Settings.") from e
            except anthropic.PermissionDeniedError as e:
                raise AIError("This Anthropic key can't use that model. Pick another model in Settings.") from e
            except anthropic.NotFoundError as e:
                raise AIError(f"Model {self.model!r} wasn't found. Pick another model in Settings.") from e
            except anthropic.RateLimitError as e:
                raise AIError("Anthropic's rate limit was reached. Wait a minute and try again.") from e
            except anthropic.APIStatusError as e:
                raise AIError(f"Anthropic returned an error ({e.status_code}). Try again shortly.") from e
            except anthropic.APIConnectionError as e:
                raise AIError("Couldn't reach Anthropic. Check the internet connection.") from e

            for b in resp.content:
                if b.type == "server_tool_use" and getattr(b, "name", "") == "web_search":
                    q = (getattr(b, "input", None) or {}).get("query", "")
                    log.append({"tool": "web_search", "label": "Searched the web", "detail": q, "ok": True})
                elif b.type == "web_search_tool_result":
                    content = getattr(b, "content", None)
                    if isinstance(content, list):
                        for r in content:
                            url = getattr(r, "url", None)
                            if url and all(s["url"] != url for s in sources):
                                sources.append({"title": getattr(r, "title", "") or url, "url": url})

            if resp.stop_reason == "refusal":
                raise AIError("The assistant declined this request. It only helps with diagnosing cars.")
            if resp.stop_reason == "pause_turn":
                msgs.append({"role": "assistant", "content": resp.content})
                continue
            tool_uses = [b for b in resp.content if b.type == "tool_use"]
            if resp.stop_reason == "tool_use" and tool_uses:
                msgs.append({"role": "assistant", "content": resp.content})
                results = []
                for tu in tool_uses:
                    tool = by_name.get(tu.name)
                    args = tu.input if isinstance(tu.input, dict) else {}
                    if tool is None:
                        out, ok = f"Unknown tool {tu.name}", False
                    else:
                        try:
                            out, ok = tool.call(args), True
                        except Exception as e:  # report the failure to the model, don't crash the chat
                            out, ok = f"Error: {e}", False
                    log.append({"tool": tu.name, "label": tool.label if tool else tu.name,
                                "detail": describe_call(tool, args) if tool else "", "ok": ok})
                    item = {"type": "tool_result", "tool_use_id": tu.id, "content": out}
                    if not ok:
                        item["is_error"] = True
                    results.append(item)
                msgs.append({"role": "user", "content": results})
                continue
            text = "\n\n".join(b.text for b in resp.content if b.type == "text").strip()
            if resp.stop_reason == "max_tokens" and not text:
                raise AIError("The answer was too long. Try a narrower question.")
            return RunResult(text=text, tool_log=log, sources=sources, stop_reason=resp.stop_reason,
                             model=getattr(resp, "model", self.model))
        raise AIError("The assistant took too many steps. Try a narrower question.")
