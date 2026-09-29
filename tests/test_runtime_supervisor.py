import tempfile
import unittest
from pathlib import Path

from waveframe.runtime_supervisor import (
    consume_resume_signal,
    run_runtime_supervised,
    safe_hold_until_resume,
)


class FakeSim:

    def __init__(
        self,
        fail_status=False,
    ):
        self.fail_status = (
            fail_status
        )

    def status(self):

        if self.fail_status:
            raise RuntimeError(
                "simulator unavailable"
            )

        return {
            "status":
                "WAITING_ACK",
            "seq":
                558,
            "sim_now":
                "2026-09-23T01:30:00+00:00",
        }


class RuntimeSequence:

    def __init__(
        self,
        outcomes,
    ):
        self.outcomes = list(
            outcomes
        )

        self.calls = 0

    def run_until_finished(
        self
    ):
        self.calls += 1

        outcome = (
            self.outcomes
            .pop(0)
        )

        if isinstance(
            outcome,
            BaseException,
        ):
            raise outcome

        return outcome


class RuntimeSupervisorTests(
    unittest.TestCase
):

    def test_runtime_error_enters_hold_then_continues(self):

        runtime = RuntimeSequence(
            [
                RuntimeError(
                    "temporary runtime fault"
                ),
                {
                    "trades_closed": 1,
                },
            ]
        )

        holds = []

        def hold_fn(
            run_root,
            sim,
            exc,
        ):
            holds.append(
                type(exc).__name__
            )

        report = (
            run_runtime_supervised(
                runtime,
                FakeSim(),
                Path("."),
                hold_fn=hold_fn,
            )
        )

        self.assertEqual(
            runtime.calls,
            2,
        )

        self.assertEqual(
            holds,
            [
                "RuntimeError",
            ],
        )

        self.assertEqual(
            report[
                "trades_closed"
            ],
            1,
        )


    def test_system_exit_cannot_kill_runtime(self):

        runtime = RuntimeSequence(
            [
                SystemExit(2),
                {
                    "trades_closed": 2,
                },
            ]
        )

        holds = []

        def hold_fn(
            run_root,
            sim,
            exc,
        ):
            holds.append(
                type(exc).__name__
            )

        report = (
            run_runtime_supervised(
                runtime,
                FakeSim(),
                Path("."),
                hold_fn=hold_fn,
            )
        )

        self.assertEqual(
            runtime.calls,
            2,
        )

        self.assertEqual(
            holds,
            [
                "SystemExit",
            ],
        )

        self.assertEqual(
            report[
                "trades_closed"
            ],
            2,
        )


    def test_keyboard_interrupt_is_explicit_operator_stop(self):

        runtime = RuntimeSequence(
            [
                KeyboardInterrupt(),
            ]
        )

        holds = []

        def hold_fn(
            run_root,
            sim,
            exc,
        ):
            holds.append(
                exc
            )

        with self.assertRaises(
            KeyboardInterrupt
        ):
            run_runtime_supervised(
                runtime,
                FakeSim(),
                Path("."),
                hold_fn=hold_fn,
            )

        self.assertEqual(
            holds,
            [],
        )


    def test_resume_signal_is_consumed_once(self):

        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            signal = (
                root
                / "RESUME.signal"
            )

            signal.write_text(
                "resume",
                encoding="utf-8",
            )

            self.assertTrue(
                consume_resume_signal(
                    root
                )
            )

            self.assertFalse(
                signal.exists()
            )

            self.assertFalse(
                consume_resume_signal(
                    root
                )
            )


    def test_safe_hold_survives_simulator_outage_and_resumes(self):

        with tempfile.TemporaryDirectory() as td:

            root = Path(td)

            checks = iter(
                [
                    False,
                    True,
                ]
            )

            sleeps = []

            safe_hold_until_resume(
                root,
                FakeSim(
                    fail_status=True
                ),
                RuntimeError(
                    "test failure"
                ),
                sleep=lambda seconds:
                    sleeps.append(
                        seconds
                    ),
                resume_check=lambda:
                    next(checks),
                heartbeat_seconds=0.01,
            )

            self.assertEqual(
                sleeps,
                [
                    0.01,
                ],
            )


if __name__ == "__main__":
    unittest.main()
