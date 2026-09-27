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

        events = data.setdefault(
            "events",
            {},
        )

        # Backward-compatible normalization.
        # Old schema-v1 journals did not contain
        # per-timeframe outcomes or an analysis plan.
        for event in events.values():
            event.setdefault(
                "decisions",
                {},
            )
            event.setdefault(
                "timeframe_results",
                {},
            )
            event.setdefault(
                "analysis_complete",
                False,
            )
            event.setdefault(
                "analysis_summary",
                None,
            )
            event.setdefault(
                "execution_started",
                False,
            )
            event.setdefault(
                "execution_done",
                False,
            )
            event.setdefault(
                "execution_results",
                None,
            )
            event.setdefault(
                "ack_done",
                False,
            )
            event.setdefault(
                "ack",
                None,
            )

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
                "timeframe_results": {},
                "analysis_complete": False,
                "analysis_summary": None,
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

    def record_timeframe_result(
        self,
        *,
        seq: int,
        sim_time: str,
        timeframe: str,
        status: str,
        called: bool,
        reason: str | None,
        evidence_fingerprint:
            str | None,
        watch_triggered: bool,
        force_rebase: bool,
        rows: int | None,
        recovered_decision: bool,
    ) -> dict:
        """
        Persist the completed outcome of one timeframe.

        This includes NO_CALL outcomes.

        Once durable, the same seq/timeframe is never
        re-evaluated against Market Memory that may have
        changed later in the same multi-timeframe event.
        """
        if status not in {
            "PROCESSED",
            "SKIPPED",
        }:
            raise ValueError(
                "Unsupported timeframe recovery "
                f"status: {status}"
            )

        self.ensure_event(
            seq,
            sim_time,
        )

        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        payload = {
            "timeframe":
                str(timeframe),
            "status":
                str(status),
            "called":
                bool(called),
            "reason":
                reason,
            "evidence_fingerprint": (
                str(evidence_fingerprint)
                if evidence_fingerprint
                is not None
                else None
            ),
            "watch_triggered":
                bool(watch_triggered),
            "force_rebase":
                bool(force_rebase),
            "rows": (
                int(rows)
                if rows is not None
                else None
            ),
            "recovered_decision":
                bool(recovered_decision),
        }

        results = event.setdefault(
            "timeframe_results",
            {},
        )

        existing = results.get(
            str(timeframe)
        )

        if existing is not None:
            if existing != payload:
                raise RuntimeError(
                    "Conflicting durable timeframe "
                    "result for "
                    f"seq={seq} "
                    f"timeframe={timeframe}"
                )

            return existing

        results[str(timeframe)] = (
            payload
        )

        self._save(data)

        self._log(
            "TIMEFRAME_RESULT_DURABLE",
            {
                "seq": int(seq),
                "sim_time":
                    str(sim_time),
                "timeframe":
                    str(timeframe),
                "status":
                    str(status),
                "called":
                    bool(called),
                "reason":
                    reason,
                "evidence_fingerprint":
                    evidence_fingerprint,
            },
        )

        return payload

    def get_timeframe_result(
        self,
        seq: int,
        timeframe: str,
    ) -> dict | None:
        event = self.get_event(seq)

        if event is None:
            return None

        return (
            event.get(
                "timeframe_results",
                {},
            )
            .get(str(timeframe))
        )

    def mark_analysis_complete(
        self,
        seq: int,
        *,
        closed_timeframes:
            list[str],
        claude_called: bool,
        actions: list[str],
        processed: list[dict],
        execution_plan:
            list[dict],
    ) -> dict:
        """
        Atomically seal the exact analysis result
        before execution starts.

        execution_plan contains EVERY Claude decision,
        including WAIT, because WAIT can cancel stale
        pending orders.
        """
        data = self.load()
        event = data["events"][
            self._key(seq)
        ]

        timeframe_results = (
            event.setdefault(
                "timeframe_results",
                {},
            )
        )

        for item in processed:
            timeframe = str(
                item["timeframe"]
            )

            if timeframe not in (
                timeframe_results
            ):
                raise RuntimeError(
                    "Cannot seal analysis before "
                    "timeframe result is durable: "
                    f"seq={seq} "
                    f"timeframe={timeframe}"
                )

        serialized_plan = []

        for item in execution_plan:
            timeframe = str(
                item["timeframe"]
            )

            decision = item[
                "decision"
            ]

            if isinstance(
                decision,
                ClaudeDecision,
            ):
                decision_payload = (
                    decision.model_dump(
                        mode="json"
                    )
                )

            elif isinstance(
                decision,
                dict,
            ):
                decision_payload = (
                    ClaudeDecision
                    .model_validate(
                        decision
                    )
                    .model_dump(
                        mode="json"
                    )
                )

            else:
                raise TypeError(
                    "Execution plan decision "
                    "must be ClaudeDecision "
                    "or dict"
                )

            serialized_plan.append(
                {
                    "timeframe":
                        timeframe,
                    "decision":
                        decision_payload,
                }
            )

        payload = {
            "closed_timeframes":
                sorted(
                    {
                        str(x)
                        for x
                        in closed_timeframes
                    }
                ),
            "claude_called":
                bool(claude_called),
            "actions":
                list(actions),
            "processed": [
                dict(x)
                for x in processed
            ],
            "execution_plan":
                serialized_plan,
        }

        if event.get(
            "analysis_complete"
        ):
            existing = event.get(
                "analysis_summary"
            )

            if existing != payload:
                raise RuntimeError(
                    "Conflicting immutable "
                    "analysis plan for "
                    f"seq={seq}"
                )

            return existing

        event[
            "analysis_summary"
        ] = payload

        event[
            "analysis_complete"
        ] = True

        self._save(data)

        self._log(
            "ANALYSIS_PLAN_DURABLE",
            {
                "seq": int(seq),
                "timeframe_count":
                    len(processed),
                "execution_decision_count":
                    len(serialized_plan),
                "claude_called":
                    bool(claude_called),
            },
        )

        return payload

    def get_analysis(
        self,
        seq: int,
    ) -> dict | None:
        event = self.get_event(seq)

        if event is None:
            return None

        if not event.get(
            "analysis_complete"
        ):
            return None

        summary = event.get(
            "analysis_summary"
        )

        if not isinstance(
            summary,
            dict,
        ):
            raise RuntimeError(
                "Recovery journal says "
                "analysis_complete but "
                "analysis_summary is missing: "
                f"seq={seq}"
            )

        return summary

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
