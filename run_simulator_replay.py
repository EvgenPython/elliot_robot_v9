from __future__ import annotations
import argparse
from pathlib import Path
from dotenv import load_dotenv

from waveframe.clock import ReplayClock
from waveframe.claude_gateway import (
    ClaudeGateway,
    ClaudeCycleError,
    ClaudeOutputTruncated,
)
from waveframe.config import load_settings
from waveframe.logging import AuditLogger
from waveframe.replay_runtime import ReplayRuntime
from waveframe.run_identity import (
    build_run_identity,
    resolve_new_run_ai,
    validate_resume_identity,
    write_run_identity,
)
from waveframe.simulator_client import SimulatorClient
from waveframe.stub_claude import StubClaudeGateway


def main():
    ap = argparse.ArgumentParser(
        description=(
            "Run Elliot Robot V9 against "
            "WaveFrame Market Simulator"
        )
    )

    source = ap.add_mutually_exclusive_group(
        required=True
    )

    source.add_argument(
        "--start",
        help="Create a new replay from this UTC timestamp.",
    )

    source.add_argument(
        "--resume",
        metavar="RUN_ID",
        help="Restore an existing simulator replay.",
    )

    ap.add_argument(
        "--end",
        help="End UTC timestamp for a new replay.",
    )

    ap.add_argument(
        "--sim-url",
        default="http://127.0.0.1:8765",
    )
    ap.add_argument(
        "--ai",
        choices=["stub", "live"],
        default=None,
        help=(
            "New run: omitted = stub. "
            "Resume: omitted = persisted run AI mode."
        ),
    )
    args = ap.parse_args()

    if args.start and not args.end:
        ap.error("--end is required with --start")

    if args.resume and args.end:
        ap.error("--end cannot be used with --resume")

    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    settings = load_settings(root)

    # --------------------------------------------------------
    # Resolve/validate identity BEFORE gateway/runtime.
    #
    # Resume validation also happens BEFORE contacting the
    # simulator, so an AI/config mismatch cannot execute or
    # consume a Claude call.
    # --------------------------------------------------------

    if args.resume:
        run_id = str(args.resume)
        run_root = (
            root
            / "replay_runs"
            / run_id
        )

        (
            ai_mode,
            run_metadata,
        ) = validate_resume_identity(
            run_id=run_id,
            run_root=run_root,
            requested_ai=args.ai,
            settings=settings,
        )

        sim = SimulatorClient(
            args.sim_url
        )

        created = sim.restore_replay(
            run_id
        )

        if str(
            created.get("run_id")
        ) != run_id:
            sim.close()
            raise RuntimeError(
                "Simulator restored unexpected run_id: "
                f"{created.get('run_id')} != {run_id}"
            )

        resumed = True

    else:
        ai_mode = resolve_new_run_ai(
            args.ai
        )

        sim = SimulatorClient(
            args.sim_url
        )

        created = sim.create_replay(
            args.start,
            args.end,
        )

        run_id = str(
            created["run_id"]
        )

        run_root = (
            root
            / "replay_runs"
            / run_id
        )

        run_metadata = build_run_identity(
            run_id=run_id,
            ai_mode=ai_mode,
            settings=settings,
        )

        try:
            write_run_identity(
                run_root,
                run_metadata,
            )

        except Exception:
            sim.close()
            raise

        resumed = False

    clock = ReplayClock()
    clock.set(created["sim_now"])
    logger = AuditLogger(run_root, clock=clock)

    if ai_mode == "live":
        c = settings.get("claude", {})
        gateway = ClaudeGateway(
            root=run_root,
            model=c.get("model", "claude-sonnet-5"),
            max_tokens=int(c.get("max_output_tokens", 2200)),
            cache_ttl=c.get("cache_ttl", "1h"),
            effort=c.get("effort", "medium"),
            logger=logger,
        )
    else:
        gateway = StubClaudeGateway(logger=logger)

    runtime = ReplayRuntime(root, run_root, sim, clock, gateway)
    print(f"RUN_ID={run_id}")
    print(f"AI_MODE={ai_mode}")
    print(f"RUN_ROOT={run_root}")
    print(f"RESUMED={resumed}")
    print(
        f"SIM_STATUS={created.get('status')}"
    )

    status = created.get("status")

    if status in {
        "READY",
        "PAUSED",
        "RUNNING",
    }:
        sim.start()

    elif status in {
        "WAITING_ACK",
        "FINISHED",
    }:
        # WAITING_ACK:
        # leave the restored pending event untouched.
        # ReplayRuntime will receive it via wait_event().
        #
        # FINISHED:
        # run_until_finished() will immediately produce
        # the final report.
        pass

    else:
        raise RuntimeError(
            "Unsupported simulator state on startup: "
            f"{status}"
        )

    try:
        try:
            report = runtime.run_until_finished()

        except ClaudeOutputTruncated as exc:
            try:
                current = sim.status()
            except Exception:
                current = {}

            print("")
            print("RUN_PAUSED_RECOVERABLE")
            print("REASON=CLAUDE_MAX_TOKENS_RECOVERY_EXHAUSTED")
            print(f"DETAIL={exc}")
            print(
                f"SIM_STATUS={current.get('status')}"
            )
            print(
                f"SIM_TIME={current.get('sim_now')}"
            )
            print(
                f"SIM_SEQ={current.get('seq')}"
            )
            print(
                "NO_TRADE_DECISION_WAS_FABRICATED=True"
            )
            print(
                "SIMULATOR_EVENT_REMAINS_RECOVERABLE=True"
            )

            raise SystemExit(2)

        except ClaudeCycleError as exc:
            try:
                current = sim.status()
            except Exception:
                current = {}

            print("")
            print("RUN_PAUSED_RECOVERABLE")
            print("REASON=CLAUDE_CYCLE_REQUIRES_OPERATOR")
            print(
                f"ERROR_TYPE={type(exc).__name__}"
            )
            print(f"DETAIL={exc}")
            print(
                f"SIM_STATUS={current.get('status')}"
            )
            print(
                f"SIM_TIME={current.get('sim_now')}"
            )
            print(
                f"SIM_SEQ={current.get('seq')}"
            )
            print(
                "NO_TRADE_DECISION_WAS_FABRICATED=True"
            )
            print(
                "SIMULATOR_EVENT_REMAINS_RECOVERABLE=True"
            )

            raise SystemExit(2)

        print("FINISHED")
        print(
            f"trades_closed="
            f"{report.get('trades_closed', 0)}"
        )
        print(
            f"final_balance="
            f"{(report.get('account') or {}).get('balance')}"
        )

    finally:
        sim.close()


if __name__ == "__main__":
    main()
