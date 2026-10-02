"""API-reported usage and estimated USD cost, never a local token approximation."""
from copy import deepcopy
from urllib.parse import urlparse

# Standard text-token USD / 1M, checked 2026-09-30 against the linked model pages.
RATES = {
    # GPT-3.5 has no published cached-input discount.
    "gpt-3.5-turbo": (0.50, None, 1.50),
    "gpt-3.5-turbo-0125": (0.50, None, 1.50),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
    "gpt-4o-mini-2024-07-18": (0.15, 0.075, 0.60),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-2024-08-06": (2.50, 1.25, 10.00),
    "gpt-4o-2024-11-20": (2.50, 1.25, 10.00),
    "gpt-4.1": (2.00, 0.50, 8.00),
    "gpt-4.1-2025-04-14": (2.00, 0.50, 8.00),
    "gpt-4.1-mini": (0.40, 0.10, 1.60),
    "gpt-4.1-mini-2025-04-14": (0.40, 0.10, 1.60),
}


def value(obj, name, default=None):
    return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)


class UsageMeter:
    def __init__(self, config, initial=None):
        self.config = config
        self.data = deepcopy(initial) if isinstance(initial, dict) else {
            "requests": 0, "input_tokens": 0, "uncached_input_tokens": 0,
            "cached_input_tokens": 0, "cache_write_tokens": 0,
            "output_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0,
            "missing_usage_requests": 0, "unpriced_requests": 0,
            "known_cost_usd": 0.0, "calls": [],
        }

    def record(self, response, phase, step=0):
        model = value(response, "model") or self.config.model
        usage = value(response, "usage")
        call = {"phase": phase, "step": step, "model": model, "estimated_cost_usd": None}
        self.data["requests"] += 1
        if usage is None:
            call["usage_available"] = False
            call["unpriced_reason"] = "API response did not include usage."
            self.data["missing_usage_requests"] += 1
        else:
            input_tokens = value(usage, "input_tokens", 0) or 0
            output_tokens = value(usage, "output_tokens", 0) or 0
            details = value(usage, "input_tokens_details")
            cached = min(input_tokens, value(details, "cached_tokens", 0) or 0)
            writes = value(details, "cache_write_tokens", 0) or 0
            call.update({"usage_available": True, "input_tokens": input_tokens,
                         "uncached_input_tokens": input_tokens - cached,
                         "cached_input_tokens": cached, "cache_write_tokens": writes,
                         "output_tokens": output_tokens,
                         "reasoning_tokens": value(value(usage, "output_tokens_details"), "reasoning_tokens", 0) or 0,
                         "total_tokens": value(usage, "total_tokens", input_tokens + output_tokens) or 0})
            for key in ("input_tokens", "uncached_input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens", "total_tokens"):
                self.data[key] += call[key]
            custom = tuple(getattr(self.config, key, None) for key in ("input_price", "cached_input_price", "output_price"))
            is_openai = not self.config.base_url or urlparse(self.config.base_url).hostname == "api.openai.com"
            rates = custom if all(rate is not None for rate in custom) else RATES.get(model) if is_openai else None
            tier = value(response, "service_tier", "default") or "default"
            # Unusual cache-write billing and service tiers need their own tariff.
            if rates is not None and (not cached or rates[1] is not None) and not writes and tier in ("default", "auto"):
                cost = ((input_tokens - cached) * rates[0] + cached * (rates[1] or 0) + output_tokens * rates[2]) / 1_000_000
                call["estimated_cost_usd"] = cost
                call["rates_per_million"] = {"input": rates[0], "cached_input": rates[1], "output": rates[2]}
                call["pricing_source"] = "custom" if all(rate is not None for rate in custom) else "OpenAI standard text rates, checked 2026-09-30"
                self.data["known_cost_usd"] += cost
            else:
                if rates is None:
                    call["unpriced_reason"] = "Custom provider requires explicit rates." if not is_openai else f"No built-in prices for model {model}."
                elif writes:
                    call["unpriced_reason"] = "Cache-write billing needs a separate tariff."
                elif cached and rates[1] is None:
                    call["unpriced_reason"] = "No published cached-input rate for this model."
                else:
                    call["unpriced_reason"] = f"Service tier {tier} needs its own rates."
                self.data["unpriced_requests"] += 1
        self.data["calls"].append(call)
        return self.snapshot()

    def snapshot(self):
        result = deepcopy(self.data)
        result["estimated_cost_usd"] = None if result["missing_usage_requests"] or result["unpriced_requests"] else result["known_cost_usd"]
        result["cache_hit_percent"] = (100 * result["cached_input_tokens"] / result["input_tokens"]) if result["input_tokens"] else 0
        return result
