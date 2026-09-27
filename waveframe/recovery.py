from __future__ import annotations

import json
from pathlib import Path

from .models import ClaudeDecision


class ReplayRecoveryJournal:
    """
    Durable per-replay recovery state.

    The file is written atomically using tmp -> replace.

    It is deliberately separate from Market Memory:
    Market Memory describes the market.
    Recovery Journal describes unfinished processing state.
    """

    SCHEMA_VERSION = 1

    def __init__(
        self,
        root: str | Path,
        logger=None,
    ):
        self.root = Path(root).resolve()
        self.path = (
            self.root
            / "state"
            / "replay_recovery.json"
        )
        self.path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.logger = logger

    def _empty(self) -> dict:
        return {
            "schema_version":
                self.SCHEMA_VERSION,
            "events": {},
        }

    def load(self) -> dict:
        if not self.path.exists():
            return self._empty()

        data = json.loads(
            self.path.read_text(
                encoding="utf-8"
            )
        )

        if int(
            data.get("schema_version") or 0
        ) != self.SCHEMA_VERSION:
            raise RuntimeError(
                "Unsupported replay recovery "
                "journal schema"
            )

        data.setdefault("events", {})

        return data

    def _save(self, data: dict) -> None:
        raw = json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

        tmp = self.path.with_suffix(
            ".tmp"
        )

        tmp.write_text(
            raw,
            encoding="utf-8",
        )

        tmp.replace(self.path)

    @staticmethod
    def _key(seq: int) -> str:
        return str(int(seq))

    def ensure_event(
        self,
        seq: int,
        sim_time: str,
    ) -> dict:
        data = self.load()

        key = self._key(seq)

        event = data["events"].get(key)

        if event is None:
            event = {
                "seq": int(seq),
                "sim_time": str(sim_time),
                "decisions": {},
                "execution_started": False,
                "execution_done": False,
                "execution_results": None,
                "ack_done": False,
                "ack": None,
            }

            data["events"][key] = event
            self._save(data)

            self._log(
                "RECOVERY_EVENT_CREATED",
                {
                    "seq": int(seq),
                    "sim_time": str(sim_time),
                },
            )

        else:
            if str(
                event.get("sim_time")
            ) != str(sim_time):
                raise RuntimeError(
                    "Recovery journal event "
                    f"{seq} sim_time mismatch: "
                    f"{event.get('sim_time')} "
                    f"!= {sim_time}"
                )

        return event

    def get_event(
        self,
        seq: int,
    ) -> dict | None:
        return (
            self.load()
            .get("events", {})
            .get(self._key(seq))
        )

    def record_decision(
        self,
        *,
        seq: int,
        sim_time: str,
        timeframe: str,
        evidence_fingerprint: str,
        decision: ClaudeDecision,
        api_called: bool,
    ) -> dict:
        self.ensure_event(
            seq,
            sim_time,
        )

        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        payload = {
            "timeframe": str(timeframe),
            "evidence_fingerprint":
                str(evidence_fingerprint),
            "api_called": bool(api_called),
            "decision":
                decision.model_dump(
                    mode="json"
                ),
            "memory_committed": False,
        }

        existing = (
            event["decisions"]
            .get(str(timeframe))
        )

        if existing is not None:
            # Idempotent replay of the exact
            # same durable decision is allowed.
            if (
                existing.get(
                    "evidence_fingerprint"
                )
                != payload[
                    "evidence_fingerprint"
                ]
                or existing.get("decision")
                != payload["decision"]
            ):
                raise RuntimeError(
                    "Conflicting durable decision "
                    f"for seq={seq} "
                    f"timeframe={timeframe}"
                )

            return existing

        event["decisions"][
            str(timeframe)
        ] = payload

        self._save(data)

        self._log(
            "VALID_DECISION_DURABLE",
            {
                "seq": int(seq),
                "sim_time": str(sim_time),
                "timeframe": str(timeframe),
                "evidence_fingerprint":
                    str(evidence_fingerprint),
                "api_called":
                    bool(api_called),
                "action":
                    decision.action,
            },
        )

        return payload

    def get_decision(
        self,
        seq: int,
        timeframe: str,
    ) -> dict | None:
        event = self.get_event(seq)

        if event is None:
            return None

        return (
            event.get(
                "decisions",
                {},
            )
            .get(str(timeframe))
        )

    def mark_memory_committed(
        self,
        seq: int,
        timeframe: str,
    ) -> None:
        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        item = event["decisions"][
            str(timeframe)
        ]

        if item.get(
            "memory_committed"
        ):
            return

        item["memory_committed"] = True
        self._save(data)

        self._log(
            "RECOVERY_MEMORY_COMMITTED",
            {
                "seq": int(seq),
                "timeframe": str(timeframe),
            },
        )

    def mark_execution_started(
        self,
        seq: int,
    ) -> None:
        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        if event.get(
            "execution_started"
        ):
            return

        event["execution_started"] = True
        self._save(data)

        self._log(
            "RECOVERY_EXECUTION_STARTED",
            {"seq": int(seq)},
        )

    def mark_execution_done(
        self,
        seq: int,
        results: list[dict],
    ) -> None:
        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        event["execution_started"] = True
        event["execution_done"] = True
        event["execution_results"] = results

        self._save(data)

        self._log(
            "RECOVERY_EXECUTION_DONE",
            {
                "seq": int(seq),
                "result_count":
                    len(results),
            },
        )

    def mark_ack_done(
        self,
        seq: int,
        ack: dict,
    ) -> None:
        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        event["ack_done"] = True
        event["ack"] = ack

        self._save(data)

        self._log(
            "RECOVERY_ACK_DONE",
            {"seq": int(seq)},
        )

    def _log(
        self,
        event: str,
        data: dict,
    ) -> None:
        if self.logger is not None:
            self.logger.event(
                "recovery",
                event,
                data,
            )
