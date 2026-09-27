import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from unittest.mock import patch

from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.orchestrator import Orchestrator
from waveframe.replay_runtime import ReplayRuntime


def decision():
    count = ElliottCount(
        degree="M15",
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
        support_resistance_assessment="test",
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


class DummyClaude:
    api_called = False


class FakeRecovery:
    def __init__(self, durable):
        self.durable = durable
        self.marked = []
        self.recorded = []

    def get_decision(
        self,
        seq,
        timeframe,
    ):
        return self.durable

    def mark_memory_committed(
        self,
        seq,
        timeframe,
    ):
        self.marked.append(
            (seq, timeframe)
        )

    def record_decision(self, **kwargs):
        self.recorded.append(kwargs)


class FakeLogger:
    def __init__(self):
        self.events = []

    def event(self, category, event, data):
        self.events.append(
            (category, event, data)
        )


class FakeOrchestrator:
    def __init__(self):
        self.claude = DummyClaude()
        self.process_calls = 0
        self.commit_calls = 0

    def result_from_decision(
        self,
        decision,
        reason,
        recovered=False,
    ):
        return {
            "called": True,
            "reason": reason,
            "decision": decision,
            "trade_intent": None,
            "recovered": recovered,
        }

    def commit_validated_decision(
        self,
        pack,
        decision,
        **kwargs,
    ):
        self.commit_calls += 1

        return {
            "called": True,
            "reason":
                kwargs["result_reason"],
            "decision": decision,
            "trade_intent": None,
            "recovered": True,
        }

    def process_evidence(
        self,
        pack,
        **kwargs,
    ):
        self.process_calls += 1

        callback = kwargs[
            "on_valid_decision"
        ]

        d = decision()

        callback(d)

        return {
            "called": True,
            "reason": "EVIDENCE_CHANGED",
            "decision": d,
            "trade_intent": None,
            "recovered": False,
        }


def runtime_shell(
    durable,
):
    rt = ReplayRuntime.__new__(
        ReplayRuntime
    )

    rt.recovery = FakeRecovery(
        durable
    )

    rt.orchestrator = (
        FakeOrchestrator()
    )

    rt.logger = FakeLogger()

    return rt


class RuntimeDecisionRecoveryTests(
    unittest.TestCase
):

    def test_durable_decision_skips_claude_path(self):
        d = decision()

        rt = runtime_shell(
            {
                "evidence_fingerprint":
                    "fp1",
                "api_called": True,
                "memory_committed": True,
                "decision":
                    d.model_dump(
                        mode="json"
                    ),
            }
        )

        result, recovered = (
            rt._process_pack_with_recovery(
                seq=7,
                sim_time=(
                    "2026-09-25T08:15:00+00:00"
                ),
                timeframe="M15",
                pack=object(),
                fingerprint="fp1",
                watch_triggered=False,
                force_rebase=False,
            )
        )

        self.assertTrue(recovered)
        self.assertEqual(
            0,
            rt.orchestrator.process_calls,
        )
        self.assertEqual(
            0,
            rt.orchestrator.commit_calls,
        )
        self.assertEqual(
            "WAIT",
            result["decision"].action,
        )

    def test_uncommitted_durable_decision_commits_memory_only(self):
        d = decision()

        rt = runtime_shell(
            {
                "evidence_fingerprint":
                    "fp2",
                "api_called": True,
                "memory_committed": False,
                "decision":
                    d.model_dump(
                        mode="json"
                    ),
            }
        )

        _, recovered = (
            rt._process_pack_with_recovery(
                seq=8,
                sim_time=(
                    "2026-09-25T08:15:00+00:00"
                ),
                timeframe="M15",
                pack=object(),
                fingerprint="fp2",
                watch_triggered=False,
                force_rebase=False,
            )
        )

        self.assertTrue(recovered)

        self.assertEqual(
            0,
            rt.orchestrator.process_calls,
        )

        self.assertEqual(
            1,
            rt.orchestrator.commit_calls,
        )

        self.assertEqual(
            [(8, "M15")],
            rt.recovery.marked,
        )

    def test_new_decision_is_durable_before_return(self):
        rt = runtime_shell(None)

        result, recovered = (
            rt._process_pack_with_recovery(
                seq=9,
                sim_time=(
                    "2026-09-25T08:15:00+00:00"
                ),
                timeframe="M15",
                pack=object(),
                fingerprint="fp3",
                watch_triggered=False,
                force_rebase=False,
            )
        )

        self.assertFalse(recovered)

        self.assertEqual(
            1,
            rt.orchestrator.process_calls,
        )

        self.assertEqual(
            1,
            len(rt.recovery.recorded),
        )

        self.assertEqual(
            "WAIT",
            rt.recovery.recorded[0]
            ["decision"].action,
        )

        self.assertEqual(
            [(9, "M15")],
            rt.recovery.marked,
        )

        self.assertEqual(
            "WAIT",
            result["decision"].action,
        )

    def test_memory_commit_is_idempotent_after_crash_window(self):
        with tempfile.TemporaryDirectory() as td:
            orch = Orchestrator(
                td,
                DummyClaude(),
            )

            pack = SimpleNamespace(
                symbol="XAUUSD",
                timeframe="M15",
                last_closed_bar=datetime(
                    2026,
                    9,
                    25,
                    8,
                    15,
                    tzinfo=timezone.utc,
                ),
            )

            d = decision()

            with patch(
                "waveframe.orchestrator.material_fingerprint",
                return_value="stable-fp",
            ):
                orch.commit_validated_decision(
                    pack,
                    d,
                    recovered=True,
                )

                first = (
                    orch.memory_store
                    .load("XAUUSD")
                )

                first_version = (
                    first.memory_version
                )

                orch.commit_validated_decision(
                    pack,
                    d,
                    recovered=True,
                )

                second = (
                    orch.memory_store
                    .load("XAUUSD")
                )

            self.assertEqual(
                first_version,
                second.memory_version,
            )


if __name__ == "__main__":
    unittest.main()
