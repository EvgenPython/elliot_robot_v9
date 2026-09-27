import tempfile
import unittest

from waveframe.claude_gateway import (
    ClaudeGateway,
    ClaudeCycleError,
    ClaudeSchemaExhausted,
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


if __name__ == "__main__":
    unittest.main()
