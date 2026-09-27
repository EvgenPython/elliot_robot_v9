from __future__ import annotations
from .models import EvidencePack, MarketMemory
from .hashing import stable_hash


def _event_key(value):
    if not value:
        return None
    return {
        "direction": value.get("direction"),
        "level": round(float(value["level"]), 2) if value.get("level") is not None else None,
    }


def material_fingerprint(pack: EvidencePack) -> str:
    """Only decision-relevant structural change.

    A fresh candle by itself is not a paid-analysis trigger. Numeric trend-line
    drift remains available in the EvidencePack and will be sent whenever a real
    structural/watch/rebase event causes Claude to be called.
    """
    signature = {
        "structure": pack.structure.fingerprint,
        "bos": _event_key(pack.smc.last_bos),
        "choch": _event_key(pack.smc.last_choch),
        "liquidity": [
            (x.get("side"), round(float(x.get("level")), 1))
            for x in pack.smc.liquidity
            if x.get("level") is not None
        ],
        "channel_type": pack.geometry.channel_type,
        "pattern_hints": sorted(pack.geometry.pattern_hints),
    }
    return stable_hash(signature)


def evidence_changed(pack: EvidencePack, memory: MarketMemory) -> bool:
    old = memory.timeframes.get(pack.timeframe)
    return old is None or old.evidence_fingerprint != material_fingerprint(pack)


def compact_delta(pack: EvidencePack, memory: MarketMemory) -> dict:
    old = memory.timeframes.get(pack.timeframe)
    new_fp = material_fingerprint(pack)
    return {
        "timeframe": pack.timeframe,
        "last_closed_bar": pack.last_closed_bar.isoformat(),
        "previous_fingerprint": old.evidence_fingerprint if old else None,
        "new_fingerprint": new_fp,
        "structure": pack.structure.model_dump(mode="json"),
        "smc": pack.smc.model_dump(mode="json"),
        "trend": pack.trend.model_dump(mode="json"),
        "geometry": pack.geometry.model_dump(mode="json"),
        "recent_ohlc": pack.recent_ohlc,
        "advisory_note": pack.advisory_note,
    }
