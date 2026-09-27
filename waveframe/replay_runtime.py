from __future__ import annotations

import json
import time
from datetime import timedelta, timezone
from pathlib import Path

from .config import load_settings
from .delta import material_fingerprint
from .evidence import build_evidence
from .logging import AuditLogger
from .orchestrator import Orchestrator
from .replay_execution import ReplayExecutor
from .recovery import ReplayRecoveryJournal
from .models import ClaudeDecision
from .watch import evaluate_watch


ANALYSIS_ORDER = [
    "H4",
    "H1",
    "M30",
    "M15",
]

FP_TZ = timezone(
    timedelta(hours=3)
)


class ReplayRuntime:

    def __init__(
        self,
        project_root,
        run_root,
        simulator,
        clock,
        claude_gateway,
    ):
        self.project_root = Path(
            project_root
        ).resolve()

        self.run_root = Path(
            run_root
        ).resolve()

        self.run_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.settings = load_settings(
            self.project_root
        )

        self.simulator = simulator
        self.clock = clock

        self.logger = AuditLogger(
            self.run_root,
            clock=self.clock,
        )

        if hasattr(
            claude_gateway,
            "logger",
        ):
            claude_gateway.logger = self.logger

        self.orchestrator = Orchestrator(
            self.run_root,
            claude_gateway,
            clock=self.clock,
        )

        self.symbol = str(
            self.settings.get("symbol")
            or "XAUUSD"
        )

        structure = self.settings.get(
            "structure",
            {},
        )

        self.history_bars = int(
            structure.get(
                "history_bars",
                300,
            )
        )

        self.left = int(
            structure.get(
                "pivot_left",
                3,
            )
        )

        self.right = int(
            structure.get(
                "pivot_right",
                3,
            )
        )

        self.recent = int(
            structure.get(
                "recent_ohlc_for_claude",
                24,
            )
        )

        self.rebase_hour_fp = int(
            self.settings.get(
                "claude",
                {},
            ).get(
                "daily_rebase_hour_fp",
                8,
            )
        )

        self.executor = ReplayExecutor(
            simulator=self.simulator,
            logger=self.logger,
            symbol=self.symbol,
            settings=self.settings,
        )

        self.recovery = ReplayRecoveryJournal(
            self.run_root,
            logger=self.logger,
        )

    def _memory_audit(self) -> dict:
        memory = (
            self.orchestrator
            .memory_store
            .load(self.symbol)
        )

        return {
            "memory_version": memory.memory_version,
            "updated_at": (
                memory.updated_at.isoformat()
                if memory.updated_at
                else None
            ),
            "last_decision": (
                memory.last_decision.action
                if memory.last_decision
                else None
            ),
            "active_watches": len(
                memory.active_watches
            ),
            "timeframes": {
                tf: {
                    "evidence_fingerprint":
                        state.evidence_fingerprint,
                    "last_closed_bar": (
                        state.last_closed_bar.isoformat()
                        if state.last_closed_bar is not None
                        else None
                    ),
                }
                for tf, state
                in memory.timeframes.items()
            },
        }

    def _watch_triggered(
        self,
        timeframe: str,
        latest_bar: dict,
    ) -> bool:

        memory = (
            self.orchestrator
            .memory_store
            .load(self.symbol)
        )

        hits = []

        for watch in memory.active_watches:
            if (
                watch.timeframe == timeframe
                and evaluate_watch(
                    watch,
                    latest_bar,
                )
            ):
                hits.append(
                    watch.model_dump(
                        mode="json"
                    )
                )

        if hits:
            self.logger.event(
                "decisions",
                "WATCH_TRIGGERED",
                {
                    "timeframe": timeframe,
                    "bar": latest_bar,
                    "watches": hits,
                },
            )

        return bool(hits)

    def _force_rebase(
        self,
        timeframe: str,
    ) -> bool:

        if timeframe != "H1":
            return False

        now_fp = (
            self.clock
            .now()
            .astimezone(FP_TZ)
        )

        if (
            now_fp.hour
            != self.rebase_hour_fp
            or now_fp.minute != 0
        ):
            return False

        memory = (
            self.orchestrator
            .memory_store
            .load(self.symbol)
        )

        return (
            memory.last_rebase_at is None
            or (
                memory.last_rebase_at
                .astimezone(FP_TZ)
                .date()
                != now_fp.date()
            )
        )

    def _process_pack_with_recovery(
        self,
        *,
        seq: int,
        sim_time: str,
        timeframe: str,
        pack,
        fingerprint: str,
        watch_triggered: bool,
        force_rebase: bool,
    ):
        """
        Return (orchestrator_result, recovered_decision).

        If this exact seq/timeframe decision is already durable,
        Claude is NOT called again.
        """
        durable = self.recovery.get_decision(
            seq,
            timeframe,
        )

        if durable is not None:
            durable_fp = str(
                durable.get(
                    "evidence_fingerprint"
                )
            )

            if durable_fp != str(
                fingerprint
            ):
                raise RuntimeError(
                    "Recovery evidence fingerprint "
                    "mismatch for "
                    f"seq={seq} "
                    f"timeframe={timeframe}: "
                    f"{durable_fp} != {fingerprint}"
                )

            decision = (
                ClaudeDecision.model_validate(
                    durable["decision"]
                )
            )

            if durable.get(
                "memory_committed"
            ):
                result = (
                    self.orchestrator
                    .result_from_decision(
                        decision,
                        reason=
                            "RECOVERY_DURABLE_DECISION",
                        recovered=True,
                    )
                )

            else:
                result = (
                    self.orchestrator
                    .commit_validated_decision(
                        pack,
                        decision,
                        force_rebase=
                            force_rebase,
                        result_reason=
                            "RECOVERY_DURABLE_DECISION",
                        save_reason=
                            "RECOVERY_DURABLE_DECISION",
                        recovered=True,
                    )
                )

                self.recovery.mark_memory_committed(
                    seq,
                    timeframe,
                )

            self.logger.event(
                "recovery",
                "DURABLE_DECISION_REUSED",
                {
                    "seq": int(seq),
                    "sim_time":
                        str(sim_time),
                    "timeframe":
                        str(timeframe),
                    "evidence_fingerprint":
                        str(fingerprint),
                    "action":
                        decision.action,
                    "api_called_originally":
                        bool(
                            durable.get(
                                "api_called"
                            )
                        ),
                },
            )

            return result, True

        api_called = bool(
            getattr(
                self.orchestrator.claude,
                "api_called",
                True,
            )
        )

        def persist_valid_decision(
            decision,
        ):
            self.recovery.record_decision(
                seq=seq,
                sim_time=sim_time,
                timeframe=timeframe,
                evidence_fingerprint=
                    fingerprint,
                decision=decision,
                api_called=api_called,
            )

        result = (
            self.orchestrator
            .process_evidence(
                pack,
                watch_triggered=
                    watch_triggered,
                force_rebase=
                    force_rebase,
                on_valid_decision=
                    persist_valid_decision,
            )
        )

        if result.get("called"):
            # process_evidence() only returns after
            # commit_validated_decision() completed.
            self.recovery.mark_memory_committed(
                seq,
                timeframe,
            )

        return result, False

    def _execute_with_recovery(
        self,
        *,
        seq: int,
        execution_decisions: list[dict],
    ):
        """
        Crash-safe execution protocol.

        execution_done=True:
            never call executor again.

        execution_started=True but done=False:
            replay the SAME durable decisions through ReplayExecutor.

        ReplayExecutor is intentionally convergence-safe:
        - identical pending -> PENDING_UNCHANGED;
        - existing position -> no duplicate exposure;
        - already-cancelled WAIT -> no-op;
        - ambiguous order_send -> broker-state reconciliation.
        """
        state = self.recovery.get_event(
            seq
        )

        if state is None:
            raise RuntimeError(
                "Recovery event missing before execution: "
                f"seq={seq}"
            )

        if state.get(
            "execution_done"
        ):
            results = (
                state.get(
                    "execution_results"
                )
                or []
            )

            self.logger.event(
                "recovery",
                "EXECUTION_RESULT_REUSED",
                {
                    "seq": int(seq),
                    "result_count":
                        len(results),
                },
            )

            return results, "REUSED_DONE"

        resumed = bool(
            state.get(
                "execution_started"
            )
        )

        if resumed:
            self.logger.event(
                "recovery",
                "EXECUTION_CONVERGENCE_RESUME",
                {
                    "seq": int(seq),
                    "decision_count":
                        len(execution_decisions),
                },
            )

        else:
            self.recovery.mark_execution_started(
                seq
            )

        # Important:
        # if the previous process died INSIDE this call,
        # execution_started remains true but execution_done
        # remains false. On restart we converge through the
        # executor rather than assuming success or re-sending blindly.
        results = (
            self.executor
            .execute_decisions(
                execution_decisions
            )
        )

        self.recovery.mark_execution_done(
            seq,
            results,
        )

        return (
            results,
            (
                "RESUMED_INCOMPLETE"
                if resumed
                else "FRESH"
            ),
        )

    @staticmethod
    def _status_proves_ack(
        status: dict,
        seq: int,
    ) -> bool:
        """
        With require_ack=True the simulator cannot clear pending_event
        and advance past an event unless ACK was accepted.
        """
        simulator_seq = int(
            status.get("seq") or 0
        )

        pending = status.get(
            "pending_event"
        )

        # ACK for seq is proven when:
        #
        # 1. simulator already advanced beyond seq, regardless of
        #    whether the NEXT event is currently WAITING_ACK;
        #
        # 2. simulator is still at seq but pending_event disappeared.
        #
        # This closes the race where ACK succeeds and the worker
        # advances to seq+1 before our reconciliation status() call.
        if simulator_seq > int(seq):
            return True

        if (
            simulator_seq == int(seq)
            and pending is None
        ):
            return True

        return False

    @staticmethod
    def _same_pending_seq(
        status: dict,
        seq: int,
    ) -> bool:
        pending = status.get(
            "pending_event"
        )

        if not pending:
            return False

        return (
            int(
                pending.get("seq") or -1
            )
            == int(seq)
        )

    def _ensure_simulator_running_after_ack(
        self,
        seq: int,
    ) -> None:
        """
        A restored replay can be WAITING_ACK with no engine thread.

        ACK changes simulator state to RUNNING, but does not itself
        create the worker thread. Calling start() while RUNNING is
        safe: ReplayEngine only creates a thread when none is alive.

        FINISHED is also valid if the final event completed before
        this check.
        """
        status = self.simulator.status()

        current = str(
            status.get("status") or ""
        )

        if current == "FINISHED":
            return

        # The already-running worker may advance to the next event
        # before this check. WAITING_ACK therefore also proves that
        # the worker exists and made forward progress.
        if current == "WAITING_ACK":
            self.logger.event(
                "recovery",
                "SIMULATOR_ALREADY_ADVANCED",
                {
                    "seq": int(seq),
                    "current_seq":
                        int(
                            status.get("seq")
                            or 0
                        ),
                },
            )
            return

        if current != "RUNNING":
            raise RuntimeError(
                "Simulator did not enter RUNNING "
                "after ACK: "
                f"seq={seq} status={status}"
            )

        try:
            self.simulator.start()

        except Exception as error:
            # Avoid a false failure if the replay reached FINISHED
            # between status() and start().
            status_after = (
                self.simulator.status()
            )

            if (
                status_after.get("status")
                == "FINISHED"
            ):
                return

            raise RuntimeError(
                "ACK succeeded but simulator worker "
                "could not be ensured running: "
                f"seq={seq} status={status_after}"
            ) from error

        self.logger.event(
            "recovery",
            "SIMULATOR_WORKER_ENSURED",
            {
                "seq": int(seq),
            },
        )

    def _ack_with_recovery(
        self,
        *,
        seq: int,
        claude_called: bool,
        decision_text: str | None,
    ):
        """
        ACK is also treated as an idempotent state transition.

        If its HTTP response is lost:
        1. read simulator state;
        2. if pending_event disappeared, ACK succeeded;
        3. if the exact same event is still pending, retry ACK once;
        4. otherwise fail closed.

        Never loop indefinitely.
        """
        state = self.recovery.get_event(
            seq
        )

        if state is None:
            raise RuntimeError(
                "Recovery event missing before ACK: "
                f"seq={seq}"
            )

        if state.get("ack_done"):
            status = self.simulator.status()

            if self._same_pending_seq(
                status,
                seq,
            ):
                raise RuntimeError(
                    "Recovery journal says ACK_DONE "
                    "but simulator still waits for "
                    f"seq={seq}"
                )

            ack = (
                state.get("ack")
                or {
                    "ok": True,
                    "recovered": True,
                }
            )

            self.logger.event(
                "recovery",
                "ACK_RESULT_REUSED",
                {
                    "seq": int(seq),
                },
            )

            return ack, "REUSED_DONE"

        def send_ack():
            return self.simulator.ack(
                seq,
                claude_called=
                    claude_called,
                cycle_id=f"replay-{seq}",
                decision=decision_text,
            )

        try:
            ack = send_ack()

        except Exception as first_error:
            try:
                status = (
                    self.simulator.status()
                )

            except Exception as status_error:
                raise RuntimeError(
                    "ACK response failed and simulator "
                    "state cannot be reconciled"
                ) from status_error

            # ACK reached simulator, but HTTP response
            # was lost.
            if self._status_proves_ack(
                status,
                seq,
            ):
                ack = {
                    "ok": True,
                    "reconciled": True,
                    "reason":
                        "ACK_RESPONSE_LOST",
                    "simulator_seq":
                        int(
                            status.get(
                                "seq"
                            )
                            or 0
                        ),
                    "original_error":
                        repr(first_error),
                }

                self.logger.event(
                    "recovery",
                    "ACK_RECONCILED_AFTER_RESPONSE_LOSS",
                    {
                        "seq": int(seq),
                    },
                )

            # Exact same event is provably still
            # awaiting ACK. One retry is safe.
            elif self._same_pending_seq(
                status,
                seq,
            ):
                self.logger.event(
                    "recovery",
                    "ACK_RETRY_AFTER_STATE_CHECK",
                    {
                        "seq": int(seq),
                        "first_error":
                            repr(
                                first_error
                            ),
                    },
                )

                try:
                    ack = send_ack()

                    ack = dict(
                        ack or {}
                    )

                    ack[
                        "retried_after_state_check"
                    ] = True

                except Exception as second_error:
                    try:
                        status2 = (
                            self.simulator
                            .status()
                        )

                    except Exception as status2_error:
                        raise RuntimeError(
                            "ACK retry also failed and "
                            "simulator state cannot be "
                            "reconciled"
                        ) from status2_error

                    # Second ACK may also have succeeded
                    # while its response was lost.
                    if self._status_proves_ack(
                        status2,
                        seq,
                    ):
                        ack = {
                            "ok": True,
                            "reconciled": True,
                            "reason":
                                "ACK_RETRY_RESPONSE_LOST",
                            "simulator_seq":
                                int(
                                    status2.get(
                                        "seq"
                                    )
                                    or 0
                                ),
                            "first_error":
                                repr(
                                    first_error
                                ),
                            "second_error":
                                repr(
                                    second_error
                                ),
                        }

                    else:
                        raise RuntimeError(
                            "ACK remains unresolved after "
                            "one state-checked retry"
                        ) from second_error

            else:
                raise RuntimeError(
                    "ACK failed and simulator state "
                    "does not prove either acceptance "
                    "or safe retry"
                ) from first_error

        self.recovery.mark_ack_done(
            seq,
            ack,
        )

        self._ensure_simulator_running_after_ack(
            seq
        )

        return ack, "FRESH_OR_RECONCILED"

    def _reconcile_orphaned_acks(
        self,
    ) -> int:
        """
        Handles the narrow crash window:

            simulator accepted ACK + persisted state
            -> Python died
            -> journal did not yet write ack_done

        On restart an older journal event can be proven ACKed if:
        - simulator seq has advanced beyond it, or
        - simulator is at that seq and no pending_event remains.
        """
        data = self.recovery.load()

        events = data.get(
            "events",
            {},
        )

        if not events:
            return 0

        status = self.simulator.status()

        simulator_seq = int(
            status.get("seq") or 0
        )

        pending = status.get(
            "pending_event"
        )

        pending_seq = (
            int(
                pending.get("seq")
                or -1
            )
            if pending
            else None
        )

        reconciled = 0

        for raw_seq, event in events.items():
            seq = int(raw_seq)

            if not event.get(
                "execution_done"
            ):
                continue

            if event.get(
                "ack_done"
            ):
                continue

            proven = (
                seq < simulator_seq
                or (
                    seq == simulator_seq
                    and pending_seq is None
                )
            )

            if not proven:
                continue

            ack = {
                "ok": True,
                "reconciled_on_startup":
                    True,
                "reason":
                    "SIMULATOR_STATE_PROVES_PRIOR_ACK",
                "simulator_seq":
                    simulator_seq,
            }

            self.recovery.mark_ack_done(
                seq,
                ack,
            )

            reconciled += 1

            self.logger.event(
                "recovery",
                "ORPHANED_ACK_RECONCILED",
                {
                    "seq": seq,
                    "simulator_seq":
                        simulator_seq,
                },
            )

        return reconciled

    def process_event(
        self,
        event: dict,
    ) -> dict:

        self.clock.set(
            event["sim_time"]
        )

        seq = int(
            event["seq"]
        )

        closed = set(
            event.get(
                "closed_timeframes"
            )
            or []
        )

        self.logger.event(
            "replay",
            "EVENT_RECEIVED",
            {
                "seq": seq,
                "sim_time":
                    event["sim_time"],
                "closed_timeframes":
                    sorted(closed),
            },
        )

        memory_before = (
            self._memory_audit()
        )

        self.recovery.ensure_event(
            seq,
            event["sim_time"],
        )

        self.simulator.analysis_started(
            seq,
            cycle_id=f"replay-{seq}",
        )

        # ====================================================
        # Event-level durable analysis plan
        # ====================================================

        durable_analysis = (
            self.recovery
            .get_analysis(seq)
        )

        if durable_analysis is not None:

            durable_closed = set(
                durable_analysis.get(
                    "closed_timeframes"
                )
                or []
            )

            if durable_closed != closed:
                raise RuntimeError(
                    "Recovery closed timeframe "
                    "mismatch for "
                    f"seq={seq}: "
                    f"{sorted(durable_closed)} "
                    f"!= {sorted(closed)}"
                )

            claude_called = bool(
                durable_analysis.get(
                    "claude_called"
                )
            )

            actions = list(
                durable_analysis.get(
                    "actions"
                )
                or []
            )

            processed = [
                dict(x)
                for x
                in (
                    durable_analysis.get(
                        "processed"
                    )
                    or []
                )
            ]

            execution_decisions = []

            for item in (
                durable_analysis.get(
                    "execution_plan"
                )
                or []
            ):
                execution_decisions.append(
                    {
                        "timeframe":
                            str(
                                item[
                                    "timeframe"
                                ]
                            ),
                        "decision":
                            ClaudeDecision
                            .model_validate(
                                item[
                                    "decision"
                                ]
                            ),
                    }
                )

            trade_candidates = [
                item
                for item
                in execution_decisions
                if (
                    item["decision"].action
                    in {
                        "READY_LONG",
                        "READY_SHORT",
                    }
                )
            ]

            analysis_recovery_mode = (
                "REUSED_COMPLETE"
            )

            self.logger.event(
                "recovery",
                "ANALYSIS_PLAN_REUSED",
                {
                    "seq": int(seq),
                    "processed_count":
                        len(processed),
                    "execution_decision_count":
                        len(
                            execution_decisions
                        ),
                },
            )

        else:

            claude_called = False
            actions = []
            processed = []
            trade_candidates = []
            execution_decisions = []

            reused_timeframes = 0

            # ================================================
            # Per-timeframe transaction
            # ================================================

            for tf in ANALYSIS_ORDER:

                if tf not in closed:
                    continue

                durable_result = (
                    self.recovery
                    .get_timeframe_result(
                        seq,
                        tf,
                    )
                )

                # --------------------------------------------
                # Already completed timeframe.
                #
                # Do NOT recompute watch conditions,
                # Market Memory policy, evidence or Claude.
                # --------------------------------------------

                if durable_result is not None:

                    status = str(
                        durable_result.get(
                            "status"
                        )
                        or ""
                    )

                    if status == "SKIPPED":

                        if durable_result.get(
                            "called"
                        ):
                            raise RuntimeError(
                                "SKIPPED durable "
                                "timeframe cannot have "
                                "called=True: "
                                f"seq={seq} tf={tf}"
                            )

                        item = {
                            "timeframe": tf,
                            "called": False,
                            "reason":
                                durable_result.get(
                                    "reason"
                                ),
                            "evidence_fingerprint":
                                None,
                            "watch_triggered":
                                bool(
                                    durable_result
                                    .get(
                                        "watch_triggered"
                                    )
                                ),
                            "force_rebase":
                                bool(
                                    durable_result
                                    .get(
                                        "force_rebase"
                                    )
                                ),
                            "rows":
                                durable_result.get(
                                    "rows"
                                ),
                            "recovered_decision":
                                False,
                            "recovered_timeframe_result":
                                True,
                        }

                        processed.append(
                            item
                        )

                        reused_timeframes += 1

                        self.logger.event(
                            "recovery",
                            "TIMEFRAME_RESULT_REUSED",
                            {
                                "seq": int(seq),
                                "timeframe": tf,
                                "status":
                                    "SKIPPED",
                            },
                        )

                        continue

                    if status != "PROCESSED":
                        raise RuntimeError(
                            "Unsupported durable "
                            "timeframe status: "
                            f"seq={seq} "
                            f"tf={tf} "
                            f"status={status}"
                        )

                    called = bool(
                        durable_result.get(
                            "called"
                        )
                    )

                    decision = None

                    durable_decision = (
                        self.recovery
                        .get_decision(
                            seq,
                            tf,
                        )
                    )

                    if called:

                        if durable_decision is None:
                            raise RuntimeError(
                                "Durable timeframe "
                                "result says called=True "
                                "but decision is missing: "
                                f"seq={seq} tf={tf}"
                            )

                        result_fp = (
                            durable_result.get(
                                "evidence_fingerprint"
                            )
                        )

                        decision_fp = (
                            durable_decision.get(
                                "evidence_fingerprint"
                            )
                        )

                        if str(
                            result_fp
                        ) != str(
                            decision_fp
                        ):
                            raise RuntimeError(
                                "Durable timeframe "
                                "fingerprint conflicts "
                                "with durable decision: "
                                f"seq={seq} tf={tf}"
                            )

                        if not durable_decision.get(
                            "memory_committed"
                        ):
                            raise RuntimeError(
                                "Durable timeframe "
                                "result exists before "
                                "memory commit: "
                                f"seq={seq} tf={tf}"
                            )

                        decision = (
                            ClaudeDecision
                            .model_validate(
                                durable_decision[
                                    "decision"
                                ]
                            )
                        )

                        result = (
                            self.orchestrator
                            .result_from_decision(
                                decision,
                                reason=(
                                    "RECOVERY_DURABLE_"
                                    "TIMEFRAME_RESULT"
                                ),
                                recovered=True,
                            )
                        )

                    else:

                        if durable_decision is not None:
                            raise RuntimeError(
                                "Durable timeframe says "
                                "called=False but a "
                                "durable decision exists: "
                                f"seq={seq} tf={tf}"
                            )

                        result = {
                            "called": False,
                            "reason":
                                durable_result.get(
                                    "reason"
                                ),
                            "decision": None,
                            "trade_intent": None,
                            "recovered": True,
                        }

                    item = {
                        "timeframe": tf,
                        "called":
                            called,
                        "reason":
                            durable_result.get(
                                "reason"
                            ),
                        "evidence_fingerprint":
                            durable_result.get(
                                "evidence_fingerprint"
                            ),
                        "watch_triggered":
                            bool(
                                durable_result.get(
                                    "watch_triggered"
                                )
                            ),
                        "force_rebase":
                            bool(
                                durable_result.get(
                                    "force_rebase"
                                )
                            ),
                        "rows":
                            durable_result.get(
                                "rows"
                            ),
                        "recovered_decision":
                            bool(called),
                        "recovered_timeframe_result":
                            True,
                    }

                    processed.append(
                        item
                    )

                    reused_timeframes += 1

                    self.logger.event(
                        "recovery",
                        "TIMEFRAME_RESULT_REUSED",
                        {
                            "seq": int(seq),
                            "timeframe": tf,
                            "status":
                                "PROCESSED",
                            "called":
                                called,
                        },
                    )

                    if called:

                        claude_called = True

                        actions.append(
                            f"{tf}:"
                            f"{decision.action}"
                        )

                        execution_decisions.append(
                            {
                                "timeframe": tf,
                                "decision":
                                    decision,
                            }
                        )

                        if result.get(
                            "trade_intent"
                        ):
                            trade_candidates.append(
                                {
                                    "timeframe":
                                        tf,
                                    "decision":
                                        decision,
                                }
                            )

                    continue

                # --------------------------------------------
                # Fresh timeframe
                # --------------------------------------------

                df = self.simulator.rates(
                    self.symbol,
                    tf,
                    count=self.history_bars,
                )

                rows = len(df)

                if (
                    rows
                    < self.left
                    + self.right
                    + 1
                ):
                    self.recovery.record_timeframe_result(
                        seq=seq,
                        sim_time=
                            event["sim_time"],
                        timeframe=tf,
                        status="SKIPPED",
                        called=False,
                        reason=
                            "INSUFFICIENT_HISTORY",
                        evidence_fingerprint=
                            None,
                        watch_triggered=False,
                        force_rebase=False,
                        rows=rows,
                        recovered_decision=
                            False,
                    )

                    self.logger.event(
                        "decisions",
                        "TIMEFRAME_SKIPPED",
                        {
                            "timeframe": tf,
                            "reason":
                                "INSUFFICIENT_HISTORY",
                            "rows": rows,
                        },
                    )

                    processed.append(
                        {
                            "timeframe": tf,
                            "called": False,
                            "reason":
                                "INSUFFICIENT_HISTORY",
                            "evidence_fingerprint":
                                None,
                            "watch_triggered":
                                False,
                            "force_rebase":
                                False,
                            "rows":
                                rows,
                            "recovered_decision":
                                False,
                            "recovered_timeframe_result":
                                False,
                        }
                    )

                    continue

                latest = (
                    df.iloc[-1]
                    .to_dict()
                )

                pack = build_evidence(
                    df,
                    self.symbol,
                    tf,
                    left=self.left,
                    right=self.right,
                    recent=self.recent,
                    generated_at=
                        self.clock.now(),
                )

                watch_triggered = (
                    self._watch_triggered(
                        tf,
                        latest,
                    )
                )

                force_rebase = (
                    self._force_rebase(tf)
                )

                fingerprint = (
                    material_fingerprint(
                        pack
                    )
                )

                (
                    result,
                    recovered_decision,
                ) = (
                    self
                    ._process_pack_with_recovery(
                        seq=seq,
                        sim_time=
                            event["sim_time"],
                        timeframe=tf,
                        pack=pack,
                        fingerprint=
                            fingerprint,
                        watch_triggered=
                            watch_triggered,
                        force_rebase=
                            force_rebase,
                    )
                )

                decision = result.get(
                    "decision"
                )

                if (
                    result.get("called")
                    and decision is None
                ):
                    raise RuntimeError(
                        "Orchestrator returned "
                        "called=True without "
                        "a decision: "
                        f"seq={seq} tf={tf}"
                    )

                # Critical ordering:
                # persist the timeframe outcome BEFORE
                # moving to the next timeframe.
                self.recovery.record_timeframe_result(
                    seq=seq,
                    sim_time=
                        event["sim_time"],
                    timeframe=tf,
                    status="PROCESSED",
                    called=bool(
                        result.get(
                            "called"
                        )
                    ),
                    reason=
                        result.get("reason"),
                    evidence_fingerprint=
                        fingerprint,
                    watch_triggered=
                        watch_triggered,
                    force_rebase=
                        force_rebase,
                    rows=rows,
                    recovered_decision=
                        recovered_decision,
                )

                processed.append(
                    {
                        "timeframe": tf,
                        "called": bool(
                            result.get(
                                "called"
                            )
                        ),
                        "reason":
                            result.get(
                                "reason"
                            ),
                        "evidence_fingerprint":
                            fingerprint,
                        "watch_triggered":
                            watch_triggered,
                        "force_rebase":
                            force_rebase,
                        "rows":
                            rows,
                        "recovered_decision":
                            recovered_decision,
                        "recovered_timeframe_result":
                            False,
                    }
                )

                if result.get("called"):

                    claude_called = True

                    actions.append(
                        f"{tf}:"
                        f"{decision.action}"
                    )

                    # Execution receives EVERY
                    # Claude decision, including WAIT.
                    execution_decisions.append(
                        {
                            "timeframe": tf,
                            "decision":
                                decision,
                        }
                    )

                    if result.get(
                        "trade_intent"
                    ):
                        trade_candidates.append(
                            {
                                "timeframe": tf,
                                "decision":
                                    decision,
                            }
                        )

            # ================================================
            # Atomic boundary:
            # analysis is now immutable BEFORE execution.
            # ================================================

            self.recovery.mark_analysis_complete(
                seq,
                closed_timeframes=
                    sorted(closed),
                claude_called=
                    claude_called,
                actions=
                    actions,
                processed=
                    processed,
                execution_plan=
                    execution_decisions,
            )

            analysis_recovery_mode = (
                "PARTIAL_RESUME"
                if reused_timeframes
                else "FRESH"
            )

        # ====================================================
        # Execution uses the exact durable execution plan.
        # ====================================================

        (
            execution_results,
            execution_recovery_mode,
        ) = self._execute_with_recovery(
            seq=seq,
            execution_decisions=
                execution_decisions,
        )

        memory_after = (
            self._memory_audit()
        )

        decision_text = (
            ",".join(actions)
            if actions
            else None
        )

        (
            ack,
            ack_recovery_mode,
        ) = self._ack_with_recovery(
            seq=seq,
            claude_called=
                claude_called,
            decision_text=
                decision_text,
        )

        self.logger.event(
            "replay",
            "EVENT_COMPLETED",
            {
                "seq": seq,
                "sim_time":
                    event["sim_time"],
                "closed_timeframes":
                    sorted(closed),
                "analyzed_timeframes": [
                    x["timeframe"]
                    for x in processed
                ],
                "claude_called":
                    claude_called,
                "actions":
                    actions,
                "processed":
                    processed,
                "trade_candidates": [
                    ReplayExecutor
                    ._candidate_summary(x)
                    for x
                    in trade_candidates
                ],
                "execution_decisions": [
                    ReplayExecutor
                    ._candidate_summary(x)
                    for x
                    in execution_decisions
                ],
                "execution_results":
                    execution_results,
                "analysis_recovery_mode":
                    analysis_recovery_mode,
                "execution_recovery_mode":
                    execution_recovery_mode,
                "ack_recovery_mode":
                    ack_recovery_mode,
                "memory_before":
                    memory_before,
                "memory_after":
                    memory_after,
                "ack":
                    ack,
            },
        )

        return {
            "seq": seq,
            "claude_called":
                claude_called,
            "actions":
                actions,
            "processed":
                processed,
            "execution_results":
                execution_results,
            "analysis_recovery_mode":
                analysis_recovery_mode,
            "execution_recovery_mode":
                execution_recovery_mode,
            "ack_recovery_mode":
                ack_recovery_mode,
        }

    def run_until_finished(
        self,
        poll_seconds: float = 0.02,
    ) -> dict:

        last_seq = 0

        self._reconcile_orphaned_acks()

        while True:

            event = (
                self.simulator
                .wait_event(
                    last_seq,
                    poll_seconds=
                        poll_seconds,
                )
            )

            if event is None:
                status = (
                    self.simulator
                    .status()
                )

                if (
                    status.get("status")
                    == "FINISHED"
                ):
                    break

                if status.get(
                    "status"
                ) in {
                    "ERROR",
                    "STOPPED",
                }:
                    raise RuntimeError(
                        "Simulator stopped: "
                        f"{status}"
                    )

                time.sleep(
                    poll_seconds
                )
                continue

            result = self.process_event(
                event
            )

            last_seq = int(
                result["seq"]
            )

            print(
                f'{last_seq:04d} '
                f'{self.clock.now().isoformat()} '
                f'Claude={result["claude_called"]} '
                f'{result["actions"]} '
                f'Execution={result["execution_results"]}'
            )

        report = (
            self.simulator
            .report()
        )

        # Add final broker state that /report itself does not fully expose.
        report["final_positions"] = (
            self.simulator
            .positions_get(
                self.symbol
            )
        )

        report["final_orders"] = (
            self.simulator
            .orders_get(
                self.symbol
            )
        )

        report["history_deals"] = (
            self.simulator
            .history_deals_get()
        )

        (
            self.run_root
            / "simulator_report.json"
        ).write_text(
            json.dumps(
                report,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

        return report
