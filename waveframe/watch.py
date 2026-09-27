from __future__ import annotations
from .models import WatchCondition

def evaluate_watch(w: WatchCondition, bar: dict) -> bool:
    c=float(bar["close"]); h=float(bar["high"]); l=float(bar["low"])
    return {
        "close_above": c > w.level,
        "close_below": c < w.level,
        "touch_above": h >= w.level,
        "touch_below": l <= w.level,
    }[w.kind]
