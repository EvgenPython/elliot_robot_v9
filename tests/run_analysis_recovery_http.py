from __future__ import annotations

import sys
import time
import uuid
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


from waveframe.clock import (
    ReplayClock,
)
from waveframe.replay_runtime import (
    ANALYSIS_ORDER,
    ReplayRuntime,
)
from waveframe.simulator_client import (
    SimulatorClient,
)
from waveframe.stub_claude import (
    StubClaudeGateway,
)


SIM_URL = (
    "http://127.0.0.1:8765"
)

START = (
    "2026-09-25T08:55:00+00:00"
)

END = (
    "2026-09-25T09:05:00+00:00"
)


def check(
    condition,
    message,
):
    if not condition:
        raise AssertionError(
            message
        )


def wait_event(
    sim,
    after_seq=0,
    timeout=10.0,
):
    deadline = (
        time.monotonic()
        + timeout
    )

    while (
        time.monotonic()
        < deadline
    ):
        event = sim.poll_event(
            after_seq
        )

        if event is not None:
            return event

        status = sim.status()

        if status.get(
            "status"
        ) in {
            "ERROR",
            "STOPPED",
        }:
            raise RuntimeError(
                f"Simulator stopped: "
                f"{status}"
            )

        time.sleep(0.01)

    raise TimeoutError(
        "No replay event "
        f"after seq={after_seq}"
    )


class CrashExecutor:
    def __init__(self):
        self.calls = 0

    def execute_decisions(
        self,
        decisions,
    ):
        self.calls += 1

        raise RuntimeError(
            "INTENTIONAL_AFTER_"
            "ANALYSIS_CRASH"
        )


class ForbiddenGateway:
    api_called = False

    def __init__(self):
        self.logger = None
        self.calls = 0

    def ask_until_valid(
        self,
        *args,
        **kwargs,
    ):
        self.calls += 1

        raise AssertionError(
            "Gateway must not be "
            "called after complete "
            "analysis recovery"
        )


class CountingSimulator:
    def __init__(
        self,
        inner,
    ):
        self.inner = inner
        self.rates_calls = 0

    def __getattr__(
        self,
        name,
    ):
        return getattr(
            self.inner,
            name,
        )

    def rates(
        self,
        *args,
        **kwargs,
    ):
        self.rates_calls += 1

        return self.inner.rates(
            *args,
            **kwargs,
        )


def main():

    run_id = (
        "analysis_recovery_"
        + uuid.uuid4().hex[:10]
    )

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

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

    event = wait_event(
        sim,
        0,
    )

    seq = int(
        event["seq"]
    )

    closed_analysis = [
        tf
        for tf in ANALYSIS_ORDER
        if tf in (
            event.get(
                "closed_timeframes"
            )
            or []
        )
    ]

    check(
        len(closed_analysis)
        >= 2,
        (
            "Expected multi-timeframe "
            "checkpoint, got "
            f"{event}"
        ),
    )

    # --------------------------------------------------------
    # Attempt #1:
    # finish all analysis, then crash inside execution.
    # --------------------------------------------------------

    clock1 = ReplayClock()

    clock1.set(
        event["sim_time"]
    )

    runtime1 = ReplayRuntime(
        ROOT,
        run_root,
        sim,
        clock1,
        StubClaudeGateway(),
    )

    crash_executor = (
        CrashExecutor()
    )

    runtime1.executor = (
        crash_executor
    )

    crashed = False

    try:
        runtime1.process_event(
            event
        )

    except RuntimeError as error:

        check(
            "INTENTIONAL_AFTER_"
            "ANALYSIS_CRASH"
            in str(error),
            (
                "Unexpected first "
                f"attempt error: {error}"
            ),
        )

        crashed = True

    check(
        crashed,
        "Intentional crash missing",
    )

    state1 = (
        runtime1.recovery
        .get_event(seq)
    )

    check(
        state1[
            "analysis_complete"
        ],
        "Analysis was not sealed",
    )

    check(
        state1[
            "execution_started"
        ],
        "Execution did not start",
    )

    check(
        not state1[
            "execution_done"
        ],
        (
            "Execution should be "
            "incomplete after crash"
        ),
    )

    for tf in closed_analysis:
        check(
            tf in state1[
                "timeframe_results"
            ],
            (
                "Missing durable "
                "timeframe result: "
                f"{tf}"
            ),
        )

    analysis_before = (
        state1[
            "analysis_summary"
        ]
    )

    sim.close()

    # --------------------------------------------------------
    # Attempt #2:
    # restore same simulator event.
    #
    # ZERO rates/evidence recomputation.
    # ZERO gateway calls.
    # Exact analysis plan is reused.
    # --------------------------------------------------------

    raw_sim2 = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    restored = (
        raw_sim2.restore_replay(
            run_id
        )
    )

    check(
        restored.get("status")
        == "WAITING_ACK",
        (
            "Expected WAITING_ACK "
            f"after restore: {restored}"
        ),
    )

    event2 = restored.get(
        "pending_event"
    )

    check(
        event2 is not None,
        "Pending event missing",
    )

    check(
        int(
            event2["seq"]
        )
        == seq,
        "Restored wrong seq",
    )

    counted_sim = (
        CountingSimulator(
            raw_sim2
        )
    )

    forbidden = (
        ForbiddenGateway()
    )

    clock2 = ReplayClock()

    clock2.set(
        event2["sim_time"]
    )

    runtime2 = ReplayRuntime(
        ROOT,
        run_root,
        counted_sim,
        clock2,
        forbidden,
    )

    result = (
        runtime2.process_event(
            event2
        )
    )

    check(
        forbidden.calls == 0,
        (
            "Gateway called during "
            "complete analysis recovery"
        ),
    )

    check(
        counted_sim.rates_calls
        == 0,
        (
            "Historical rates were "
            "recomputed despite a "
            "complete durable plan"
        ),
    )

    check(
        result[
            "analysis_recovery_mode"
        ]
        == "REUSED_COMPLETE",
        (
            "Wrong analysis recovery "
            f"mode: {result}"
        ),
    )

    check(
        result[
            "execution_recovery_mode"
        ]
        == "RESUMED_INCOMPLETE",
        (
            "Execution was not resumed "
            f"from crash state: {result}"
        ),
    )

    state2 = (
        runtime2.recovery
        .get_event(seq)
    )

    check(
        state2[
            "analysis_summary"
        ]
        == analysis_before,
        (
            "Immutable analysis plan "
            "changed after restart"
        ),
    )

    check(
        state2[
            "execution_done"
        ],
        "Recovered execution not done",
    )

    check(
        state2[
            "ack_done"
        ],
        "Recovered event not ACKed",
    )

    raw_sim2.close()

    print(
        "HTTP_MULTI_TF_ANALYSIS_RECOVERY=PASS"
    )

    print(
        "ANALYSIS_TIMEFRAMES="
        + ",".join(
            closed_analysis
        )
    )

    print(
        "SECOND_ATTEMPT_GATEWAY_CALLS=0"
    )

    print(
        "SECOND_ATTEMPT_RATES_CALLS=0"
    )

    print(
        "ANTHROPIC_COST_USD=0"
    )


if __name__ == "__main__":
    main()
