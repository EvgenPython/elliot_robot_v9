import tempfile
import unittest

from waveframe.models import ClaudeDecision
from waveframe.recovery import ReplayRecoveryJournal
from waveframe.replay_runtime import ReplayRuntime


SIM_TIME = "2026-10-03T10:00:00+00:00"


class CaptureLogger:

    def __init__(self):
        self.events = []

    def event(
        self,
        category,
        event,
        data,
    ):
        self.events.append(
            {
                "category": category,
                "event": event,
                "data": data,
            }
        )


def ready_decision(
    action="READY_LONG",
):
    if action == "READY_LONG":
        entry = 4300.0
        stop = 4290.0
        target = 4320.0
        direction = "bullish"

    else:
        entry = 4300.0
        stop = 4310.0
        target = 4280.0
        direction = "bearish"

    return ClaudeDecision.model_validate(
        {
            "action": action,
            "primary_count": {
                "degree": "M15",
                "direction": direction,
                "current_wave": "3",
                "legs": [],
                "invalidation": stop,
                "summary":
                    "Confirmation gate test.",
            },
            "alternate_count": None,
            "structure_assessment":
                "test structure",
            "support_resistance_assessment":
                "test support/resistance",
            "channel_assessment":
                "test channel",
            "pattern_assessment": [],
            "evidence_disagreements": [],
            "watch_conditions": [],
            "entry": entry,
            "stop": stop,
            "target": target,
            "confidence": "medium",
            "rationale_brief":
                "test rationale",
            "evidence_interpretation":
                "test evidence",
        }
    )


def runtime_for_gate():
    runtime = object.__new__(
        ReplayRuntime
    )

    runtime.logger = CaptureLogger()

    return runtime


class ConfirmationGateTests(
    unittest.TestCase
):

    def test_evidence_changed_ready_is_execution_wait(self):
        runtime = runtime_for_gate()

        original = ready_decision(
            "READY_LONG"
        )

        plan = [
            {
                "timeframe": "M15",
                "decision": original,
            }
        ]

        processed = [
            {
                "timeframe": "M15",
                "called": True,
                "reason":
                    "EVIDENCE_CHANGED",
            }
        ]

        transformed = (
            runtime
            ._apply_ready_confirmation_gate(
                plan,
                processed,
            )
        )

        # Claude's original decision object remains untouched.
        self.assertEqual(
            "READY_LONG",
            original.action,
        )

        execution_decision = (
            transformed[0]["decision"]
        )

        self.assertEqual(
            "WAIT",
            execution_decision.action,
        )

        self.assertIsNone(
            execution_decision.entry
        )

        self.assertIsNone(
            execution_decision.stop
        )

        self.assertIsNone(
            execution_decision.target
        )

        self.assertEqual(
            1,
            len(runtime.logger.events),
        )

        event = (
            runtime.logger.events[0]
        )

        self.assertEqual(
            "trade_funnel",
            event["category"],
        )

        self.assertEqual(
            (
                "READY_SUPPRESSED_"
                "AWAITING_CONFIRMATION"
            ),
            event["event"],
        )

        self.assertEqual(
            "READY_LONG",
            event["data"][
                "original_action"
            ],
        )

        self.assertEqual(
            "EVIDENCE_CHANGED",
            event["data"]["reason"],
        )


    def test_watch_triggered_ready_is_not_suppressed(self):
        runtime = runtime_for_gate()

        original = ready_decision(
            "READY_SHORT"
        )

        transformed = (
            runtime
            ._apply_ready_confirmation_gate(
                [
                    {
                        "timeframe":
                            "M15",
                        "decision":
                            original,
                    }
                ],
                [
                    {
                        "timeframe":
                            "M15",
                        "called":
                            True,
                        "reason":
                            "WATCH_TRIGGERED",
                    }
                ],
            )
        )

        self.assertEqual(
            "READY_SHORT",
            transformed[0][
                "decision"
            ].action,
        )

        self.assertEqual(
            [],
            runtime.logger.events,
        )


    def test_force_rebase_ready_is_not_suppressed(self):
        runtime = runtime_for_gate()

        original = ready_decision(
            "READY_LONG"
        )

        transformed = (
            runtime
            ._apply_ready_confirmation_gate(
                [
                    {
                        "timeframe":
                            "H1",
                        "decision":
                            original,
                    }
                ],
                [
                    {
                        "timeframe":
                            "H1",
                        "called":
                            True,
                        "reason":
                            "FORCE_REBASE",
                    }
                ],
            )
        )

        self.assertEqual(
            "READY_LONG",
            transformed[0][
                "decision"
            ].action,
        )

        self.assertEqual(
            [],
            runtime.logger.events,
        )


    def test_suppressed_wait_is_durable_before_execution(self):
        runtime = runtime_for_gate()

        original = ready_decision(
            "READY_LONG"
        )

        processed = [
            {
                "timeframe": "M15",
                "called": True,
                "reason":
                    "EVIDENCE_CHANGED",
            }
        ]

        transformed = (
            runtime
            ._apply_ready_confirmation_gate(
                [
                    {
                        "timeframe":
                            "M15",
                        "decision":
                            original,
                    }
                ],
                processed,
            )
        )

        with tempfile.TemporaryDirectory() as td:
            journal = (
                ReplayRecoveryJournal(td)
            )

            journal.ensure_event(
                7,
                SIM_TIME,
            )

            journal.record_timeframe_result(
                seq=7,
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

            sealed = (
                journal
                .mark_analysis_complete(
                    7,
                    closed_timeframes=[
                        "M15"
                    ],
                    claude_called=True,
                    actions=[
                        "M15:READY_LONG"
                    ],
                    processed=processed,
                    execution_plan=
                        transformed,
                )
            )

            restored = (
                journal.get_analysis(7)
            )

            # Durable analysis still records what Claude said.
            self.assertEqual(
                [
                    "M15:READY_LONG"
                ],
                sealed["actions"],
            )

            # Durable EXECUTION source of truth is WAIT.
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

            self.assertIsNone(
                restored[
                    "execution_plan"
                ][0][
                    "decision"
                ][
                    "entry"
                ]
            )

            # Simulate any number of crash/resume reads:
            # suppressed READY never reappears.
            second_read = (
                journal.get_analysis(7)
            )

            self.assertEqual(
                restored,
                second_read,
            )

            self.assertEqual(
                "WAIT",
                second_read[
                    "execution_plan"
                ][0][
                    "decision"
                ][
                    "action"
                ],
            )


if __name__ == "__main__":
    unittest.main()
