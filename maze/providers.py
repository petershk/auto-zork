"""Provider presets: where to send requests and which environment variable holds the key."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    base_url: str | None
    key_env: tuple[str, ...]
    model: str
    api: str  # "responses", "responses_system" (system prompt sent once, in the input) or "chat"


PROVIDERS = {p.id: p for p in (
    Provider("openai", "OpenAI", None, ("OPENAI_API_KEY",), "gpt-4o-mini", "responses"),
    Provider("anthropic", "Anthropic (Claude)", "https://api.anthropic.com/v1/",
             ("ANTHROPIC_API_KEY",), "claude-sonnet-5-5", "chat"),
    Provider("google", "Google (Gemini)", "https://generativelanguage.googleapis.com/v1beta/openai/",
             ("GEMINI_API_KEY", "GOOGLE_API_KEY"), "gemini-2.5-flash", "chat"),
    Provider("xai", "xAI (Grok)", "https://api.x.ai/v1", ("XAI_API_KEY",), "grok-4", "responses_system"),
    # One key, hundreds of models: pick any "vendor/model" id after loading models.
    Provider("openrouter", "OpenRouter (any model)", "https://openrouter.ai/api/v1",
             ("OPENROUTER_API_KEY",), "openai/gpt-4o-mini", "chat"),
    Provider("custom", "Other (OpenAI-compatible)", None, (), "gpt-4o-mini", "responses"),
)}
# Only these may fall back to the generic MAZE_AGENT_* settings; a named provider must
# never receive a key that was saved for a different one.
GENERIC = ("openai", "custom")


def provider_for(provider_id: str) -> Provider:
    try:
        return PROVIDERS[provider_id]
    except KeyError:
        raise ValueError(f"Unknown provider {provider_id!r}. Choose one of: {', '.join(PROVIDERS)}.") from None


def model_env_name(provider_id: str) -> str:
    """Environment variable remembering the last model used with this provider."""
    return f"MAZE_AGENT_MODEL_{provider_for(provider_id).id.upper()}"


def guess_provider() -> str:
    """Last provider used, else the provider a saved key belongs to, else OpenAI."""
    remembered = os.environ.get("MAZE_AGENT_PROVIDER")
    if remembered in PROVIDERS:
        return remembered
    # Provider-specific keys are unambiguous; the generic key could belong to anyone.
    for provider in PROVIDERS.values():
        if provider.id not in GENERIC and find_api_key(provider.id):
            return provider.id
    return "openai"


def key_env_name(provider_id: str) -> str:
    """Environment variable a key for this provider is saved under."""
    provider = provider_for(provider_id)
    return "MAZE_AGENT_API_KEY" if provider.id in GENERIC else provider.key_env[0]


def find_api_key(provider_id: str) -> str | None:
    provider = provider_for(provider_id)
    names = list(provider.key_env)
    if provider.id in GENERIC:
        names.insert(0, "MAZE_AGENT_API_KEY")
    return next((os.environ[n] for n in names if os.environ.get(n, "").strip()), None)
