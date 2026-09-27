from __future__ import annotations
import argparse
from pathlib import Path
from dotenv import load_dotenv

from waveframe.clock import ReplayClock
from waveframe.claude_gateway import ClaudeGateway
from waveframe.config import load_settings
from waveframe.logging import AuditLogger
from waveframe.replay_runtime import ReplayRuntime
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
        default="stub",
        help="stub = zero-cost integration test; live = real Anthropic API",
    )
    args = ap.parse_args()

    if args.start and not args.end:
        ap.error("--end is required with --start")

    if args.resume and args.end:
        ap.error("--end cannot be used with --resume")

    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    settings = load_settings(root)
    sim = SimulatorClient(args.sim_url)

    if args.resume:
        created = sim.restore_replay(
            args.resume
        )
        resumed = True
    else:
        created = sim.create_replay(
            args.start,
            args.end,
        )
        resumed = False

    run_id = created["run_id"]
    run_root = root / "replay_runs" / run_id
    clock = ReplayClock()
    clock.set(created["sim_now"])
    logger = AuditLogger(run_root, clock=clock)

    if args.ai == "live":
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
    print(f"AI_MODE={args.ai}")
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
        report = runtime.run_until_finished()
        print("FINISHED")
        print(f"trades_closed={report.get('trades_closed', 0)}")
        print(f"final_balance={(report.get('account') or {}).get('balance')}")
    finally:
        sim.close()


if __name__ == "__main__":
    main()
