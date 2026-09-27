import json
import tempfile
import unittest

from waveframe.claude_gateway import (
    ClaudeGateway,
    ClaudeCycleError,
    ClaudeSchemaExhausted,
    ClaudeOutputTruncated,
)


class RetryBoundTests(unittest.TestCase):

    def make_gateway(self, transport):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        return ClaudeGateway(
            root=tmp.name,
            transport=transport,
            max_transport_failures=3,
            max_schema_repairs=1,
            max_elliott_revisions=2,
        )

    def test_fatal_401_is_never_retried(self):
        calls = {"n": 0}

        def transport(_stable, _dynamic):
            calls["n"] += 1
            raise RuntimeError("401 authentication_error invalid x-api-key")

        gateway = self.make_gateway(transport)

        with self.assertRaises(RuntimeError):
            gateway.ask_until_valid(
                "stable-prefix",
                {},
                sleep=lambda _seconds: None,
            )

        self.assertEqual(calls["n"], 1)

    def test_bad_schema_cannot_loop_forever(self):
        calls = {"n": 0}

        def transport(_stable, _dynamic):
            calls["n"] += 1
            return {
                "text": "{}",
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
                "model": "claude-sonnet-5",
            }

        gateway = self.make_gateway(transport)

        with self.assertRaises(ClaudeSchemaExhausted):
            gateway.ask_until_valid(
                "stable-prefix",
                {},
                sleep=lambda _seconds: None,
            )

        self.assertEqual(calls["n"], 2)

    def test_transient_transport_failure_is_bounded(self):
        calls = {"n": 0}

        def transport(_stable, _dynamic):
            calls["n"] += 1
            raise RuntimeError("temporary network failure")

        gateway = self.make_gateway(transport)

        with self.assertRaises(ClaudeCycleError):
            gateway.ask_until_valid(
                "stable-prefix",
                {},
                sleep=lambda _seconds: None,
            )

        self.assertEqual(calls["n"], 4)



    def _valid_wait_payload(self):
        return {
            "action": "WAIT",
            "primary_count": {
                "degree": "Minor",
                "direction": "neutral",
                "current_wave": "unclear",
                "legs": [],
                "invalidation": None,
                "summary": "No confirmed setup.",
            },
            "alternate_count": None,
            "structure_assessment": "Neutral.",
            "support_resistance_assessment": "No material change.",
            "channel_assessment": "No clean channel.",
            "pattern_assessment": [],
            "evidence_disagreements": [],
            "watch_conditions": [],
            "entry": None,
            "stop": None,
            "target": None,
            "confidence": "low",
            "rationale_brief": "Wait for confirmation.",
            "evidence_interpretation": "Evidence remains unresolved.",
        }

    def test_max_tokens_gets_one_compact_recovery(self):
        calls = []

        def transport(_stable, dynamic):
            calls.append(dynamic)

            if len(calls) == 1:
                return {
                    "text": '{"action":"WAIT"',
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 5000,
                    },
                    "model": "claude-sonnet-5",
                    "stop_reason": "max_tokens",
                }

            return {
                "text": json.dumps(
                    self._valid_wait_payload()
                ),
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 500,
                },
                "model": "claude-sonnet-5",
                "stop_reason": "end_turn",
            }

        gateway = ClaudeGateway(
            root=tempfile.mkdtemp(),
            transport=transport,
            max_tokens=5000,
            max_truncation_retries=1,
            truncation_recovery_max_tokens=7000,
            truncation_recovery_effort="low",
        )

        decision = gateway.ask_until_valid(
            "stable-prefix",
            {"x": 1},
            sleep=lambda _seconds: None,
        )

        self.assertEqual(
            decision.action,
            "WAIT",
        )

        self.assertEqual(
            len(calls),
            2,
        )

        self.assertIn(
            "OUTPUT_LIMIT_RECOVERY",
            calls[1],
        )

    def test_repeated_max_tokens_is_bounded(self):
        calls = {"n": 0}

        def transport(_stable, _dynamic):
            calls["n"] += 1

            return {
                "text": '{"action":"WAIT"',
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 7000,
                },
                "model": "claude-sonnet-5",
                "stop_reason": "max_tokens",
            }

        gateway = self.make_gateway(
            transport
        )

        gateway.max_tokens = 5000
        gateway.max_truncation_retries = 1
        gateway.truncation_recovery_max_tokens = 7000

        with self.assertRaises(
            ClaudeOutputTruncated
        ):
            gateway.ask_until_valid(
                "stable-prefix",
                {},
                sleep=lambda _seconds: None,
            )

        self.assertEqual(
            calls["n"],
            2,
        )

    def test_sdk_recovery_uses_7000_and_low_effort(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        gateway = ClaudeGateway(
            root=tmp.name,
            transport=None,
            max_tokens=5000,
            effort="medium",
            max_truncation_retries=1,
            truncation_recovery_max_tokens=7000,
            truncation_recovery_effort="low",
        )

        calls = []

        def fake_sdk(
            _stable,
            _dynamic,
            *,
            max_tokens=None,
            effort=None,
        ):
            calls.append(
                {
                    "max_tokens": max_tokens,
                    "effort": effort,
                }
            )

            if len(calls) == 1:
                return {
                    "text": '{"action":"WAIT"',
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 5000,
                    },
                    "model": "claude-sonnet-5",
                    "stop_reason": "max_tokens",
                    "structured_output": True,
                }

            return {
                "text": json.dumps(
                    self._valid_wait_payload()
                ),
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 500,
                },
                "model": "claude-sonnet-5",
                "stop_reason": "end_turn",
                "structured_output": True,
            }

        gateway._sdk_transport = fake_sdk

        decision = gateway.ask_until_valid(
            "stable-prefix",
            {"x": 1},
            sleep=lambda _seconds: None,
        )

        self.assertEqual(
            decision.action,
            "WAIT",
        )

        self.assertEqual(
            calls,
            [
                {
                    "max_tokens": 5000,
                    "effort": "medium",
                },
                {
                    "max_tokens": 7000,
                    "effort": "low",
                },
            ],
        )

if __name__ == "__main__":
    unittest.main()
