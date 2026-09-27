from __future__ import annotations

import json
import re
import subprocess
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


from waveframe.clock import ReplayClock
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
from waveframe.replay_runtime import (
    ReplayRuntime,
)
from waveframe.simulator_client import (
    SimulatorClient,
)
from waveframe.stub_claude import (
    StubClaudeGateway,
)


SIM_URL = "http://127.0.0.1:8765"

START = (
    "2026-09-25T08:00:00+00:00"
)

END = (
    "2026-09-25T09:00:00+00:00"
)


SETTINGS = load_settings(
    ROOT
)

SETTINGS.setdefault(
    "execution",
    {},
)["replay_enabled"] = True

SYMBOL = str(
    SETTINGS.get("symbol")
    or "XAUUSD"
)


class SilentLogger:
    def event(
        self,
        category,
        event,
        data,
    ):
        pass


LOGGER = SilentLogger()


def check(
    condition,
    message,
):
    if not condition:
        raise AssertionError(
            message
        )


def unique_run(
    name,
):
    return (
        "final_stability_"
        + name
        + "_"
        + uuid.uuid4().hex[:8]
    )


def wait_event(
    sim,
    after_seq,
    timeout=5.0,
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

        if status.get("status") in {
            "ERROR",
            "STOPPED",
        }:
            raise RuntimeError(
                f"Simulator stopped: {status}"
            )

        time.sleep(0.01)

    raise TimeoutError(
        "Timed out waiting for "
        f"event after seq={after_seq}; "
        f"status={sim.status()}"
    )


def create_first_event(
    run_id,
    *,
    analysis_started=True,
):
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

    if analysis_started:
        sim.analysis_started(
            int(event["seq"]),
            cycle_id=(
                f"test-{event['seq']}"
            ),
        )

    return sim, event


def count(
    timeframe,
    direction,
    summary,
):
    return ElliottCount(
        degree=timeframe,
        direction=direction,
        current_wave="test",
        legs=[],
        invalidation=None,
        summary=summary,
    )


def wait_decision(
    timeframe="M5",
):
    return ClaudeDecision(
        action="WAIT",
        primary_count=count(
            timeframe,
            "neutral",
            "HTTP stability WAIT",
        ),
        alternate_count=None,
        structure_assessment="test",
        support_resistance_assessment=
            "test",
        channel_assessment="test",
        pattern_assessment=[],
        evidence_disagreements=[],
        watch_conditions=[],
        entry=None,
        stop=None,
        target=None,
        confidence="low",
        rationale_brief=(
            "Deterministic zero-cost test."
        ),
        evidence_interpretation=(
            "No Anthropic call."
        ),
    )


def ready_long(
    entry,
    stop,
    target,
    timeframe="M5",
):
    primary = count(
        timeframe,
        "bullish",
        "HTTP stability READY",
    )

    primary.invalidation = stop

    return ClaudeDecision(
        action="READY_LONG",
        primary_count=primary,
        alternate_count=None,
        structure_assessment="test",
        support_resistance_assessment=
            "test",
        channel_assessment="test",
        pattern_assessment=[],
        evidence_disagreements=[],
        watch_conditions=[],
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        confidence="medium",
        rationale_brief=(
            "Deterministic zero-cost test."
        ),
        evidence_interpretation=(
            "No Anthropic call."
        ),
    )


def executor_for(
    sim,
):
    return ReplayExecutor(
        simulator=sim,
        logger=LOGGER,
        symbol=SYMBOL,
        settings=SETTINGS,
    )


def runtime_shell(
    sim,
    run_root,
):
    rt = ReplayRuntime.__new__(
        ReplayRuntime
    )

    rt.simulator = sim
    rt.logger = LOGGER

    rt.recovery = (
        ReplayRecoveryJournal(
            run_root,
            logger=LOGGER,
        )
    )

    rt.executor = executor_for(
        sim
    )

    return rt


def prepare_journal(
    run_root,
    event,
    decision,
    timeframe="M5",
    fingerprint="http-test",
):
    seq = int(
        event["seq"]
    )

    journal = ReplayRecoveryJournal(
        run_root,
        logger=LOGGER,
    )

    journal.ensure_event(
        seq,
        event["sim_time"],
    )

    journal.record_decision(
        seq=seq,
        sim_time=event["sim_time"],
        timeframe=timeframe,
        evidence_fingerprint=
            fingerprint,
        decision=decision,
        api_called=False,
    )

    journal.mark_memory_committed(
        seq,
        timeframe,
    )

    return journal


# ============================================================
# Scenario 1
# Real OS process dies AFTER broker accepted pending
# but BEFORE execution_done and ACK.
# ============================================================

def test_os_crash_after_pending_send():
    run_id = unique_run(
        "os_pending"
    )

    worker = (
        ROOT
        / "tests"
        / "http_crash_stage1.py"
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(worker),
            run_id,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )

    check(
        proc.returncode == 91,
        (
            "Crash worker did not reach "
            "intentional os._exit(91).\n"
            f"rc={proc.returncode}\n"
            f"stdout={proc.stdout}\n"
            f"stderr={proc.stderr}"
        ),
    )

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

    context = json.loads(
        (
            run_root
            / "http_crash_context.json"
        ).read_text(
            encoding="utf-8"
        )
    )

    seq = int(
        context["seq"]
    )

    decision = (
        ClaudeDecision
        .model_validate(
            context["decision"]
        )
    )

    sim = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    restored = sim.restore_replay(
        run_id
    )

    check(
        restored.get("status")
        == "WAITING_ACK",
        (
            "Expected WAITING_ACK "
            f"after restore, got {restored}"
        ),
    )

    check(
        int(
            restored[
                "pending_event"
            ]["seq"]
        )
        == seq,
        "Restored wrong pending seq",
    )

    rt = runtime_shell(
        sim,
        run_root,
    )

    results, mode = (
        rt._execute_with_recovery(
            seq=seq,
            execution_decisions=[
                {
                    "timeframe": "M5",
                    "decision": decision,
                }
            ],
        )
    )

    check(
        mode == "RESUMED_INCOMPLETE",
        f"Unexpected mode: {mode}",
    )

    orders = sim.orders_get(
        SYMBOL
    )

    positions = sim.positions_get(
        SYMBOL
    )

    check(
        len(orders) == 1,
        (
            "Pending order duplicated or "
            f"lost after recovery: {orders}"
        ),
    )

    check(
        len(positions) == 0,
        (
            "Unexpected position before "
            f"ACK: {positions}"
        ),
    )

    check(
        len(orders) + len(positions)
        == 1,
        "More than one exposure exists",
    )

    event_state = (
        rt.recovery
        .get_event(seq)
    )

    check(
        event_state[
            "execution_done"
        ],
        "execution_done not persisted",
    )

    rt._ack_with_recovery(
        seq=seq,
        claude_called=False,
        decision_text=
            "M5:READY_LONG",
    )

    check(
        rt.recovery
        .get_event(seq)
        ["ack_done"],
        "ACK was not persisted",
    )

    next_event = wait_event(
        sim,
        seq,
    )

    check(
        int(next_event["seq"])
        > seq,
        "Replay did not progress after recovery",
    )

    # Release next event before next scenario.
    sim.ack(
        int(next_event["seq"]),
        claude_called=False,
        cycle_id="cleanup",
        decision="CLEANUP",
    )

    sim.close()

    print(
        "HTTP_OS_CRASH_PENDING=PASS"
    )


# ============================================================
# Scenario 2
# Market order accepted, robot dies before execution_done.
# Restart sees existing position and must NOT duplicate it.
# ============================================================

def test_market_position_restart():
    run_id = unique_run(
        "market_position"
    )

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

    sim, event = create_first_event(
        run_id
    )

    seq = int(
        event["seq"]
    )

    tick = sim.symbol_info_tick(
        SYMBOL
    )

    ask = round(
        float(tick["ask"]),
        2,
    )

    decision = ready_long(
        entry=ask,
        stop=round(
            ask - 2.0,
            2,
        ),
        target=round(
            ask + 4.0,
            2,
        ),
    )

    journal = prepare_journal(
        run_root,
        event,
        decision,
        fingerprint=
            "market-position-fp",
    )

    journal.mark_execution_started(
        seq
    )

    executor = executor_for(
        sim
    )

    first = (
        executor.execute_decisions(
            [
                {
                    "timeframe": "M5",
                    "decision": decision,
                }
            ]
        )
    )

    check(
        any(
            x.get("status")
            in {
                "ORDER_ACCEPTED",
                "ORDER_ACCEPTED_RECONCILED",
            }
            for x in first
        ),
        f"Market order not accepted: {first}",
    )

    check(
        len(
            sim.positions_get(
                SYMBOL
            )
        )
        == 1,
        "Expected one open position",
    )

    sim.close()

    sim2 = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    sim2.restore_replay(
        run_id
    )

    rt = runtime_shell(
        sim2,
        run_root,
    )

    recovered, mode = (
        rt._execute_with_recovery(
            seq=seq,
            execution_decisions=[
                {
                    "timeframe": "M5",
                    "decision": decision,
                }
            ],
        )
    )

    check(
        mode == "RESUMED_INCOMPLETE",
        f"Unexpected mode {mode}",
    )

    check(
        len(
            sim2.positions_get(
                SYMBOL
            )
        )
        == 1,
        "Position duplicated/lost on restart",
    )

    check(
        len(
            sim2.orders_get(
                SYMBOL
            )
        )
        == 0,
        "Unexpected pending order appeared",
    )

    check(
        any(
            x.get("status")
            == "EXISTING_EXPOSURE"
            for x in recovered
        ),
        (
            "Recovery did not detect "
            f"existing position: {recovered}"
        ),
    )

    rt._ack_with_recovery(
        seq=seq,
        claude_called=False,
        decision_text=
            "M5:READY_LONG",
    )

    sim2.close()

    print(
        "HTTP_MARKET_POSITION_RESTART=PASS"
    )


# ============================================================
# Scenario 3
# WAIT cancels owned pending, process dies before execution_done.
# Restart must NOT recreate anything.
# ============================================================

def test_wait_cancel_restart():
    run_id = unique_run(
        "wait_cancel"
    )

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

    sim, event = create_first_event(
        run_id
    )

    seq = int(
        event["seq"]
    )

    tick = sim.symbol_info_tick(
        SYMBOL
    )

    ask = round(
        float(tick["ask"]),
        2,
    )

    entry = round(
        ask + 10.0,
        2,
    )

    direct = sim.order_send(
        {
            "action": 5,
            "symbol": SYMBOL,
            "type": 4,
            "volume": 0.10,
            "price": entry,
            "sl": round(
                entry - 2.0,
                2,
            ),
            "tp": round(
                entry + 4.0,
                2,
            ),
            "magic": 90502,
            "comment":
                "WF_REPLAY_M5_READY_LONG",
        }
    )

    check(
        int(
            direct.get("retcode")
            or 0
        )
        == 10009,
        f"Failed creating test pending: {direct}",
    )

    check(
        len(
            sim.orders_get(
                SYMBOL
            )
        )
        == 1,
        "Test pending missing",
    )

    decision = wait_decision(
        "M5"
    )

    journal = prepare_journal(
        run_root,
        event,
        decision,
        fingerprint=
            "wait-cancel-fp",
    )

    journal.mark_execution_started(
        seq
    )

    executor = executor_for(
        sim
    )

    first = (
        executor.execute_decisions(
            [
                {
                    "timeframe": "M5",
                    "decision": decision,
                }
            ]
        )
    )

    check(
        any(
            x.get("status")
            == "PENDING_CANCELLED"
            for x in first
        ),
        f"WAIT did not cancel pending: {first}",
    )

    check(
        sim.orders_get(
            SYMBOL
        )
        == [],
        "Pending remains after WAIT",
    )

    # Simulated crash before mark_execution_done().
    sim.close()

    sim2 = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    sim2.restore_replay(
        run_id
    )

    rt = runtime_shell(
        sim2,
        run_root,
    )

    results, mode = (
        rt._execute_with_recovery(
            seq=seq,
            execution_decisions=[
                {
                    "timeframe": "M5",
                    "decision": decision,
                }
            ],
        )
    )

    check(
        mode == "RESUMED_INCOMPLETE",
        f"Unexpected mode {mode}",
    )

    check(
        sim2.orders_get(
            SYMBOL
        )
        == [],
        "WAIT recovery recreated pending",
    )

    check(
        sim2.positions_get(
            SYMBOL
        )
        == [],
        "WAIT recovery opened position",
    )

    rt._ack_with_recovery(
        seq=seq,
        claude_called=False,
        decision_text="M5:WAIT",
    )

    sim2.close()

    print(
        "HTTP_WAIT_CANCEL_RESTART=PASS"
    )


# ============================================================
# Scenario 4
# Simulator accepts ACK but HTTP response is lost.
# Runtime must reconcile state and MUST NOT send ACK twice.
# ============================================================

class LostAckOnce:
    def __init__(
        self,
        inner,
    ):
        self.inner = inner
        self.ack_calls = 0

    def __getattr__(
        self,
        name,
    ):
        return getattr(
            self.inner,
            name,
        )

    def ack(
        self,
        *args,
        **kwargs,
    ):
        self.ack_calls += 1

        result = self.inner.ack(
            *args,
            **kwargs,
        )

        if self.ack_calls == 1:
            raise TimeoutError(
                "Intentional lost ACK response"
            )

        return result


def test_ack_response_loss():
    run_id = unique_run(
        "ack_loss"
    )

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

    sim, event = create_first_event(
        run_id
    )

    seq = int(
        event["seq"]
    )

    journal = ReplayRecoveryJournal(
        run_root,
        logger=LOGGER,
    )

    journal.ensure_event(
        seq,
        event["sim_time"],
    )

    journal.mark_execution_done(
        seq,
        [],
    )

    wrapped = LostAckOnce(
        sim
    )

    rt = runtime_shell(
        wrapped,
        run_root,
    )

    ack, mode = (
        rt._ack_with_recovery(
            seq=seq,
            claude_called=False,
            decision_text=None,
        )
    )

    check(
        wrapped.ack_calls == 1,
        (
            "ACK was blindly retried despite "
            "simulator proving acceptance"
        ),
    )

    check(
        mode
        == "FRESH_OR_RECONCILED",
        f"Unexpected ACK mode {mode}",
    )

    check(
        ack.get("reconciled")
        is True,
        f"ACK not reconciled: {ack}",
    )

    check(
        journal
        .get_event(seq)
        ["ack_done"],
        "ACK durable flag missing",
    )

    sim.close()

    print(
        "HTTP_ACK_RESPONSE_LOSS=PASS"
    )


# ============================================================
# Scenario 5
# Full Runtime:
#
# process #1 gets deterministic stub decision,
# decision becomes durable,
# execution deliberately crashes.
#
# process #2 restores same pending event using gateway that
# MUST NEVER BE CALLED.
# ============================================================

class CountingStub(
    StubClaudeGateway
):
    def __init__(
        self,
        logger=None,
    ):
        super().__init__(
            logger=logger
        )
        self.calls = 0

    def ask_until_valid(
        self,
        *args,
        **kwargs,
    ):
        self.calls += 1

        return super().ask_until_valid(
            *args,
            **kwargs,
        )


class ForbiddenGateway:
    api_called = False

    def __init__(
        self,
        logger=None,
    ):
        self.logger = logger
        self.calls = 0

    def ask_until_valid(
        self,
        *args,
        **kwargs,
    ):
        self.calls += 1

        raise AssertionError(
            "Gateway was called during "
            "durable decision recovery"
        )


class CrashExecutor:
    def __init__(
        self,
    ):
        self.calls = 0

    def execute_decisions(
        self,
        decisions,
    ):
        self.calls += 1

        raise RuntimeError(
            "INTENTIONAL_EXECUTION_CRASH"
        )


def test_full_runtime_decision_reuse():
    run_id = unique_run(
        "decision_reuse"
    )

    run_root = (
        ROOT
        / "replay_runs"
        / run_id
    )

    # Start shortly before an M15 close.
    # The generic START=08:00 produces an initial 08:05 M5
    # checkpoint where no Claude-analysis timeframe necessarily
    # closes, so a deterministic gateway call is not guaranteed.
    sim = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    sim.create_replay(
        "2026-09-25T08:10:00+00:00",
        END,
        run_id=run_id,
    )

    sim.start()

    event = wait_event(
        sim,
        0,
    )

    check(
        "M15"
        in (
            event.get(
                "closed_timeframes"
            )
            or []
        ),
        (
            "Decision-reuse test expected "
            "an M15 checkpoint, got: "
            f"{event}"
        ),
    )

    clock1 = ReplayClock()

    clock1.set(
        event["sim_time"]
    )

    gateway1 = CountingStub()

    runtime1 = ReplayRuntime(
        ROOT,
        run_root,
        sim,
        clock1,
        gateway1,
    )

    runtime1.executor = (
        CrashExecutor()
    )

    crashed = False

    try:
        runtime1.process_event(
            event
        )

    except RuntimeError as error:
        check(
            "INTENTIONAL_EXECUTION_CRASH"
            in str(error),
            f"Unexpected first-process error: {error}",
        )

        crashed = True

    check(
        crashed,
        "Execution crash was not triggered",
    )

    check(
        gateway1.calls > 0,
        (
            "First runtime never produced "
            "a deterministic decision"
        ),
    )

    seq = int(
        event["seq"]
    )

    journal1 = (
        ReplayRecoveryJournal(
            run_root
        )
    )

    durable_event = (
        journal1.get_event(
            seq
        )
    )

    check(
        durable_event is not None,
        "Recovery event was not written",
    )

    check(
        len(
            durable_event[
                "decisions"
            ]
        )
        > 0,
        "No durable decision exists",
    )

    check(
        durable_event[
            "execution_started"
        ],
        "execution_started missing",
    )

    check(
        not durable_event[
            "execution_done"
        ],
        (
            "execution_done should not "
            "exist after intentional crash"
        ),
    )

    sim.close()

    sim2 = SimulatorClient(
        SIM_URL,
        timeout=30.0,
    )

    restored = sim2.restore_replay(
        run_id
    )

    check(
        restored.get("status")
        == "WAITING_ACK",
        (
            "Pending event not restored: "
            f"{restored}"
        ),
    )

    event2 = restored[
        "pending_event"
    ]

    clock2 = ReplayClock()

    clock2.set(
        event2["sim_time"]
    )

    gateway2 = ForbiddenGateway()

    runtime2 = ReplayRuntime(
        ROOT,
        run_root,
        sim2,
        clock2,
        gateway2,
    )

    result = runtime2.process_event(
        event2
    )

    check(
        gateway2.calls == 0,
        (
            "Recovered event called "
            "gateway a second time"
        ),
    )

    check(
        result[
            "execution_recovery_mode"
        ]
        == "RESUMED_INCOMPLETE",
        (
            "Execution did not resume from "
            f"incomplete state: {result}"
        ),
    )

    check(
        runtime2.recovery
        .get_event(seq)
        ["execution_done"],
        "Recovered execution not durable",
    )

    check(
        runtime2.recovery
        .get_event(seq)
        ["ack_done"],
        "Recovered ACK not durable",
    )

    sim2.close()

    print(
        "HTTP_DECISION_REUSE_NO_SECOND_GATEWAY=PASS"
    )


# ============================================================
# Scenario 6
# Full free stub replay, then --resume already FINISHED run.
# ============================================================

def test_full_stub_and_finished_resume():
    command = [
        sys.executable,
        str(
            ROOT
            / "run_simulator_replay.py"
        ),
        "--start",
        (
            "2026-09-25T08:00:00+00:00"
        ),
        "--end",
        (
            "2026-09-25T08:30:00+00:00"
        ),
        "--ai",
        "stub",
    ]

    first = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )

    check(
        first.returncode == 0,
        (
            "Full stub replay failed.\n"
            f"stdout:\n{first.stdout}\n"
            f"stderr:\n{first.stderr}"
        ),
    )

    check(
        "FINISHED"
        in first.stdout,
        (
            "Stub replay did not finish.\n"
            + first.stdout
        ),
    )

    match = re.search(
        r"RUN_ID=([^\r\n]+)",
        first.stdout,
    )

    check(
        match is not None,
        (
            "RUN_ID missing from stub replay.\n"
            + first.stdout
        ),
    )

    run_id = (
        match.group(1)
        .strip()
    )

    report_path = (
        ROOT
        / "replay_runs"
        / run_id
        / "simulator_report.json"
    )

    check(
        report_path.exists(),
        "simulator_report.json missing",
    )

    report = json.loads(
        report_path.read_text(
            encoding="utf-8"
        )
    )

    check(
        int(
            report.get(
                "trades_closed"
            )
            or 0
        )
        == 0,
        (
            "Stub WAIT replay unexpectedly "
            "closed trades"
        ),
    )

    account = (
        report.get("account")
        or {}
    )

    check(
        abs(
            float(
                account.get(
                    "balance"
                )
                or 0.0
            )
            - 100000.0
        )
        < 1e-9,
        (
            "Unexpected stub replay balance: "
            f"{account}"
        ),
    )

    resume = subprocess.run(
        [
            sys.executable,
            str(
                ROOT
                / "run_simulator_replay.py"
            ),
            "--resume",
            run_id,
            "--ai",
            "stub",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )

    check(
        resume.returncode == 0,
        (
            "Resume FINISHED replay failed.\n"
            f"stdout:\n{resume.stdout}\n"
            f"stderr:\n{resume.stderr}"
        ),
    )

    check(
        "RESUMED=True"
        in resume.stdout,
        (
            "Resume mode not reported.\n"
            + resume.stdout
        ),
    )

    check(
        "FINISHED"
        in resume.stdout,
        (
            "Resumed FINISHED replay "
            "did not terminate cleanly.\n"
            + resume.stdout
        ),
    )

    print(
        "FULL_STUB_AND_FINISHED_RESUME=PASS"
    )

    print(
        "  FIRST_RUN_ID="
        + run_id
    )


def main():
    probe = SimulatorClient(
        SIM_URL,
        timeout=5.0,
    )

    health = probe.health()

    probe.close()

    check(
        health is not None,
        "Simulator health failed",
    )

    print(
        "SIMULATOR_HEALTH=PASS"
    )

    tests = [
        (
            "OS crash after pending order",
            test_os_crash_after_pending_send,
        ),
        (
            "Market position restart",
            test_market_position_restart,
        ),
        (
            "WAIT cancel restart",
            test_wait_cancel_restart,
        ),
        (
            "ACK response loss",
            test_ack_response_loss,
        ),
        (
            "Durable decision reuse",
            test_full_runtime_decision_reuse,
        ),
        (
            "Full stub + finished resume",
            test_full_stub_and_finished_resume,
        ),
    ]

    for name, fn in tests:
        print()
        print(
            "========== "
            + name
            + " =========="
        )

        fn()

    print()
    print(
        "FINAL_HTTP_STABILITY_PACK=PASS"
    )


if __name__ == "__main__":
    main()
