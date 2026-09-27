from __future__ import annotations
import json, zipfile
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

SECRET_KEYS = {"api_key", "password", "token", "secret", "login"}


def _wall_now() -> datetime:
    return datetime.now(timezone.utc)


def redact(value: Any):
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if any(s in str(k).lower() for s in SECRET_KEYS) else redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(x) for x in value]
    return value


class AuditLogger:
    def __init__(self, root: str | Path = ".", clock=None):
        self.root = Path(root).resolve()
        self.logs = self.root / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._snapshot_lock = Lock()
        self._snapshot_seq = 0

    def _market_now(self) -> datetime:
        return self.clock.now() if self.clock is not None else _wall_now()

    def _next_snapshot_seq(self) -> int:
        with self._snapshot_lock:
            self._snapshot_seq += 1
            return self._snapshot_seq

    def event(self, category: str, event: str, data: dict | None = None, **meta):
        wall = _wall_now()
        market = self._market_now()

        path = self.logs / category / f"{market.date().isoformat()}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)

        record = {
            "at_utc": market.isoformat(),
            "wall_at_utc": wall.isoformat(),
            "market_at": market.isoformat(),
            "category": category,
            "event": event,
            **meta,
            "data": redact(data or {}),
        }

        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

        return record

    def snapshot(self, category: str, name: str, payload: dict):
        market = self._market_now()
        seq = self._next_snapshot_seq()

        stamp = market.strftime("%Y%m%dT%H%M%S_%fZ")
        path = self.logs / category / f"{stamp}_{seq:06d}_{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)

        path.write_text(
            json.dumps(redact(payload), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def claude_request(self, cycle_id: str, payload: dict):
        return self.snapshot("claude/requests", cycle_id, payload)

    def claude_response(self, cycle_id: str, payload: dict):
        return self.snapshot("claude/responses", cycle_id, payload)

    def claude_parsed(self, cycle_id: str, payload: dict):
        return self.snapshot("claude/parsed", cycle_id, payload)


def export_audit(root: str | Path, output: str | Path, days: int = 7):
    root = Path(root).resolve()
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        for folder in ["logs", "state", "diagnostics"]:
            base = root / folder
            if not base.exists():
                continue
            for p in base.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(root))

    return output
