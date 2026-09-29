from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path


RUN_METADATA_SCHEMA_VERSION = 2


class RunIdentityError(RuntimeError):
    """Replay cannot be resumed with a different run identity."""
    pass


def resolve_clean_code_revision(
    root,
    *,
    runner=None,
) -> str:
    """
    Return the exact Git HEAD SHA for the running robot.

    Paid/live replay identity is valid only from a clean working tree.
    This prevents one historical run from silently mixing two code
    versions.

    No secrets or environment values are inspected or persisted.
    """

    root = Path(root).resolve()

    if runner is None:

        def runner(args):
            return subprocess.run(
                args,
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

    head_result = runner(
        [
            "git",
            "rev-parse",
            "--verify",
            "HEAD",
        ]
    )

    if (
        head_result.returncode != 0
        or not str(
            head_result.stdout
        ).strip()
    ):
        raise RunIdentityError(
            "Cannot determine Git HEAD for replay identity"
        )

    revision = str(
        head_result.stdout
    ).strip()

    if len(revision) != 40:
        raise RunIdentityError(
            "Git HEAD is not a full 40-character revision"
        )

    dirty_result = runner(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=all",
        ]
    )

    if dirty_result.returncode != 0:
        raise RunIdentityError(
            "Cannot verify Git working tree cleanliness"
        )

    dirty = str(
        dirty_result.stdout
    ).strip()

    if dirty:
        raise RunIdentityError(
            "Replay requires a clean Git working tree. "
            "Commit or discard code changes before starting/resuming."
        )

    return revision


def resolve_new_run_ai(
    requested_ai: str | None,
) -> str:
    """
    New runs remain safe-by-default:
    omitted --ai means stub.

    Resume behavior is handled separately and never defaults
    to stub behind the user's back.
    """
    if requested_ai is None:
        return "stub"

    if requested_ai not in {
        "stub",
        "live",
    }:
        raise RunIdentityError(
            f"Unsupported AI mode: {requested_ai}"
        )

    return requested_ai


def _selected_config(
    settings: dict,
) -> dict:
    """
    Only persist decision/execution-affecting configuration.

    Deliberately do NOT copy arbitrary settings or environment
    values, so API keys/secrets cannot leak into run metadata.
    """
    structure = dict(
        settings.get(
            "structure",
            {},
        )
        or {}
    )

    claude = dict(
        settings.get(
            "claude",
            {},
        )
        or {}
    )

    execution = dict(
        settings.get(
            "execution",
            {},
        )
        or {}
    )

    configured_timeframes = (
        settings.get(
            "timeframes"
        )
        or [
            "H4",
            "H1",
            "M30",
            "M15",
        ]
    )

    return {
        "symbol": str(
            settings.get("symbol")
            or "XAUUSD"
        ),

        "timeframes": [
            str(x)
            for x in configured_timeframes
        ],

        "structure": {
            "pivot_left": int(
                structure.get(
                    "pivot_left",
                    3,
                )
            ),
            "pivot_right": int(
                structure.get(
                    "pivot_right",
                    3,
                )
            ),
            "history_bars": int(
                structure.get(
                    "history_bars",
                    300,
                )
            ),
            "recent_ohlc_for_claude": int(
                structure.get(
                    "recent_ohlc_for_claude",
                    24,
                )
            ),
        },

        "claude": {
            "model": str(
                claude.get(
                    "model",
                    "claude-sonnet-5",
                )
            ),
            "max_output_tokens": int(
                claude.get(
                    "max_output_tokens",
                    2200,
                )
            ),
            "cache_ttl": str(
                claude.get(
                    "cache_ttl",
                    "1h",
                )
            ),
            "effort": str(
                claude.get(
                    "effort",
                    "medium",
                )
            ),
            "daily_rebase_hour_fp": int(
                claude.get(
                    "daily_rebase_hour_fp",
                    8,
                )
            ),
        },

        "execution": {
            "replay_enabled": bool(
                execution.get(
                    "replay_enabled",
                    True,
                )
            ),
            "risk_fraction": float(
                execution.get(
                    "risk_fraction",
                    0.0025,
                )
            ),
            "replay_magic": int(
                execution.get(
                    "replay_magic",
                    90502,
                )
            ),
        },
    }


def _canonical_json(
    value,
) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
    )


def _fingerprint(
    config: dict,
) -> str:
    return hashlib.sha256(
        _canonical_json(
            config
        ).encode(
            "utf-8"
        )
    ).hexdigest()


def build_run_identity(
    *,
    run_id: str,
    ai_mode: str,
    settings: dict,
    code_revision: str,
) -> dict:

    if ai_mode not in {
        "stub",
        "live",
    }:
        raise RunIdentityError(
            f"Unsupported AI mode: {ai_mode}"
        )

    identity_config = (
        _selected_config(
            settings
        )
    )

    return {
        "schema_version":
            RUN_METADATA_SCHEMA_VERSION,

        "run_id":
            str(run_id),

        "ai_mode":
            str(ai_mode),

        "model":
            identity_config[
                "claude"
            ][
                "model"
            ],

        "symbol":
            identity_config[
                "symbol"
            ],

        "risk_fraction":
            identity_config[
                "execution"
            ][
                "risk_fraction"
            ],

        "code_revision":
            str(
                code_revision
            ),

        "config_fingerprint":
            _fingerprint(
                identity_config
            ),

        "identity_config":
            identity_config,
    }


def metadata_path(
    run_root,
) -> Path:
    return (
        Path(run_root)
        / "run_metadata.json"
    )


def write_run_identity(
    run_root,
    metadata: dict,
) -> Path:
    """
    Atomic + idempotent.

    Same identity:
        safe no-op.

    Different identity at same run root:
        fail closed.
    """
    path = metadata_path(
        run_root
    )

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    if path.exists():
        existing = load_run_identity(
            run_root
        )

        if existing == metadata:
            return path

        raise RunIdentityError(
            "Conflicting run metadata already exists: "
            f"{path}"
        )

    tmp = path.with_name(
        path.name + ".tmp"
    )

    payload = (
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    try:
        with tmp.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                payload
            )
            handle.flush()
            os.fsync(
                handle.fileno()
            )

        os.replace(
            tmp,
            path,
        )

    finally:
        if tmp.exists():
            tmp.unlink()

    return path


def load_run_identity(
    run_root,
    *,
    expected_run_id: str | None = None,
) -> dict:

    path = metadata_path(
        run_root
    )

    if not path.exists():
        raise RunIdentityError(
            "Run has no run_metadata.json. "
            "Legacy replay runs cannot be resumed safely: "
            f"{path}"
        )

    try:
        metadata = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:
        raise RunIdentityError(
            "Run metadata is unreadable or corrupt: "
            f"{path}"
        ) from exc

    if not isinstance(
        metadata,
        dict,
    ):
        raise RunIdentityError(
            "Run metadata root must be an object"
        )

    required = {
        "schema_version",
        "run_id",
        "ai_mode",
        "model",
        "symbol",
        "risk_fraction",
        "code_revision",
        "config_fingerprint",
        "identity_config",
    }

    missing = sorted(
        required
        - set(metadata)
    )

    if missing:
        raise RunIdentityError(
            "Run metadata missing fields: "
            + ", ".join(
                missing
            )
        )

    if int(
        metadata[
            "schema_version"
        ]
    ) != RUN_METADATA_SCHEMA_VERSION:
        raise RunIdentityError(
            "Unsupported run metadata schema: "
            f"{metadata['schema_version']}"
        )

    if (
        expected_run_id is not None
        and str(
            metadata["run_id"]
        )
        != str(
            expected_run_id
        )
    ):
        raise RunIdentityError(
            "Run ID mismatch in metadata: "
            f"{metadata['run_id']} != "
            f"{expected_run_id}"
        )

    ai_mode = str(
        metadata[
            "ai_mode"
        ]
    )

    if ai_mode not in {
        "stub",
        "live",
    }:
        raise RunIdentityError(
            "Invalid persisted AI mode: "
            f"{ai_mode}"
        )

    calculated = _fingerprint(
        metadata[
            "identity_config"
        ]
    )

    if (
        calculated
        != metadata[
            "config_fingerprint"
        ]
    ):
        raise RunIdentityError(
            "Run metadata fingerprint is corrupt"
        )

    return metadata


def validate_resume_identity(
    *,
    run_id: str,
    run_root,
    requested_ai: str | None,
    settings: dict,
    code_revision: str,
) -> tuple[str, dict]:
    """
    Validate BEFORE simulator restore / gateway creation / runtime.

    Omitted --ai on resume inherits persisted AI mode.

    Explicit AI mismatch or config mismatch fails closed.
    """
    persisted = load_run_identity(
        run_root,
        expected_run_id=
            run_id,
    )

    persisted_ai = str(
        persisted[
            "ai_mode"
        ]
    )

    if (
        requested_ai is not None
        and requested_ai
        != persisted_ai
    ):
        raise RunIdentityError(
            "Resume AI mode mismatch: "
            f"run={persisted_ai}, "
            f"requested={requested_ai}"
        )

    current = build_run_identity(
        run_id=run_id,
        ai_mode=persisted_ai,
        settings=settings,
        code_revision=code_revision,
    )

    for field in (
        "model",
        "symbol",
        "risk_fraction",
        "code_revision",
        "config_fingerprint",
    ):
        if (
            current[field]
            != persisted[field]
        ):
            raise RunIdentityError(
                "Resume configuration mismatch "
                f"for {field}: "
                f"run={persisted[field]!r}, "
                f"current={current[field]!r}"
            )

    return (
        persisted_ai,
        persisted,
    )
