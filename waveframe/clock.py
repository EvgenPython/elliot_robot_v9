from __future__ import annotations
from datetime import datetime, timezone
from threading import RLock


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class RealClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class ReplayClock:
    """Mutable market clock controlled only by simulator events."""

    def __init__(self, initial: datetime | None = None):
        self._lock = RLock()
        self._now = _utc(initial) if initial is not None else None

    def set(self, value: datetime | str) -> datetime:
        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        value = _utc(value)
        with self._lock:
            self._now = value
        return value

    def now(self) -> datetime:
        with self._lock:
            if self._now is None:
                raise RuntimeError("ReplayClock is not initialized")
            return self._now
