from __future__ import annotations
import json, uuid
from datetime import datetime, timezone
from pathlib import Path
from .models import MarketMemory
from .logging import AuditLogger
from .hashing import stable_hash

class MarketMemoryStore:
    def __init__(self, root: str|Path=".", logger: AuditLogger|None=None):
        self.root=Path(root).resolve(); self.state=self.root/"state"/"market_memory"
        self.current=self.state/"current.json"; self.archive=self.state/"archive"
        self.archive.mkdir(parents=True,exist_ok=True)
        self.logger=logger or AuditLogger(self.root)

    def load(self, symbol="XAUUSD") -> MarketMemory:
        if not self.current.exists():
            return MarketMemory(memory_id=str(uuid.uuid4()),symbol=symbol,updated_at=datetime.now(timezone.utc))
        return MarketMemory.model_validate_json(self.current.read_text(encoding="utf-8"))

    def save(self, memory: MarketMemory, reason: str) -> MarketMemory:
        before=self.current.read_text(encoding="utf-8") if self.current.exists() else ""
        memory.memory_version += 1
        memory.updated_at=datetime.now(timezone.utc)
        raw=memory.model_dump_json(indent=2)
        self.state.mkdir(parents=True,exist_ok=True)
        tmp=self.current.with_suffix(".tmp"); tmp.write_text(raw,encoding="utf-8"); tmp.replace(self.current)
        stamp=memory.updated_at.strftime("%Y%m%dT%H%M%S_%fZ")
        (self.archive/f"{stamp}_v{memory.memory_version:06d}.json").write_text(raw,encoding="utf-8")
        self.logger.event("memory","MEMORY_UPDATED",{"reason":reason,"memory_id":memory.memory_id,"version":memory.memory_version,"hash_before":stable_hash(before),"hash_after":stable_hash(raw)})
        return memory
