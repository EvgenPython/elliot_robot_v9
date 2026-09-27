from __future__ import annotations
from datetime import datetime, timezone
from .logging import AuditLogger
from .memory import MarketMemoryStore
from .cache import CacheManager
from .prompts import SYSTEM_RULES
from .delta import compact_delta
from .models import TimeframeMemory
from .policy import AnalysisPolicy
from .decision import claude_trade_intent

class Orchestrator:
    def __init__(self, root, claude_gateway):
        self.root=root; self.log=AuditLogger(root); self.memory_store=MarketMemoryStore(root,self.log)
        self.cache=CacheManager(); self.policy=AnalysisPolicy(self.log); self.claude=claude_gateway

    def process_evidence(self, pack, watch_triggered=False, force_rebase=False):
        memory=self.memory_store.load(pack.symbol)
        self.log.snapshot("evidence", f"{pack.timeframe}_{pack.last_closed_bar.strftime('%Y%m%dT%H%M%S')}", pack.model_dump(mode="json"))
        if not self.policy.on_closed_bar(pack,memory,watch_triggered,force_rebase):
            return {"called":False,"reason":"NO_MATERIAL_DELTA"}
        prefix=self.cache.build_stable_prefix(SYSTEM_RULES,memory)
        delta=compact_delta(pack,memory)
        self.log.event("cache","CACHE_CONTEXT_PREPARED",{"source":prefix.source,"memory_version":prefix.memory_version,"timeframe":pack.timeframe})
        decision=self.claude.ask_until_valid(prefix.stable_prefix,delta)
        memory.timeframes[pack.timeframe]=TimeframeMemory(timeframe=pack.timeframe,evidence_fingerprint=pack.structure.fingerprint,last_closed_bar=pack.last_closed_bar,elliott=decision.primary_count,alternate=decision.alternate_count,claude_context={"structure":decision.structure_assessment,"support_resistance":decision.support_resistance_assessment,"channel":decision.channel_assessment,"patterns":decision.pattern_assessment,"disagreements":decision.evidence_disagreements})
        memory.last_decision=decision; memory.active_watches=decision.watch_conditions
        if force_rebase: memory.last_rebase_at=datetime.now(timezone.utc)
        self.memory_store.save(memory,reason="CLAUDE_VALIDATED_UPDATE")
        intent=claude_trade_intent(decision)
        self.log.event("trade_funnel","CLAUDE_TRADE_INTENT",{"action":decision.action,"intent":intent,"entry":decision.entry,"stop":decision.stop,"target":decision.target})
        return {"called":True,"decision":decision,"trade_intent":intent}
