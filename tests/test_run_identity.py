import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from waveframe.run_identity import (
    RunIdentityError,
    build_run_identity,
    load_run_identity,
    resolve_new_run_ai,
    validate_resume_identity,
    write_run_identity,
)


def base_settings():
    return {
        "symbol": "XAUUSD",
        "timeframes": [
            "H4",
            "H1",
            "M30",
            "M15",
        ],
        "structure": {
            "pivot_left": 3,
            "pivot_right": 3,
            "history_bars": 300,
            "recent_ohlc_for_claude": 24,
        },
        "claude": {
            "model": "claude-sonnet-5",
            "max_output_tokens": 2200,
            "cache_ttl": "1h",
            "effort": "medium",
            "daily_rebase_hour_fp": 8,
        },
        "execution": {
            "replay_enabled": True,
            "risk_fraction": 0.0025,
            "replay_magic": 90502,
        },
        "logging": {
            "heartbeat_seconds": 300,
        },
    }


class RunIdentityTests(
    unittest.TestCase
):

    def test_new_run_defaults_to_stub(self):
        self.assertEqual(
            resolve_new_run_ai(None),
            "stub",
        )

        self.assertEqual(
            resolve_new_run_ai("live"),
            "live",
        )

    def test_metadata_write_is_atomic_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "run-1"
            )

            metadata = build_run_identity(
                run_id="run-1",
                ai_mode="live",
                settings=base_settings(),
            )

            path = write_run_identity(
                root,
                metadata,
            )

            self.assertTrue(
                path.exists()
            )

            self.assertFalse(
                path.with_name(
                    path.name + ".tmp"
                ).exists()
            )

            write_run_identity(
                root,
                metadata,
            )

            loaded = load_run_identity(
                root,
                expected_run_id="run-1",
            )

            self.assertEqual(
                loaded,
                metadata,
            )

    def test_conflicting_metadata_write_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "run-2"
            )

            live = build_run_identity(
                run_id="run-2",
                ai_mode="live",
                settings=base_settings(),
            )

            stub = build_run_identity(
                run_id="run-2",
                ai_mode="stub",
                settings=base_settings(),
            )

            write_run_identity(
                root,
                live,
            )

            with self.assertRaises(
                RunIdentityError
            ):
                write_run_identity(
                    root,
                    stub,
                )

    def test_resume_without_ai_reuses_persisted_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "run-live"
            )

            settings = (
                base_settings()
            )

            metadata = build_run_identity(
                run_id="run-live",
                ai_mode="live",
                settings=settings,
            )

            write_run_identity(
                root,
                metadata,
            )

            ai_mode, loaded = (
                validate_resume_identity(
                    run_id="run-live",
                    run_root=root,
                    requested_ai=None,
                    settings=settings,
                )
            )

            self.assertEqual(
                ai_mode,
                "live",
            )

            self.assertEqual(
                loaded["ai_mode"],
                "live",
            )

    def test_explicit_ai_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "run-ai"
            )

            settings = (
                base_settings()
            )

            write_run_identity(
                root,
                build_run_identity(
                    run_id="run-ai",
                    ai_mode="live",
                    settings=settings,
                ),
            )

            with self.assertRaisesRegex(
                RunIdentityError,
                "AI mode mismatch",
            ):
                validate_resume_identity(
                    run_id="run-ai",
                    run_root=root,
                    requested_ai="stub",
                    settings=settings,
                )

    def test_config_change_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "run-config"
            )

            original = (
                base_settings()
            )

            write_run_identity(
                root,
                build_run_identity(
                    run_id="run-config",
                    ai_mode="live",
                    settings=original,
                ),
            )

            changed = deepcopy(
                original
            )

            changed[
                "execution"
            ][
                "risk_fraction"
            ] = 0.005

            with self.assertRaisesRegex(
                RunIdentityError,
                "configuration mismatch",
            ):
                validate_resume_identity(
                    run_id="run-config",
                    run_root=root,
                    requested_ai=None,
                    settings=changed,
                )

    def test_missing_metadata_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "legacy-run"
            )

            root.mkdir(
                parents=True
            )

            with self.assertRaisesRegex(
                RunIdentityError,
                "Legacy replay runs",
            ):
                validate_resume_identity(
                    run_id="legacy-run",
                    run_root=root,
                    requested_ai=None,
                    settings=base_settings(),
                )

    def test_nonidentity_logging_change_is_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = (
                Path(tmp)
                / "run-logging"
            )

            original = (
                base_settings()
            )

            write_run_identity(
                root,
                build_run_identity(
                    run_id="run-logging",
                    ai_mode="live",
                    settings=original,
                ),
            )

            changed = deepcopy(
                original
            )

            changed[
                "logging"
            ][
                "heartbeat_seconds"
            ] = 999

            ai_mode, _ = (
                validate_resume_identity(
                    run_id="run-logging",
                    run_root=root,
                    requested_ai=None,
                    settings=changed,
                )
            )

            self.assertEqual(
                ai_mode,
                "live",
            )

    def test_metadata_does_not_persist_unselected_secrets(self):
        settings = (
            base_settings()
        )

        fake_api_key = (
            "sk-"
            + "ant-"
            + "this-must-never-be-persisted"
        )

        settings[
            "claude"
        ][
            "api_key"
        ] = fake_api_key

        settings[
            "some_secret"
        ] = "password-value"

        metadata = build_run_identity(
            run_id="secret-test",
            ai_mode="live",
            settings=settings,
        )

        raw = json.dumps(
            metadata,
            sort_keys=True,
        )

        self.assertNotIn(
            fake_api_key,
            raw,
        )

        self.assertNotIn(
            "password-value",
            raw,
        )


if __name__ == "__main__":
    unittest.main()
