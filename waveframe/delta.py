from __future__ import annotations
from .models import EvidencePack, MarketMemory


def evidence_changed(pack: EvidencePack, memory: MarketMemory) -> bool:
    old=memory.timeframes.get(pack.timeframe)
    return old is None or old.evidence_fingerprint != pack.structure.fingerprint or old.last_closed_bar != pack.last_closed_bar


def compact_delta(pack: EvidencePack, memory: MarketMemory) -> dict:
    old=memory.timeframes.get(pack.timeframe)
    return {
        "timeframe": pack.timeframe,
        "last_closed_bar": pack.last_closed_bar.isoformat(),
        "previous_fingerprint": old.evidence_fingerprint if old else None,
        "new_fingerprint": pack.structure.fingerprint,
        "structure": pack.structure.model_dump(mode="json"),
        "smc": pack.smc.model_dump(mode="json"),
        "trend": pack.trend.model_dump(mode="json"),
        "geometry": pack.geometry.model_dump(mode="json"),
        "recent_ohlc": pack.recent_ohlc,
        "advisory_note": pack.advisory_note,
    }
