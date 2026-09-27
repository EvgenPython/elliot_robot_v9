import unittest

from waveframe.replay_runtime import (
    ReplayRuntime,
)


class FakeLogger:
    def __init__(self):
        self.events = []

    def event(
        self,
        category,
        event,
        data,
    ):
        self.events.append(
            (category, event, data)
        )


class FakeRecovery:
    def __init__(self, event):
        self.event = event
        self.execution_started_calls = 0
        self.execution_done_calls = []
        self.ack_done_calls = []

    def get_event(self, seq):
        return self.event

    def mark_execution_started(
        self,
        seq,
    ):
        self.execution_started_calls += 1
        self.event[
            "execution_started"
        ] = True

    def mark_execution_done(
        self,
        seq,
        results,
    ):
        self.execution_done_calls.append(
            (seq, results)
        )
        self.event[
            "execution_started"
        ] = True
        self.event[
            "execution_done"
        ] = True
        self.event[
            "execution_results"
        ] = results

    def mark_ack_done(
        self,
        seq,
        ack,
    ):
        self.ack_done_calls.append(
            (seq, ack)
        )
        self.event["ack_done"] = True
        self.event["ack"] = ack

    def load(self):
        return {
            "events": {
                str(
                    self.event["seq"]
                ): self.event
            }
        }


class FakeExecutor:
    def __init__(self):
        self.calls = 0

    def execute_decisions(
        self,
        decisions,
    ):
        self.calls += 1

        return [
            {
                "status":
                    "PENDING_UNCHANGED",
                "count":
                    len(decisions),
            }
        ]


class FakeSimulator:
    def __init__(
        self,
        *,
        statuses,
        ack_behaviour,
    ):
        self.statuses = list(
            statuses
        )
        self.ack_behaviour = list(
            ack_behaviour
        )
        self.ack_calls = 0
        self.start_calls = 0

    def start(self):
        self.start_calls += 1

        return {
            "status": "RUNNING",
        }

    def status(self):
        if len(self.statuses) > 1:
            return self.statuses.pop(0)

        return self.statuses[0]

    def ack(self, *args, **kwargs):
        self.ack_calls += 1

        behaviour = (
            self.ack_behaviour.pop(0)
        )

        if isinstance(
            behaviour,
            Exception,
        ):
            raise behaviour

        # Real simulator ACK clears pending_event
        # and sets status=RUNNING.
        self.statuses = [
            {
                "seq": (
                    self.statuses[-1]
                    .get("seq", 0)
                ),
                "status": "RUNNING",
                "pending_event": None,
            }
        ]

        return behaviour


def runtime_shell(
    *,
    event,
    simulator=None,
):
    rt = ReplayRuntime.__new__(
        ReplayRuntime
    )

    rt.recovery = FakeRecovery(
        event
    )

    rt.executor = FakeExecutor()
    rt.logger = FakeLogger()
    rt.simulator = simulator

    return rt


class ExecutionAckRecoveryTests(
    unittest.TestCase
):

    def test_execution_done_is_never_reexecuted(self):
        event = {
            "seq": 10,
            "execution_started": True,
            "execution_done": True,
            "execution_results": [
                {
                    "status":
                        "ORDER_ACCEPTED"
                }
            ],
            "ack_done": False,
            "ack": None,
        }

        rt = runtime_shell(
            event=event
        )

        results, mode = (
            rt._execute_with_recovery(
                seq=10,
                execution_decisions=[
                    {"dummy": True}
                ],
            )
        )

        self.assertEqual(
            "REUSED_DONE",
            mode,
        )

        self.assertEqual(
            0,
            rt.executor.calls,
        )

        self.assertEqual(
            "ORDER_ACCEPTED",
            results[0]["status"],
        )

    def test_incomplete_execution_converges_once(self):
        event = {
            "seq": 11,
            "execution_started": True,
            "execution_done": False,
            "execution_results": None,
            "ack_done": False,
            "ack": None,
        }

        rt = runtime_shell(
            event=event
        )

        results, mode = (
            rt._execute_with_recovery(
                seq=11,
                execution_decisions=[
                    {"dummy": True}
                ],
            )
        )

        self.assertEqual(
            "RESUMED_INCOMPLETE",
            mode,
        )

        self.assertEqual(
            1,
            rt.executor.calls,
        )

        self.assertEqual(
            0,
            rt.recovery
            .execution_started_calls,
        )

        self.assertEqual(
            1,
            len(
                rt.recovery
                .execution_done_calls
            ),
        )

        self.assertEqual(
            "PENDING_UNCHANGED",
            results[0]["status"],
        )

    def test_fresh_execution_marks_started_before_done(self):
        event = {
            "seq": 12,
            "execution_started": False,
            "execution_done": False,
            "execution_results": None,
            "ack_done": False,
            "ack": None,
        }

        rt = runtime_shell(
            event=event
        )

        _, mode = (
            rt._execute_with_recovery(
                seq=12,
                execution_decisions=[],
            )
        )

        self.assertEqual(
            "FRESH",
            mode,
        )

        self.assertEqual(
            1,
            rt.recovery
            .execution_started_calls,
        )

        self.assertEqual(
            1,
            len(
                rt.recovery
                .execution_done_calls
            ),
        )

    def test_lost_ack_response_is_reconciled_without_retry(self):
        event = {
            "seq": 13,
            "execution_started": True,
            "execution_done": True,
            "execution_results": [],
            "ack_done": False,
            "ack": None,
        }

        sim = FakeSimulator(
            statuses=[
                {
                    "seq": 13,
                    "status": "RUNNING",
                    "pending_event": None,
                }
            ],
            ack_behaviour=[
                TimeoutError(
                    "response lost"
                )
            ],
        )

        rt = runtime_shell(
            event=event,
            simulator=sim,
        )

        ack, _ = (
            rt._ack_with_recovery(
                seq=13,
                claude_called=True,
                decision_text="M15:WAIT",
            )
        )

        self.assertEqual(
            1,
            sim.ack_calls,
        )

        self.assertTrue(
            ack["reconciled"]
        )

        self.assertEqual(
            1,
            len(
                rt.recovery
                .ack_done_calls
            ),
        )

    def test_ack_retries_once_only_when_same_event_still_pending(self):
        event = {
            "seq": 14,
            "execution_started": True,
            "execution_done": True,
            "execution_results": [],
            "ack_done": False,
            "ack": None,
        }

        sim = FakeSimulator(
            statuses=[
                {
                    "seq": 14,
                    "pending_event": {
                        "seq": 14
                    },
                }
            ],
            ack_behaviour=[
                TimeoutError(
                    "first send failed"
                ),
                {
                    "ok": True,
                },
            ],
        )

        rt = runtime_shell(
            event=event,
            simulator=sim,
        )

        ack, _ = (
            rt._ack_with_recovery(
                seq=14,
                claude_called=False,
                decision_text=None,
            )
        )

        self.assertEqual(
            2,
            sim.ack_calls,
        )

        self.assertTrue(
            ack[
                "retried_after_state_check"
            ]
        )

        self.assertTrue(
            event["ack_done"]
        )

    def test_startup_reconciles_ack_accepted_before_crash(self):
        event = {
            "seq": 15,
            "execution_started": True,
            "execution_done": True,
            "execution_results": [],
            "ack_done": False,
            "ack": None,
        }

        sim = FakeSimulator(
            statuses=[
                {
                    "seq": 16,
                    "pending_event": {
                        "seq": 16
                    },
                }
            ],
            ack_behaviour=[],
        )

        rt = runtime_shell(
            event=event,
            simulator=sim,
        )

        count = (
            rt._reconcile_orphaned_acks()
        )

        self.assertEqual(1, count)

        self.assertTrue(
            event["ack_done"]
        )

        self.assertTrue(
            event["ack"][
                "reconciled_on_startup"
            ]
        )


    def test_ack_ensures_worker_after_restored_waiting_ack(self):
        event = {
            "seq": 16,
            "execution_started": True,
            "execution_done": True,
            "execution_results": [],
            "ack_done": False,
            "ack": None,
        }

        sim = FakeSimulator(
            statuses=[
                {
                    "seq": 16,
                    "status": "WAITING_ACK",
                    "pending_event": {
                        "seq": 16
                    },
                }
            ],
            ack_behaviour=[
                {
                    "ok": True,
                }
            ],
        )

        rt = runtime_shell(
            event=event,
            simulator=sim,
        )

        rt._ack_with_recovery(
            seq=16,
            claude_called=False,
            decision_text="M15:WAIT",
        )

        self.assertEqual(
            1,
            sim.ack_calls,
        )

        # Critical restart invariant:
        # ACKing a restored WAITING_ACK replay must
        # ensure the engine worker exists afterwards.
        self.assertEqual(
            1,
            sim.start_calls,
        )


if __name__ == "__main__":
    unittest.main()
