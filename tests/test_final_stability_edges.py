import json
import tempfile
import unittest
from pathlib import Path

from waveframe.recovery import (
    ReplayRecoveryJournal,
)
from waveframe.replay_runtime import (
    ReplayRuntime,
)


SIM_TIME = (
    "2026-09-25T08:15:00+00:00"
)


class SilentLogger:
    def event(
        self,
        category,
        event,
        data,
    ):
        pass


class StaticSimulator:
    def __init__(
        self,
        status,
        ack_error=None,
    ):
        self._status = status
        self.ack_error = ack_error
        self.ack_calls = 0
        self.start_calls = 0

    def status(self):
        return dict(
            self._status
        )

    def start(self):
        self.start_calls += 1

        return dict(
            self._status
        )

    def ack(
        self,
        *args,
        **kwargs,
    ):
        self.ack_calls += 1

        if self.ack_error is not None:
            raise self.ack_error

        return {
            "ok": True,
        }


def runtime_shell(
    journal,
    simulator,
):
    rt = ReplayRuntime.__new__(
        ReplayRuntime
    )

    rt.recovery = journal
    rt.simulator = simulator
    rt.logger = SilentLogger()

    return rt


class FinalStabilityEdgeTests(
    unittest.TestCase
):

    def test_corrupt_recovery_json_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            journal.path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            journal.path.write_text(
                "{this-is-not-json",
                encoding="utf-8",
            )

            with self.assertRaises(
                json.JSONDecodeError
            ):
                journal.load()

    def test_recovery_event_sim_time_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            journal.ensure_event(
                1,
                SIM_TIME,
            )

            with self.assertRaises(
                RuntimeError
            ):
                journal.ensure_event(
                    1,
                    "2026-09-25T08:20:00+00:00",
                )

    def test_advanced_seq_proves_previous_ack_even_with_next_pending(self):
        status = {
            "seq": 21,
            "status": "WAITING_ACK",
            "pending_event": {
                "seq": 21,
            },
        }

        self.assertTrue(
            ReplayRuntime
            ._status_proves_ack(
                status,
                20,
            )
        )

    def test_waiting_ack_after_ack_is_valid_worker_progress(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            simulator = StaticSimulator(
                {
                    "seq": 21,
                    "status":
                        "WAITING_ACK",
                    "pending_event": {
                        "seq": 21,
                    },
                }
            )

            rt = runtime_shell(
                journal,
                simulator,
            )

            rt._ensure_simulator_running_after_ack(
                20
            )

            self.assertEqual(
                0,
                simulator.start_calls,
            )

    def test_worker_can_advance_between_status_and_start(self):
        class RacingSimulator:
            def __init__(self):
                self.status_calls = 0
                self.start_calls = 0

            def status(self):
                self.status_calls += 1

                if self.status_calls == 1:
                    return {
                        "seq": 262,
                        "status": "RUNNING",
                        "pending_event": None,
                    }

                return {
                    "seq": 263,
                    "status": "WAITING_ACK",
                    "pending_event": {
                        "seq": 263,
                    },
                }

            def start(self):
                self.start_calls += 1

                raise RuntimeError(
                    "400 Bad Request: already advanced"
                )

        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            simulator = RacingSimulator()

            rt = runtime_shell(
                journal,
                simulator,
            )

            # This exact production race must be treated as
            # successful forward progress, not as a crash.
            rt._ensure_simulator_running_after_ack(
                262
            )

            self.assertEqual(
                simulator.start_calls,
                1,
            )

            self.assertGreaterEqual(
                simulator.status_calls,
                2,
            )

    def test_lost_ack_response_reconciles_if_simulator_already_advanced(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            journal.ensure_event(
                20,
                SIM_TIME,
            )

            journal.mark_execution_done(
                20,
                [],
            )

            simulator = StaticSimulator(
                {
                    "seq": 21,
                    "status":
                        "WAITING_ACK",
                    "pending_event": {
                        "seq": 21,
                    },
                },
                ack_error=TimeoutError(
                    "ACK response lost"
                ),
            )

            rt = runtime_shell(
                journal,
                simulator,
            )

            ack, mode = (
                rt._ack_with_recovery(
                    seq=20,
                    claude_called=False,
                    decision_text=None,
                )
            )

            self.assertEqual(
                1,
                simulator.ack_calls,
            )

            self.assertEqual(
                "FRESH_OR_RECONCILED",
                mode,
            )

            self.assertTrue(
                ack["reconciled"]
            )

            event = (
                journal.get_event(20)
            )

            self.assertTrue(
                event["ack_done"]
            )

    def test_ack_done_conflict_with_same_pending_event_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            journal.ensure_event(
                30,
                SIM_TIME,
            )

            journal.mark_execution_done(
                30,
                [],
            )

            journal.mark_ack_done(
                30,
                {
                    "ok": True,
                },
            )

            simulator = StaticSimulator(
                {
                    "seq": 30,
                    "status":
                        "WAITING_ACK",
                    "pending_event": {
                        "seq": 30,
                    },
                }
            )

            rt = runtime_shell(
                journal,
                simulator,
            )

            with self.assertRaises(
                RuntimeError
            ):
                rt._ack_with_recovery(
                    seq=30,
                    claude_called=False,
                    decision_text=None,
                )

    def test_orphan_ack_is_not_reconciled_while_same_event_is_pending(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(
                td
            )

            journal.ensure_event(
                40,
                SIM_TIME,
            )

            journal.mark_execution_done(
                40,
                [],
            )

            simulator = StaticSimulator(
                {
                    "seq": 40,
                    "status":
                        "WAITING_ACK",
                    "pending_event": {
                        "seq": 40,
                    },
                }
            )

            rt = runtime_shell(
                journal,
                simulator,
            )

            count = (
                rt._reconcile_orphaned_acks()
            )

            self.assertEqual(
                0,
                count,
            )

            self.assertFalse(
                journal
                .get_event(40)
                ["ack_done"]
            )


if __name__ == "__main__":
    unittest.main()
