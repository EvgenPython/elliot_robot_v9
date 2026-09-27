import tempfile
import unittest
from datetime import (
    datetime,
    timezone,
)
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.recovery import (
    ReplayRecoveryJournal,
)
from waveframe.replay_runtime import (
    ReplayRuntime,
)


SIM_TIME = (
    "2026-09-25T09:00:00+00:00"
)


def wait_decision(
    timeframe="M15",
):
    count = ElliottCount(
        degree=timeframe,
        direction="neutral",
        current_wave="test",
        legs=[],
        invalidation=None,
        summary="test",
    )

    return ClaudeDecision(
        action="WAIT",
        primary_count=count,
        alternate_count=None,
        structure_assessment="test",
        support_resistance_assessment=
            "test",
        channel_assessment="test",
        pattern_assessment=[],
        evidence_disagreements=[],
        watch_conditions=[],
        entry=None,
        stop=None,
        target=None,
        confidence="low",
        rationale_brief="test",
        evidence_interpretation="test",
    )


class SilentLogger:
    def event(
        self,
        *args,
        **kwargs,
    ):
        pass


class FakeClock:
    def __init__(self):
        self.value = SIM_TIME

    def set(self, value):
        self.value = value

    def now(self):
        value = str(
            self.value
        ).replace(
            "Z",
            "+00:00",
        )

        return datetime.fromisoformat(
            value
        )


class FakeSimulator:
    def __init__(self):
        self.rates_calls = []
        self.analysis_calls = []

    def analysis_started(
        self,
        seq,
        cycle_id=None,
    ):
        self.analysis_calls.append(
            (seq, cycle_id)
        )

        return {
            "ok": True,
        }

    def rates(
        self,
        symbol,
        timeframe,
        count=300,
    ):
        self.rates_calls.append(
            timeframe
        )

        rows = []

        for i in range(12):
            rows.append(
                {
                    "time": i,
                    "open_time": i,
                    "close_time": i,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                }
            )

        return pd.DataFrame(
            rows
        )


class FakeOrchestrator:
    def __init__(self):
        self.process_calls = 0
        self.result_calls = 0

        self.claude = SimpleNamespace(
            api_called=False
        )

    def process_evidence(
        self,
        pack,
        **kwargs,
    ):
        self.process_calls += 1

        return {
            "called": False,
            "reason":
                "NO_MATERIAL_DELTA",
            "decision": None,
            "trade_intent": None,
            "recovered": False,
        }

    def result_from_decision(
        self,
        decision,
        reason,
        recovered=False,
    ):
        self.result_calls += 1

        return {
            "called": True,
            "reason": reason,
            "decision": decision,
            "trade_intent": None,
            "recovered": recovered,
        }


def runtime_shell(
    journal,
):
    rt = ReplayRuntime.__new__(
        ReplayRuntime
    )

    rt.recovery = journal
    rt.clock = FakeClock()
    rt.logger = SilentLogger()
    rt.simulator = FakeSimulator()
    rt.orchestrator = (
        FakeOrchestrator()
    )

    rt.symbol = "XAUUSD"
    rt.history_bars = 300
    rt.left = 3
    rt.right = 3
    rt.recent = 24

    rt._memory_audit = (
        lambda: {}
    )

    rt._watch_triggered = (
        lambda timeframe, latest:
            False
    )

    rt._force_rebase = (
        lambda timeframe:
            False
    )

    rt._execute_with_recovery = (
        lambda **kwargs:
            ([], "FRESH")
    )

    rt._ack_with_recovery = (
        lambda **kwargs:
            (
                {"ok": True},
                "FRESH",
            )
    )

    return rt


class AnalysisRecoveryTests(
    unittest.TestCase
):

    def test_no_call_timeframe_result_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            kwargs = dict(
                seq=1,
                sim_time=SIM_TIME,
                timeframe="H1",
                status="PROCESSED",
                called=False,
                reason=
                    "NO_MATERIAL_DELTA",
                evidence_fingerprint=
                    "fp-h1",
                watch_triggered=False,
                force_rebase=False,
                rows=300,
                recovered_decision=False,
            )

            first = (
                journal
                .record_timeframe_result(
                    **kwargs
                )
            )

            second = (
                journal
                .record_timeframe_result(
                    **kwargs
                )
            )

            self.assertEqual(
                first,
                second,
            )

            self.assertFalse(
                journal
                .get_timeframe_result(
                    1,
                    "H1",
                )["called"]
            )

    def test_conflicting_timeframe_result_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            journal.record_timeframe_result(
                seq=2,
                sim_time=SIM_TIME,
                timeframe="H1",
                status="PROCESSED",
                called=False,
                reason="FIRST",
                evidence_fingerprint=
                    "fp",
                watch_triggered=False,
                force_rebase=False,
                rows=300,
                recovered_decision=False,
            )

            with self.assertRaises(
                RuntimeError
            ):
                journal.record_timeframe_result(
                    seq=2,
                    sim_time=SIM_TIME,
                    timeframe="H1",
                    status="PROCESSED",
                    called=False,
                    reason="DIFFERENT",
                    evidence_fingerprint=
                        "fp",
                    watch_triggered=False,
                    force_rebase=False,
                    rows=300,
                    recovered_decision=False,
                )

    def test_analysis_plan_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            decision = wait_decision(
                "M15"
            )

            journal.record_decision(
                seq=3,
                sim_time=SIM_TIME,
                timeframe="M15",
                evidence_fingerprint=
                    "fp-m15",
                decision=decision,
                api_called=False,
            )

            journal.mark_memory_committed(
                3,
                "M15",
            )

            journal.record_timeframe_result(
                seq=3,
                sim_time=SIM_TIME,
                timeframe="M15",
                status="PROCESSED",
                called=True,
                reason="EVIDENCE_CHANGED",
                evidence_fingerprint=
                    "fp-m15",
                watch_triggered=False,
                force_rebase=False,
                rows=300,
                recovered_decision=False,
            )

            processed = [
                {
                    "timeframe": "M15",
                    "called": True,
                    "reason":
                        "EVIDENCE_CHANGED",
                }
            ]

            plan = [
                {
                    "timeframe": "M15",
                    "decision": decision,
                }
            ]

            first = (
                journal
                .mark_analysis_complete(
                    3,
                    closed_timeframes=[
                        "M15",
                    ],
                    claude_called=True,
                    actions=[
                        "M15:WAIT",
                    ],
                    processed=
                        processed,
                    execution_plan=
                        plan,
                )
            )

            second = (
                journal
                .mark_analysis_complete(
                    3,
                    closed_timeframes=[
                        "M15",
                    ],
                    claude_called=True,
                    actions=[
                        "M15:WAIT",
                    ],
                    processed=
                        processed,
                    execution_plan=
                        plan,
                )
            )

            self.assertEqual(
                first,
                second,
            )

            restored = (
                journal.get_analysis(3)
            )

            self.assertEqual(
                "WAIT",
                restored[
                    "execution_plan"
                ][0][
                    "decision"
                ][
                    "action"
                ],
            )

    def test_conflicting_analysis_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            journal.ensure_event(
                4,
                SIM_TIME,
            )

            journal.mark_analysis_complete(
                4,
                closed_timeframes=[],
                claude_called=False,
                actions=[],
                processed=[],
                execution_plan=[],
            )

            with self.assertRaises(
                RuntimeError
            ):
                journal.mark_analysis_complete(
                    4,
                    closed_timeframes=[],
                    claude_called=False,
                    actions=[
                        "M15:WAIT",
                    ],
                    processed=[],
                    execution_plan=[],
                )

    def test_complete_analysis_reuses_plan_without_rates_or_orchestrator(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            decision = wait_decision(
                "M15"
            )

            journal.record_decision(
                seq=5,
                sim_time=SIM_TIME,
                timeframe="M15",
                evidence_fingerprint=
                    "fp-m15",
                decision=decision,
                api_called=False,
            )

            journal.mark_memory_committed(
                5,
                "M15",
            )

            journal.record_timeframe_result(
                seq=5,
                sim_time=SIM_TIME,
                timeframe="M15",
                status="PROCESSED",
                called=True,
                reason="FIRST_ANALYSIS",
                evidence_fingerprint=
                    "fp-m15",
                watch_triggered=False,
                force_rebase=False,
                rows=300,
                recovered_decision=False,
            )

            journal.mark_analysis_complete(
                5,
                closed_timeframes=[
                    "M15",
                    "M5",
                ],
                claude_called=True,
                actions=[
                    "M15:WAIT",
                ],
                processed=[
                    {
                        "timeframe":
                            "M15",
                        "called":
                            True,
                        "reason":
                            "FIRST_ANALYSIS",
                    }
                ],
                execution_plan=[
                    {
                        "timeframe":
                            "M15",
                        "decision":
                            decision,
                    }
                ],
            )

            rt = runtime_shell(
                journal
            )

            captured = {}

            def execute(**kwargs):
                captured[
                    "plan"
                ] = kwargs[
                    "execution_decisions"
                ]

                return (
                    [],
                    "FRESH",
                )

            rt._execute_with_recovery = (
                execute
            )

            result = rt.process_event(
                {
                    "seq": 5,
                    "sim_time":
                        SIM_TIME,
                    "closed_timeframes":
                        [
                            "M5",
                            "M15",
                        ],
                }
            )

            self.assertEqual(
                [],
                rt.simulator.rates_calls,
            )

            self.assertEqual(
                0,
                rt.orchestrator
                .process_calls,
            )

            self.assertEqual(
                "WAIT",
                captured[
                    "plan"
                ][0][
                    "decision"
                ].action,
            )

            self.assertEqual(
                "REUSED_COMPLETE",
                result[
                    "analysis_recovery_mode"
                ],
            )

    def test_durable_no_call_skips_rates_watch_and_orchestrator(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            journal.record_timeframe_result(
                seq=6,
                sim_time=SIM_TIME,
                timeframe="M15",
                status="PROCESSED",
                called=False,
                reason=
                    "NO_MATERIAL_DELTA",
                evidence_fingerprint=
                    "fp-m15",
                watch_triggered=False,
                force_rebase=False,
                rows=300,
                recovered_decision=False,
            )

            rt = runtime_shell(
                journal
            )

            result = rt.process_event(
                {
                    "seq": 6,
                    "sim_time":
                        SIM_TIME,
                    "closed_timeframes":
                        ["M15"],
                }
            )

            self.assertEqual(
                [],
                rt.simulator.rates_calls,
            )

            self.assertEqual(
                0,
                rt.orchestrator
                .process_calls,
            )

            self.assertTrue(
                result[
                    "processed"
                ][0][
                    "recovered_timeframe_result"
                ]
            )

            self.assertEqual(
                "PARTIAL_RESUME",
                result[
                    "analysis_recovery_mode"
                ],
            )

    def test_multitimeframe_partial_resume_only_processes_remaining_tf(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            journal.record_timeframe_result(
                seq=7,
                sim_time=SIM_TIME,
                timeframe="H1",
                status="PROCESSED",
                called=False,
                reason=
                    "NO_MATERIAL_DELTA",
                evidence_fingerprint=
                    "fp-h1",
                watch_triggered=False,
                force_rebase=False,
                rows=300,
                recovered_decision=False,
            )

            rt = runtime_shell(
                journal
            )

            def fake_build(
                df,
                symbol,
                timeframe,
                **kwargs,
            ):
                return (
                    SimpleNamespace(
                        timeframe=
                            timeframe
                    )
                )

            with patch(
                "waveframe.replay_runtime.build_evidence",
                side_effect=
                    fake_build,
            ), patch(
                "waveframe.replay_runtime.material_fingerprint",
                return_value=
                    "fp-m15",
            ):
                result = rt.process_event(
                    {
                        "seq": 7,
                        "sim_time":
                            SIM_TIME,
                        "closed_timeframes":
                            [
                                "H1",
                                "M15",
                            ],
                    }
                )

            self.assertEqual(
                ["M15"],
                rt.simulator.rates_calls,
            )

            self.assertEqual(
                1,
                rt.orchestrator
                .process_calls,
            )

            self.assertIsNotNone(
                journal
                .get_timeframe_result(
                    7,
                    "M15",
                )
            )

            self.assertIsNotNone(
                journal.get_analysis(
                    7
                )
            )

            self.assertEqual(
                "PARTIAL_RESUME",
                result[
                    "analysis_recovery_mode"
                ],
            )

    def test_analysis_is_sealed_before_execution_starts(self):
        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(
                    td
                )
            )

            rt = runtime_shell(
                journal
            )

            observed = {
                "sealed":
                    False,
            }

            def execute(
                *,
                seq,
                execution_decisions,
            ):
                observed[
                    "sealed"
                ] = (
                    journal
                    .get_analysis(seq)
                    is not None
                )

                return (
                    [],
                    "FRESH",
                )

            rt._execute_with_recovery = (
                execute
            )

            result = rt.process_event(
                {
                    "seq": 8,
                    "sim_time":
                        SIM_TIME,
                    # M5 is deliberately not in
                    # ANALYSIS_ORDER.
                    "closed_timeframes":
                        ["M5"],
                }
            )

            self.assertTrue(
                observed["sealed"]
            )

            self.assertEqual(
                "FRESH",
                result[
                    "analysis_recovery_mode"
                ],
            )


if __name__ == "__main__":
    unittest.main()
