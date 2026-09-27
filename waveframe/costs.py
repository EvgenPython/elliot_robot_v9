from __future__ import annotations
# Default Sonnet 5 rates observed/documented for this project date.
# Keep pricing data isolated so it can be updated without touching decision logic.
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
    rate=next((v for k,v in RATES.items() if model==k or model.startswith(k+"-")),None)
    if not rate: return 0.0
    vals={k:int(usage.get(k,0) or 0) for k in ["input_tokens","output_tokens","cache_read_input_tokens","cache_creation_input_tokens"]}
    write=rate["cache_write_1h"] if cache_ttl=="1h" else rate["cache_write_5m"]
    usd=(vals["input_tokens"]*rate["input"]+vals["output_tokens"]*rate["output"]+vals["cache_read_input_tokens"]*rate["cache_read"]+vals["cache_creation_input_tokens"]*write)/1_000_000
    return round(usd,8)
