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
    ap = argparse.ArgumentParser(description="Run Elliot Robot V9 against WaveFrame Market Simulator")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--sim-url", default="http://127.0.0.1:8765")
    ap.add_argument(
        "--ai",
        choices=["stub", "live"],
        default="stub",
        help="stub = zero-cost integration test; live = real Anthropic API",
    )
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    load_dotenv(root / ".env")
    settings = load_settings(root)
    sim = SimulatorClient(args.sim_url)
    created = sim.create_replay(args.start, args.end)
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
            logger=logger,
        )
    else:
        gateway = StubClaudeGateway(logger=logger)

    runtime = ReplayRuntime(root, run_root, sim, clock, gateway)
    print(f"RUN_ID={run_id}")
    print(f"AI_MODE={args.ai}")
    print(f"RUN_ROOT={run_root}")
    sim.start()
    try:
        report = runtime.run_until_finished()
        print("FINISHED")
        print(f"trades_closed={report.get('trades_closed', 0)}")
        print(f"final_balance={(report.get('account') or {}).get('balance')}")
    finally:
        sim.close()


if __name__ == "__main__":
    main()
