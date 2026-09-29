from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


def resume_signal_path(
    run_root,
) -> Path:
    return (
        Path(run_root)
        / "RESUME.signal"
    )


def consume_resume_signal(
    run_root,
) -> bool:
    """
    Consume one explicit operator resume signal.

    The signal itself does not ACK an event, execute a trade,
    or change simulator state. It only allows the still-running
    Python process to re-enter ReplayRuntime, where durable
    recovery decides what is already complete.
    """

    path = resume_signal_path(
        run_root
    )

    if not path.exists():
        return False

    try:
        path.unlink()
    except FileNotFoundError:
        return False

    return True


def safe_hold_until_resume(
    run_root,
    sim,
    exc,
    *,
    sleep: Callable[[float], None] = time.sleep,
    resume_check: Callable[[], bool] | None = None,
    heartbeat_seconds: float = 60.0,
) -> None:
    """
    Keep the robot process alive after an unrecoverable-at-this-moment
    runtime failure.

    SAFE HOLD invariants:

    - process stays alive;
    - no fabricated WAIT/LONG/SHORT;
    - no forced ACK;
    - no automatic blind retry of ambiguous paid work;
    - durable recovery state remains untouched;
    - operator can repair the external cause and signal resume.
    """

    if resume_check is None:
        resume_check = lambda: (
            consume_resume_signal(
                run_root
            )
        )

    print("")
    print(
        "============================================"
    )
    print("ROBOT_SAFE_HOLD")
    print(
        "PROCESS_REMAINS_ALIVE=True"
    )
    print(
        f"ERROR_TYPE={type(exc).__name__}"
    )
    print(
        f"DETAIL={exc}"
    )
    print(
        "NO_TRADE_DECISION_WAS_FABRICATED=True"
    )
    print(
        "NO_ACK_WAS_FORCED=True"
    )
    print(
        "AUTOMATIC_BLIND_RETRY=False"
    )
    print(
        "RESUME_SIGNAL="
        f"{resume_signal_path(run_root)}"
    )
    print(
        "============================================"
    )

    heartbeat = 0

    while True:

        if resume_check():
            print("")
            print(
                "RESUME_SIGNAL_RECEIVED=True"
            )
            print(
                "REENTERING_DURABLE_RUNTIME=True"
            )
            return

        try:
            current = sim.status()

            sim_status = current.get(
                "status"
            )

            sim_seq = current.get(
                "seq"
            )

            sim_now = current.get(
                "sim_now"
            )

        except BaseException as status_exc:
            # Even loss of the simulator itself must not kill
            # the supervisor heartbeat.
            sim_status = "UNAVAILABLE"
            sim_seq = None
            sim_now = None

            print(
                "SAFE_HOLD_SIM_STATUS_ERROR="
                f"{type(status_exc).__name__}: "
                f"{status_exc}"
            )

        heartbeat += 1

        print(
            "SAFE_HOLD_HEARTBEAT "
            f"n={heartbeat} "
            f"status={sim_status} "
            f"seq={sim_seq} "
            f"sim_now={sim_now}"
        )

        sleep(
            float(
                heartbeat_seconds
            )
        )


def run_runtime_supervised(
    runtime,
    sim,
    run_root,
    *,
    hold_fn=None,
    on_resume=None,
):
    """
    The runtime is not allowed to terminate the process because
    of an internal/runtime failure.

    BaseException is intentionally caught so that an accidental
    SystemExit inside runtime code also becomes SAFE HOLD.

    KeyboardInterrupt remains the explicit operator stop.
    """

    if hold_fn is None:
        hold_fn = (
            safe_hold_until_resume
        )

    while True:

        try:
            return (
                runtime
                .run_until_finished()
            )

        except KeyboardInterrupt:
            # Explicit operator command: Ctrl+C.
            raise

        except BaseException as exc:
            hold_fn(
                run_root,
                sim,
                exc,
            )

            if on_resume is not None:
                on_resume()

            # Re-enter the SAME runtime object.
            # ReplayRecoveryJournal determines which parts of the
            # current event are already durable and must not repeat.
            continue
