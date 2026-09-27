from __future__ import annotations
import json
import time
from datetime import timedelta, timezone
from pathlib import Path

from .config import load_settings
from .delta import material_fingerprint
from .evidence import build_evidence
from .logging import AuditLogger
from .orchestrator import Orchestrator
from .watch import evaluate_watch

ANALYSIS_ORDER = ["H4", "H1", "M30", "M15"]
FP_TZ = timezone(timedelta(hours=3))


class ReplayRuntime:
    def __init__(self, project_root, run_root, simulator, clock, claude_gateway):
        self.project_root = Path(project_root).resolve()
        self.run_root = Path(run_root).resolve()
        self.run_root.mkdir(parents=True, exist_ok=True)
        self.settings = load_settings(self.project_root)
        self.simulator = simulator
        self.clock = clock
        self.logger = AuditLogger(self.run_root, clock=self.clock)
        if hasattr(claude_gateway, "logger"):
            claude_gateway.logger = self.logger
        self.orchestrator = Orchestrator(self.run_root, claude_gateway, clock=self.clock)
        self.symbol = str(self.settings.get("symbol") or "XAUUSD")
        s = self.settings.get("structure", {})
        self.history_bars = int(s.get("history_bars", 300))
        self.left = int(s.get("pivot_left", 3))
        self.right = int(s.get("pivot_right", 3))
        self.recent = int(s.get("recent_ohlc_for_claude", 24))
        self.rebase_hour_fp = int(self.settings.get("claude", {}).get("daily_rebase_hour_fp", 8))

    def _memory_audit(self) -> dict:
        memory = self.orchestrator.memory_store.load(self.symbol)
        return {
            "memory_version": memory.memory_version,
            "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
            "last_decision": memory.last_decision.action if memory.last_decision else None,
            "active_watches": len(memory.active_watches),
            "timeframes": {
                tf: {
                    "evidence_fingerprint": state.evidence_fingerprint,
                    "last_closed_bar": (
                        state.last_closed_bar.isoformat()
                        if state.last_closed_bar is not None
                        else None
                    ),
                }
                for tf, state in memory.timeframes.items()
            },
        }

    def _watch_triggered(self, timeframe: str, latest_bar: dict) -> bool:
        memory = self.orchestrator.memory_store.load(self.symbol)
        hits = []
        for w in memory.active_watches:
            if w.timeframe == timeframe and evaluate_watch(w, latest_bar):
                hits.append(w.model_dump(mode="json"))
        if hits:
            self.logger.event("decisions", "WATCH_TRIGGERED", {
                "timeframe": timeframe,
                "bar": latest_bar,
                "watches": hits,
            })
        return bool(hits)

    def _force_rebase(self, timeframe: str) -> bool:
        if timeframe != "H1":
            return False
        now_fp = self.clock.now().astimezone(FP_TZ)
        if now_fp.hour != self.rebase_hour_fp or now_fp.minute != 0:
            return False
        memory = self.orchestrator.memory_store.load(self.symbol)
        return memory.last_rebase_at is None or memory.last_rebase_at.astimezone(FP_TZ).date() != now_fp.date()

    def process_event(self, event: dict) -> dict:
        self.clock.set(event["sim_time"])
        seq = int(event["seq"])
        closed = set(event.get("closed_timeframes") or [])
        self.logger.event("replay", "EVENT_RECEIVED", {
            "seq": seq,
            "sim_time": event["sim_time"],
            "closed_timeframes": sorted(closed),
        })
        memory_before = self._memory_audit()

        self.simulator.analysis_started(seq, cycle_id=f"replay-{seq}")

        claude_called = False
        actions = []
        processed = []

        for tf in ANALYSIS_ORDER:
            if tf not in closed:
                continue
            df = self.simulator.rates(self.symbol, tf, count=self.history_bars)
            if len(df) < self.left + self.right + 1:
                self.logger.event("decisions", "TIMEFRAME_SKIPPED", {
                    "timeframe": tf,
                    "reason": "INSUFFICIENT_HISTORY",
                    "rows": len(df),
                })
                continue
            latest = df.iloc[-1].to_dict()
            pack = build_evidence(
                df,
                self.symbol,
                tf,
                left=self.left,
                right=self.right,
                recent=self.recent,
                generated_at=self.clock.now(),
            )
            watch_triggered = self._watch_triggered(tf, latest)
            force_rebase = self._force_rebase(tf)
            fingerprint = material_fingerprint(pack)

            result = self.orchestrator.process_evidence(
                pack,
                watch_triggered=watch_triggered,
                force_rebase=force_rebase,
            )
            processed.append({
                "timeframe": tf,
                "called": bool(result.get("called")),
                "reason": result.get("reason"),
                "evidence_fingerprint": fingerprint,
                "watch_triggered": watch_triggered,
                "force_rebase": force_rebase,
            })
            if result.get("called"):
                claude_called = True
                decision = result.get("decision")
                if decision is not None:
                    actions.append(f"{tf}:{decision.action}")

        memory_after = self._memory_audit()

        decision_text = ",".join(actions) if actions else None
        ack = self.simulator.ack(
            seq,
            claude_called=claude_called,
            cycle_id=f"replay-{seq}",
            decision=decision_text,
        )
        self.logger.event("replay", "EVENT_COMPLETED", {
            "seq": seq,
            "sim_time": event["sim_time"],
            "closed_timeframes": sorted(closed),
            "analyzed_timeframes": [x["timeframe"] for x in processed],
            "claude_called": claude_called,
            "actions": actions,
            "processed": processed,
            "memory_before": memory_before,
            "memory_after": memory_after,
            "ack": ack,
        })
        return {
            "seq": seq,
            "claude_called": claude_called,
            "actions": actions,
            "processed": processed,
        }

    def run_until_finished(self, poll_seconds: float = 0.02) -> dict:
        last_seq = 0
        while True:
            event = self.simulator.wait_event(last_seq, poll_seconds=poll_seconds)
            if event is None:
                status = self.simulator.status()
                if status.get("status") == "FINISHED":
                    break
                if status.get("status") in {"ERROR", "STOPPED"}:
                    raise RuntimeError(f"Simulator stopped: {status}")
                time.sleep(poll_seconds)
                continue
            result = self.process_event(event)
            last_seq = int(result["seq"])
            print(f'{last_seq:04d} {self.clock.now().isoformat()} Claude={result["claude_called"]} {result["actions"]}')

        report = self.simulator.report()
        (self.run_root / "simulator_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return report
