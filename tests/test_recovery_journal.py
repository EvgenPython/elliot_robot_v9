import json
import tempfile
import unittest
from pathlib import Path

from waveframe.claude_gateway import (
    ClaudeGateway,
)
from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.recovery import (
    ReplayRecoveryJournal,
)
from waveframe.stub_claude import (
    StubClaudeGateway,
)


def wait_decision():
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


class SilentLogger:
    def claude_request(self, *args, **kwargs):
        pass

    def claude_response(self, *args, **kwargs):
        pass

    def claude_parsed(self, *args, **kwargs):
        pass

    def event(self, *args, **kwargs):
        pass

    def _market_now(self):
        from datetime import datetime, timezone
        return datetime.now(timezone.utc)


class RecoveryJournalTests(unittest.TestCase):

    def test_decision_is_atomic_and_reloadable(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(td)

            decision = wait_decision()

            journal.record_decision(
                seq=17,
                sim_time=(
                    "2026-09-25T08:15:00+00:00"
                ),
                timeframe="M15",
                evidence_fingerprint="abc123",
                decision=decision,
                api_called=True,
            )

            self.assertTrue(
                journal.path.exists()
            )

            self.assertFalse(
                journal.path
                .with_suffix(".tmp")
                .exists()
            )

            reloaded = ReplayRecoveryJournal(td)

            item = reloaded.get_decision(
                17,
                "M15",
            )

            self.assertIsNotNone(item)
            self.assertEqual(
                "WAIT",
                item["decision"]["action"],
            )
            self.assertTrue(
                item["api_called"]
            )
            self.assertFalse(
                item["memory_committed"]
            )

            # JSON itself must remain valid after
            # atomic replace.
            json.loads(
                journal.path.read_text(
                    encoding="utf-8"
                )
            )

    def test_same_decision_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(td)

            kwargs = dict(
                seq=3,
                sim_time=(
                    "2026-09-25T08:15:00+00:00"
                ),
                timeframe="M15",
                evidence_fingerprint="same",
                decision=wait_decision(),
                api_called=True,
            )

            first = journal.record_decision(
                **kwargs
            )

            second = journal.record_decision(
                **kwargs
            )

            self.assertEqual(
                first,
                second,
            )

            event = journal.get_event(3)

            self.assertEqual(
                1,
                len(event["decisions"]),
            )

    def test_conflicting_decision_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            journal = ReplayRecoveryJournal(td)

            journal.record_decision(
                seq=9,
                sim_time=(
                    "2026-09-25T08:15:00+00:00"
                ),
                timeframe="M15",
                evidence_fingerprint="first",
                decision=wait_decision(),
                api_called=True,
            )

            with self.assertRaises(
                RuntimeError
            ):
                journal.record_decision(
                    seq=9,
                    sim_time=(
                        "2026-09-25T08:15:00+00:00"
                    ),
                    timeframe="M15",
                    evidence_fingerprint="different",
                    decision=wait_decision(),
                    api_called=True,
                )

    def test_stub_calls_durable_callback_before_return(self):
        captured = []

        gateway = StubClaudeGateway()

        result = gateway.ask_until_valid(
            "prefix",
            {"timeframe": "M15"},
            on_valid_decision=(
                lambda decision:
                    captured.append(
                        decision.action
                    )
            ),
        )

        self.assertEqual(
            ["WAIT"],
            captured,
        )
        self.assertEqual(
            "WAIT",
            result.action,
        )


if __name__ == "__main__":
    unittest.main()
