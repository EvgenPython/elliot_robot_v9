from __future__ import annotations

from datetime import datetime, timedelta
from hashlib import sha256


RATES = {
    "claude-sonnet-5": {
        "input": 2.0,
        "output": 10.0,
        "cache_read": 0.20,
        "cache_write_5m": 2.50,
        "cache_write_1h": 4.0,
    }
}


def estimate(model: str, usage: dict, cache_ttl="1h") -> float:
    rate = next(
        (v for k, v in RATES.items() if model == k or model.startswith(k + "-")),
        None,
    )
    if not rate:
        return 0.0

    vals = {
        k: int(usage.get(k, 0) or 0)
        for k in [
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        ]
    }

    write = (
        rate["cache_write_1h"]
        if cache_ttl == "1h"
        else rate["cache_write_5m"]
    )

    usd = (
        vals["input_tokens"] * rate["input"]
        + vals["output_tokens"] * rate["output"]
        + vals["cache_read_input_tokens"] * rate["cache_read"]
        + vals["cache_creation_input_tokens"] * write
    ) / 1_000_000

    return round(usd, 8)


def _ttl(cache_ttl: str) -> timedelta:
    if cache_ttl == "1h":
        return timedelta(hours=1)
    return timedelta(minutes=5)


class SimulatedLiveCostTracker:
    """Reprices prompt-cache usage using historical market time.

    Accelerated replay may keep Anthropic's real wall-clock cache alive while
    several historical hours pass. This tracker models whether the same stable
    prefix would still exist if requests had occurred at their replay sim_time.
    """

    def __init__(self):
        self._last_use: dict[str, datetime] = {}

    def calculate(
        self,
        *,
        stable_prefix: str,
        sim_time: datetime,
        model: str,
        usage: dict,
        cache_ttl: str,
    ) -> dict:
        key = sha256(stable_prefix.encode("utf-8")).hexdigest()

        previous = self._last_use.get(key)
        ttl = _ttl(cache_ttl)

        simulated_hit = (
            previous is not None
            and sim_time >= previous
            and (sim_time - previous) <= ttl
        )

        sim_usage = {
            k: int(usage.get(k, 0) or 0)
            for k in [
                "input_tokens",
                "output_tokens",
                "cache_read_input_tokens",
                "cache_creation_input_tokens",
            ]
        }

        cacheable_tokens = (
            sim_usage["cache_read_input_tokens"]
            + sim_usage["cache_creation_input_tokens"]
        )

        if cacheable_tokens > 0:
            if simulated_hit:
                sim_usage["cache_read_input_tokens"] = cacheable_tokens
                sim_usage["cache_creation_input_tokens"] = 0
                status = "HIT"
            else:
                sim_usage["cache_read_input_tokens"] = 0
                sim_usage["cache_creation_input_tokens"] = cacheable_tokens
                status = "CREATED_OR_REBUILT"

            # Both creation and a hit/refresh extend the TTL.
            self._last_use[key] = sim_time
        else:
            status = "MISS_OR_NOT_USED"

        return {
            "cost_usd": estimate(model, sim_usage, cache_ttl),
            "cache_status": status,
            "usage": sim_usage,
            "prefix_hash": key,
            "previous_sim_use": (
                previous.isoformat() if previous is not None else None
            ),
            "sim_time": sim_time.isoformat(),
        }
