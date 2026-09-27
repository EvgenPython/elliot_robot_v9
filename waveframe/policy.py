from __future__ import annotations
from .models import EvidencePack, MarketMemory
from .delta import evidence_changed
from .logging import AuditLogger

class AnalysisPolicy:
    """Decides whether new INFORMATION exists, never whether budget allows a call."""
    def __init__(self, logger:AuditLogger): self.logger=logger

    def on_closed_bar(self, pack:EvidencePack, memory:MarketMemory, watch_triggered:bool=False, force_rebase:bool=False) -> bool:
        changed=evidence_changed(pack,memory)
        call=bool(force_rebase or watch_triggered or changed)
        reason="FORCE_REBASE" if force_rebase else "WATCH_TRIGGERED" if watch_triggered else "EVIDENCE_CHANGED" if changed else "NO_MATERIAL_DELTA"
        self.logger.event("decisions","CLAUDE_CALL_DECISION",{"timeframe":pack.timeframe,"bar":pack.last_closed_bar.isoformat(),"called":call,"reason":reason,"structure_fingerprint":pack.structure.fingerprint})
        return call
