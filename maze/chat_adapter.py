"""Present Chat Completions providers through the Responses-style interface the agent loop uses."""
from types import SimpleNamespace


def _usage(usage):
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    completion = getattr(usage, "completion_tokens", 0) or 0
    cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0
    reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
    return SimpleNamespace(input_tokens=prompt, output_tokens=completion,
                           total_tokens=getattr(usage, "total_tokens", None) or prompt + completion,
                           input_tokens_details=SimpleNamespace(cached_tokens=cached),
                           output_tokens_details=SimpleNamespace(reasoning_tokens=reasoning))


def _tool_call(call):
    data = {"id": call.id, "type": "function",
            "function": {"name": call.function.name, "arguments": call.function.arguments or "{}"}}
    # Gemini returns opaque thought signatures here that must be echoed back.
    extra = getattr(call, "extra_content", None) or (getattr(call, "model_extra", None) or {}).get("extra_content")
    if extra:
        data["extra_content"] = extra
    return data


class ChatResponses:
    """Stateless Chat Completions made to look like `responses.create` with previous_response_id."""

    def __init__(self, client):
        self._client = client
        self._messages = []
        self._count = 0

    def create(self, *, model, input, instructions=None, previous_response_id=None,
               tools=None, tool_choice=None, store=None):
        if isinstance(input, str):
            input = [{"role": "user", "content": input}]
        history = list(self._messages) if previous_response_id else []
        if not history and instructions:
            history.append({"role": "system", "content": instructions})
        for item in input:
            if item.get("type") == "function_call_output":
                history.append({"role": "tool", "tool_call_id": item["call_id"], "content": item["output"]})
            else:
                history.append({"role": item["role"], "content": item["content"]})
        request = {"model": model, "messages": history}
        if tools:
            request["tools"] = [{"type": "function", "function": {k: v for k, v in tool.items() if k != "type"}}
                                for tool in tools]
            request["tool_choice"] = tool_choice or "auto"
        completion = self._client.chat.completions.create(**request)
        message = completion.choices[0].message
        calls = message.tool_calls or []
        assistant = {"role": "assistant", "content": message.content}
        if calls:
            assistant["tool_calls"] = [_tool_call(call) for call in calls]
        elif message.content is None:
            assistant["content"] = ""
        # History is committed only after a successful call, so a retry starts clean.
        self._messages = history + [assistant]
        self._count += 1
        output = []
        extra = getattr(message, "model_extra", None) or {}
        # Some providers (OpenRouter, DeepSeek-style models) return the model's reasoning alongside its answer.
        thinking = (getattr(message, "reasoning_content", None) or getattr(message, "reasoning", None)
                    or extra.get("reasoning_content") or extra.get("reasoning"))
        if isinstance(thinking, str) and thinking.strip():
            output.append(SimpleNamespace(type="reasoning", summary=[SimpleNamespace(type="summary_text", text=thinking.strip())]))
        if message.content:
            output.append(SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=message.content)]))
        output.extend(SimpleNamespace(type="function_call", name=call.function.name,
                                      arguments=call.function.arguments, call_id=call.id) for call in calls)
        return SimpleNamespace(id=f"chat-{self._count}", status="completed", output=output,
                               output_text=message.content or "", usage=_usage(completion.usage),
                               service_tier=getattr(completion, "service_tier", None))


class ChatClient:
    """Drop-in for the pieces of OpenAI() the agent uses."""

    def __init__(self, client):
        self.responses = ChatResponses(client)
        self.models = client.models


class _SystemPromptResponses:
    """Responses API for providers that reject `instructions` alongside previous_response_id (xAI).

    The system prompt rides in the first request's input, so the stored conversation keeps it.
    """

    def __init__(self, responses):
        self._responses = responses

    def create(self, **kwargs):
        instructions = kwargs.pop("instructions", None)
        if instructions and not kwargs.get("previous_response_id"):
            items = [{"role": "user", "content": kwargs["input"]}] if isinstance(kwargs["input"], str) else list(kwargs["input"])
            kwargs["input"] = [{"role": "system", "content": instructions}, *items]
        return self._responses.create(**kwargs)


class SystemPromptClient:
    def __init__(self, client):
        self.responses = _SystemPromptResponses(client.responses)
        self.models = client.models
