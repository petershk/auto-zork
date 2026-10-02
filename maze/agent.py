"""A GPT-driven agent that solves a maze by calling a supplied set of tools.

The agent knows nothing about mazes directly — it's handed an OpenAI-style
tool schema plus a function that executes a named tool call, and it drives
a tool-calling loop until the goal is reached or it runs out of steps. This
keeps the agent decoupled from the world it acts in, so the same agent can
later be "loaded" against tools exposed by the MCP server in maze_mcp/
instead of the in-process functions in maze.py.

Uses OpenAI's Responses API (not Chat Completions): newer flagship/codex
models require Responses for tool calling and reject Chat Completions
entirely ("This model is only supported in v1/responses..."), while
Responses still supports older models too, per OpenAI's migration guide.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse
import logging
import math
import os
import threading
import traceback
from dataclasses import dataclass, field
from typing import Callable

from openai import OpenAI, OpenAIError
from token_usage import UsageMeter
from providers import GENERIC, find_api_key, guess_provider, model_env_name, provider_for
from chat_adapter import ChatClient, SystemPromptClient
from context_memory import ContextMemory, context_error

ToolExecutor = Callable[[str, dict], dict]
StepCallback = Callable[[dict], None]

logger = logging.getLogger(__name__)


def _describe_error(exc: Exception) -> str:
    """Build a verbose, single-line description of an OpenAI (or other) exception.

    Includes the exception type, HTTP status code and request id when available
    (both present on openai.APIError subclasses), since "Error code: 400" alone
    is rarely enough to tell what actually went wrong.
    """
    parts = [f"{type(exc).__name__}: {exc}"]
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        parts.append(f"status_code={status_code}")
    request_id = getattr(exc, "request_id", None)
    if request_id:
        parts.append(f"request_id={request_id}")
    body = getattr(exc, "body", None)
    if body:
        parts.append(f"body={body}")
    return " | ".join(parts)

DEFAULT_SYSTEM_PROMPT = (
    "You are an adventurer seeking wealth and adventure in the Great Underground Empire. "
    "Play autonomously: choose and execute your own next action using the tools each turn. "
    "Explore, discover treasures, and deposit them in the trophy case. Equip yourself for "
    "unfriendly inhabitants. Read descriptions and written material, experiment with objects, "
    "and follow discoveries as the adventure unfolds.\n\n"
    "Your observations describe your current surroundings and possessions. Keep a map using "
    "room IDs and observed exits; passages can be one-way. Manage your carrying load with "
    "containers and the reported limits. Use score to check progress. Actions consume turns; "
    "score and hint are free. Request hint when you want assistance; each request is counted.\n\n"
    "Use the provided tools and allowed_actions vocabulary. Address objects by ID or name. "
    "For interact, supply an action and target; item holds spoken words for say or an item ID "
    "for give. Include reasoning with every tool call, in two or three sentences for a viewer following "
    "along: what you just observed, what you think is going on or plan to do next (your current goal, "
    "hypotheses and open leads), and why this action is the best next step. Name rooms and items. "
    "When a tool reports at_goal: true, announce completion."
)

REASONING_PROPERTY = {
    "reasoning": {
        "type": "string",
        "description": "Two or three sentences for a viewer: what you just observed, your current plan or hypothesis, and why this tool call is the best next step.",
    }
}


def _prepare_tools(tools: list[dict]) -> list[dict]:
    """Convert Chat-Completions-style tool schemas (as defined in maze.py) into
    Responses-API tool schemas, with a required `reasoning` argument added to each.

    Chat Completions nests a tool's definition under `function`; Responses
    flattens it to the top level. Doing this conversion here (not in the
    MCP server's tool list) keeps the tool definitions independent of which
    API the agent happens to call.
    """
    prepared = []
    for tool in tools:
        tool = json.loads(json.dumps(tool))  # deep copy
        function = tool["function"]
        params = function.setdefault("parameters", {"type": "object", "properties": {}})
        params.setdefault("properties", {}).update(REASONING_PROPERTY)
        required = set(params.setdefault("required", []))
        required.add("reasoning")
        params["required"] = list(required)
        prepared.append({"type": "function", **function})
    return prepared


@dataclass
class AgentConfig:
    """Agent settings, each overridable via environment variables or constructor args."""

    provider: str = field(default_factory=guess_provider)
    # None means "resolve from the provider preset or environment in __post_init__".
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    max_steps: int = field(default_factory=lambda: int(os.environ.get("MAZE_AGENT_MAX_STEPS", "2000")))
    context_refresh_steps: int = 12
    input_price: float | None = None
    cached_input_price: float | None = None
    output_price: float | None = None

    def __post_init__(self):
        preset = provider_for(self.provider)
        generic = preset.id in GENERIC
        if not self.model:
            legacy = os.environ.get("MAZE_AGENT_MODEL")
            use_legacy = legacy and (generic or os.environ.get("MAZE_AGENT_PROVIDER") == preset.id)
            self.model = os.environ.get(model_env_name(preset.id)) or (legacy if use_legacy else preset.model)
        if not self.base_url:
            self.base_url = (os.environ.get("MAZE_AGENT_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                             if generic else preset.base_url)
        if not self.api_key:
            self.api_key = find_api_key(preset.id)
        if not isinstance(self.context_refresh_steps, int) or isinstance(self.context_refresh_steps, bool) or self.context_refresh_steps < 1:
            raise ValueError("Context refresh steps must be a positive integer.")
        prices = (self.input_price, self.cached_input_price, self.output_price)
        if any(p is not None for p in prices):
            if not all(isinstance(p, (int, float)) and not isinstance(p, bool) and math.isfinite(p) and p >= 0 for p in prices):
                raise ValueError("Provide all three nonnegative finite USD prices per million tokens, or leave all blank.")


def make_client(config: "AgentConfig"):
    """OpenAI client, wrapped for providers that only speak Chat Completions."""
    client = OpenAI(api_key=config.api_key, base_url=config.base_url)
    api = provider_for(config.provider).api
    return ChatClient(client) if api == "chat" else SystemPromptClient(client) if api == "responses_system" else client


def _anthropic_models(config: "AgentConfig") -> list[str]:
    import httpx
    response = httpx.get("https://api.anthropic.com/v1/models", params={"limit": 1000}, timeout=30,
                         headers={"x-api-key": config.api_key, "anthropic-version": "2023-06-01"})
    response.raise_for_status()
    return [m["id"] for m in response.json()["data"]]


def list_models(config: AgentConfig | None = None, name_filter: str = "") -> dict:
    """List model ids available to the configured api_key/base_url.

    Unfiltered by default, since newer chat-capable models (o1, o3, o4-mini,
    etc.) don't all contain "gpt" in their name. Pass `name_filter` (a
    case-insensitive substring) to narrow the list if a provider returns a
    lot of unrelated embedding/audio/image models.
    """
    config = config or AgentConfig()
    try:
        if config.provider == "anthropic":
            names = _anthropic_models(config)
        else:
            client = OpenAI(api_key=config.api_key, base_url=config.base_url)
            names = [m.id.removeprefix("models/") for m in client.models.list().data]
        return {"ok": True, "models": sorted(n for n in names if name_filter.lower() in n.lower())}
    except OpenAIError as exc:
        logger.error("list_models failed: %s", _describe_error(exc))
        return {"ok": False, "error": _describe_error(exc)}
    except Exception as exc:  # noqa: BLE001 - surface anything unexpected, not just OpenAIError
        logger.error("list_models failed unexpectedly:\n%s", traceback.format_exc())
        return {"ok": False, "error": _describe_error(exc)}


class GPTAgent:
    """A tool-calling agent for OpenAI, xAI, Anthropic, Google, OpenRouter or any OpenAI-compatible endpoint."""

    def __init__(self, tools: list[dict], executor: ToolExecutor, config: AgentConfig | None = None):
        self.tools = _prepare_tools(tools)
        self.executor = executor
        self.config = config or AgentConfig()
        self._client = make_client(self.config)

    def _reasoning_options(self) -> dict:
        """Ask OpenAI's reasoning models for a summary of their thinking, so it can be shown."""
        config = self.config
        host = urlparse(config.base_url).hostname if config.base_url else "api.openai.com"
        if config.provider == "openai" and host == "api.openai.com" and re.match(r"(o\d|gpt-5)", config.model or ""):
            return {"reasoning": {"summary": "auto"}}
        return {}

    def check_connection(self) -> dict:
        """Verify the configured model/key/base_url actually work before running a full loop."""
        try:
            response = self._client.responses.create(model=self.config.model, input="ping")
            usage = UsageMeter(self.config).record(response, "connection_check")
            return {"ok": True, "usage": usage}
        except OpenAIError as exc:
            logger.error("check_connection failed: %s", _describe_error(exc))
            return {"ok": False, "error": _describe_error(exc)}
        except Exception as exc:  # noqa: BLE001 - surface anything unexpected, not just OpenAIError
            logger.error("check_connection failed unexpectedly:\n%s", traceback.format_exc())
            return {"ok": False, "error": _describe_error(exc)}

    def run(self, goal: str, on_step: StepCallback | None = None, stop_event: threading.Event | None = None, initial_usage: dict | None = None, get_hints: Callable[[], list[str]] | None = None, initial_memory: dict | None = None) -> dict:
        """Drive the agent until it reaches the goal, stalls, hits max_steps, or `stop_event` is set."""
        previous_response_id: str | None = None
        # First turn sends the goal as input; later turns only send this turn's
        # function_call_output items, since previous_response_id (with store=True)
        # keeps the rest of the conversation server-side.
        pending_input: list = [{"role": "user", "content": goal}]
        idle_responses = 0
        memory = ContextMemory.restore(initial_memory) if initial_memory else ContextMemory(goal)
        if initial_memory:
            pending_input = memory.input()
        context_refreshes = 0
        window_steps = 0
        meter = UsageMeter(self.config, initial_usage)

        def record_usage(response, phase, step):
            totals = meter.record(response, phase, step)
            if on_step:
                on_step({"step": step, "type": "usage", "usage": totals})

        def finish(result):
            return {**result, "usage": meter.snapshot(), "context_refreshes": context_refreshes, "memory": memory.snapshot()}

        for step in range(1, self.config.max_steps + 1):
            if stop_event is not None and stop_event.is_set():
                return finish({"success": False, "reason": "stopped by user", "steps": step - 1})

            def refresh_context(reason):
                nonlocal previous_response_id, pending_input, context_refreshes, window_steps
                previous_response_id = None
                pending_input = memory.input()
                context_refreshes += 1
                window_steps = 0
                if on_step:
                    on_step({"step": step, "type": "reasoning", "content": f"Refreshing context ({reason}); continuing with observed game memory."})

            if previous_response_id and window_steps >= self.config.context_refresh_steps:
                refresh_context("scheduled")

            for hint in get_hints() if get_hints else []:
                memory.viewer_hints.append(hint)
                pending_input.append({"role": "user", "content": "Viewer hint: " + hint})
                if on_step:
                    on_step({"step": step, "type": "reasoning", "content": "Viewer hint received: " + hint})

            response = None
            for attempt in range(2):
                try:
                    response = self._client.responses.create(
                        model=self.config.model,
                        instructions=self.config.system_prompt,
                        input=pending_input,
                        previous_response_id=previous_response_id,
                        tools=self.tools,
                        tool_choice="auto",
                        store=True,
                        **self._reasoning_options(),
                    )
                    break
                except OpenAIError as exc:
                    if context_error(exc) and previous_response_id and attempt == 0:
                        refresh_context("context limit reached")
                        continue
                    logger.error("responses.create failed on step %d: %s", step, _describe_error(exc))
                    return finish({"success": False, "reason": _describe_error(exc), "steps": step})
                except Exception as exc:
                    logger.error("responses.create failed unexpectedly on step %d:\n%s", step, traceback.format_exc())
                    return finish({"success": False, "reason": _describe_error(exc), "steps": step})

            window_steps += 1
            record_usage(response, "agent", step)
            previous_response_id = response.id
            function_calls = [item for item in response.output if item.type == "function_call"]

            if on_step:
                for item in response.output:
                    if item.type == "reasoning":  # a summary of the model's own thinking, when it provides one
                        thinking = "\n".join(part.text for part in (getattr(item, "summary", None) or []) if getattr(part, "text", None))
                        if thinking:
                            on_step({"step": step, "type": "reasoning", "content": "(thinking) " + thinking})
                        continue
                    if item.type != "message":
                        continue
                    text = "".join(
                        part.text for part in item.content if getattr(part, "type", None) == "output_text"
                    )
                    if text:
                        on_step({"step": step, "type": "reasoning", "content": text})

            if not function_calls:
                idle_responses += 1
                if idle_responses == 1 and step < self.config.max_steps:
                    if on_step:
                        on_step({"step": step, "type": "reasoning", "content": "Continuing autonomous play after a response with no action."})
                    pending_input = [{"role": "user", "content": "Continue playing autonomously. Choose your next action and execute it with a tool call, using your current observations."}]
                    continue
                if on_step:
                    on_step(
                        {
                            "step": step,
                            "type": "reasoning",
                            "content": f"Stopped calling tools (status={response.status!r}).",
                        }
                    )
                reason = "max_steps reached" if step == self.config.max_steps else "agent stopped calling tools after a continuation attempt"
                return finish({"success": False, "reason": reason, "steps": step})

            idle_responses = 0
            reached_goal = False
            game_over = False
            pending_input = []
            for call in function_calls:
                name = call.name
                try:
                    args = json.loads(call.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                reasoning = args.pop("reasoning", None)
                if reasoning and on_step:
                    on_step({"step": step, "type": "reasoning", "content": f"[{name}] {reasoning}"})

                if reached_goal or game_over:
                    # Answer every call in the batch without taking further
                    # actions after the goal has been reached.
                    result = {"success": False, "error": "Skipped: game is over.", "at_goal": reached_goal, "game_over": game_over}
                else:
                    result = self.executor(name, args)
                memory.observe(name, args, result, reasoning)
                if on_step:
                    on_step({"step": step, "type": "memory", "memory": memory.snapshot()})
                    on_step({"step": step, "type": "tool", "tool": name, "args": args, "result": result})

                if result.get("at_goal"):
                    reached_goal = True
                if result.get("game_over"):
                    game_over = True

                pending_input.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result),
                    }
                )

            if game_over:
                return finish({"success": False, "reason": "The player died. Restore a saved game or start again.", "steps": step})

            if reached_goal:
                # Give the model the final observations, with no tools
                # available, so it can announce completion without moving.
                try:
                    final_response = self._client.responses.create(
                        model=self.config.model,
                        instructions=self.config.system_prompt,
                        input=[
                            *pending_input,
                            {"role": "user", "content": "The maze reported at_goal: true. Briefly announce completion based on the tool results."},
                        ],
                        previous_response_id=previous_response_id,
                        store=True,
                    )
                    record_usage(final_response, "completion", step)
                    message = final_response.output_text
                    if message and on_step:
                        on_step({"step": step, "type": "reasoning", "content": message})
                    return finish({"success": True, "steps": step, "message": message})
                except Exception as exc:
                    # Reaching the goal remains a success even if the extra
                    # model request fails.
                    logger.error("Completion announcement failed: %s", _describe_error(exc))
                    return finish({"success": True, "steps": step, "announcement_error": _describe_error(exc)})

        return finish({"success": False, "reason": "max_steps reached", "steps": self.config.max_steps})

