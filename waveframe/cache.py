from __future__ import annotations
from dataclasses import dataclass
from .models import MarketMemory

@dataclass
class CacheContext:
    stable_prefix: str
    source: str
    memory_version: int

class CacheManager:
    """Market Memory is source of truth. Anthropic cache is only an optimization."""
    def build_stable_prefix(self, strategy_rules: str, memory: MarketMemory) -> CacheContext:
        compact = memory.model_dump_json(exclude={"last_decision": {"rationale_brief", "evidence_interpretation"}})
        return CacheContext(stable_prefix=(strategy_rules.strip()+"\n\nVALIDATED MARKET MEMORY:\n"+compact), source="MARKET_MEMORY", memory_version=memory.memory_version)

    @staticmethod
    def cache_status(usage: dict) -> str:
        read=int(usage.get("cache_read_input_tokens",0) or 0)
        created=int(usage.get("cache_creation_input_tokens",0) or 0)
        if read>0: return "HIT"
        if created>0: return "CREATED_OR_REBUILT"
        return "MISS_OR_NOT_USED"
