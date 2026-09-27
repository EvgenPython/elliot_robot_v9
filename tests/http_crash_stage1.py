from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parents[1]
)

sys.path.insert(
    0,
    str(ROOT),
)


from waveframe.config import load_settings
from waveframe.models import (
    ClaudeDecision,
    ElliottCount,
)
from waveframe.recovery import (
    ReplayRecoveryJournal,
)
from waveframe.replay_execution import (
    ReplayExecutor,
)
from waveframe.simulator_client import (
    SimulatorClient,
)


SIM_URL = "http://127.0.0.1:8765"

START = (
    "2026-09-25T08:00:00+00:00"
)

END = (
    "2026-09-25T09:00:00+00:00"
)


class SilentLogger:
    def event(
        self,
        category,
        event,
        data,
    ):
        pass


def ready_long(
    entry,
    stop,
    target,
):
    count = ElliottCount(
        degree="M5",
        direction="bullish",
        current_wave="test",
        legs=[],
        invalidation=stop,
        summary=(
            "Zero-cost HTTP crash test."
        ),
    )

    return ClaudeDecision(
        action="READY_LONG",
        primary_count=count,
        alternate_count=None,
        structure_assessment="test",
        support_resistance_assessment=
            "test",
        channel_assessment="test",
        pattern_assessment=[],
        evidence_disagreements=[],
        watch_conditions=[],
        entry=entry,
        stop=stop,
        target=target,
        confidence="medium",
        rationale_brief=(
            "Deterministic simulator test."
        ),
        evidence_interpretation=(
            "No Anthropic call."
        ),
    )


def main():
    run_id = sys.argv[1]

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

    settings = load_settings(
        ROOT
    )

    settings.setdefault(
        "execution",
        {},
    )["replay_enabled"] = True

    symbol = str(
        settings.get("symbol")
        or "XAUUSD"
    )

    logger = SilentLogger()

    sim = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    sim.create_replay(
        START,
        END,
        run_id=run_id,
    )

    sim.start()

    event = sim.wait_event(
        0,
        poll_seconds=0.01,
    )

    if event is None:
        raise RuntimeError(
            "No first event"
        )

    seq = int(
        event["seq"]
    )

    sim.analysis_started(
        seq,
        cycle_id=f"crash-{seq}",
    )

    tick = sim.symbol_info_tick(
        symbol
    )

    ask = float(
        tick["ask"]
    )

    entry = round(
        ask + 10.0,
        2,
    )

    stop = round(
        entry - 2.0,
        2,
    )

    target = round(
        entry + 4.0,
        2,
    )

    decision = ready_long(
        entry,
        stop,
        target,
    )

    journal = ReplayRecoveryJournal(
        run_root,
        logger=logger,
    )

    journal.ensure_event(
        seq,
        event["sim_time"],
    )

    journal.record_decision(
        seq=seq,
        sim_time=event["sim_time"],
        timeframe="M5",
        evidence_fingerprint=
            "http-os-crash-fp",
        decision=decision,
        api_called=False,
    )

    journal.mark_memory_committed(
        seq,
        "M5",
    )

    journal.mark_execution_started(
        seq
    )

    executor = ReplayExecutor(
        simulator=sim,
        logger=logger,
        symbol=symbol,
        settings=settings,
    )

    results = (
        executor.execute_decisions(
            [
                {
                    "timeframe": "M5",
                    "decision": decision,
                }
            ]
        )
    )

    accepted = [
        x
        for x in results
        if x.get("status")
        in {
            "ORDER_ACCEPTED",
            "ORDER_ACCEPTED_RECONCILED",
        }
    ]

    if len(accepted) != 1:
        raise RuntimeError(
            f"Order was not accepted: {results}"
        )

    orders = sim.orders_get(
        symbol
    )

    if len(orders) != 1:
        raise RuntimeError(
            "Expected exactly one pending "
            f"order before crash, got {orders}"
        )

    context = {
        "run_id": run_id,
        "seq": seq,
        "sim_time":
            event["sim_time"],
        "timeframe": "M5",
        "decision":
            decision.model_dump(
                mode="json"
            ),
    }

    (
        run_root
        / "http_crash_context.json"
    ).write_text(
        json.dumps(
            context,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Deliberately bypass finally/normal cleanup.
    #
    # Broker state has already been persisted by simulator API.
    # Journal has execution_started=True but execution_done=False.
    # ACK was never sent.
    os._exit(91)


if __name__ == "__main__":
    main()
