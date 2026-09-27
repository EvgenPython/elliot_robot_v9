from __future__ import annotations
from .logging import AuditLogger
from .memory import MarketMemoryStore
from .cache import CacheManager
from .prompts import SYSTEM_RULES
from .delta import compact_delta, material_fingerprint
from .models import TimeframeMemory
from .policy import AnalysisPolicy
from .decision import claude_trade_intent
from .clock import RealClock


class Orchestrator:
    def __init__(self, root, claude_gateway, clock=None):
        self.root = root
        self.clock = clock or RealClock()
        self.log = AuditLogger(root, clock=self.clock)
        self.memory_store = MarketMemoryStore(root, self.log, clock=self.clock)
        self.cache = CacheManager()
        self.policy = AnalysisPolicy(self.log)
        self.claude = claude_gateway

    def result_from_decision(
        self,
        decision,
        reason: str,
        recovered: bool = False,
    ):
        intent = claude_trade_intent(
            decision
        )

        self.log.event(
            "trade_funnel",
            "CLAUDE_TRADE_INTENT",
            {
                "action": decision.action,
                "intent": intent,
                "entry": decision.entry,
                "stop": decision.stop,
                "target": decision.target,
                "reason": reason,
                "recovered": bool(recovered),
            },
        )

        return {
            "called": True,
            "reason": reason,
            "decision": decision,
            "trade_intent": intent,
            "recovered": bool(recovered),
        }

    def commit_validated_decision(
        self,
        pack,
        decision,
        *,
        force_rebase: bool = False,
        result_reason: str = "RECOVERY_DURABLE_DECISION",
        save_reason: str = "RECOVERY_DURABLE_DECISION",
        recovered: bool = True,
    ):
        """
        Commit an already validated ClaudeDecision to Market Memory.

        This method is intentionally separate from the API call so a
        decision restored from ReplayRecoveryJournal can be committed
        without asking Claude again.

        It is idempotent for the crash window:
            Market Memory saved
            -> process dies
            -> journal memory_committed flag not yet written.
        """
        memory = self.memory_store.load(
            pack.symbol
        )

        fingerprint = material_fingerprint(
            pack
        )

        existing = memory.timeframes.get(
            pack.timeframe
        )

        last_decision_same = (
            memory.last_decision is not None
            and
            memory.last_decision.model_dump(
                mode="json"
            )
            == decision.model_dump(
                mode="json"
            )
        )

        watches_same = (
            [
                x.model_dump(mode="json")
                for x in memory.active_watches
            ]
            ==
            [
                x.model_dump(mode="json")
                for x in decision.watch_conditions
            ]
        )

        already_committed = (
            existing is not None
            and
            existing.evidence_fingerprint
            == fingerprint
            and last_decision_same
            and watches_same
        )

        if already_committed:
            self.log.event(
                "recovery",
                "MEMORY_COMMIT_ALREADY_PRESENT",
                {
                    "symbol": pack.symbol,
                    "timeframe": pack.timeframe,
                    "evidence_fingerprint":
                        fingerprint,
                    "action":
                        decision.action,
                },
            )

        else:
            memory.timeframes[
                pack.timeframe
            ] = TimeframeMemory(
                timeframe=pack.timeframe,
                evidence_fingerprint=
                    fingerprint,
                last_closed_bar=
                    pack.last_closed_bar,
                elliott=
                    decision.primary_count,
                alternate=
                    decision.alternate_count,
                claude_context={
                    "structure":
                        decision.structure_assessment,
                    "support_resistance":
                        decision.support_resistance_assessment,
                    "channel":
                        decision.channel_assessment,
                    "patterns":
                        decision.pattern_assessment,
                    "disagreements":
                        decision.evidence_disagreements,
                },
            )

            memory.last_decision = decision
            memory.active_watches = (
                decision.watch_conditions
            )

            if force_rebase:
                memory.last_rebase_at = (
                    self.clock.now()
                )

            self.memory_store.save(
                memory,
                reason=save_reason,
            )

        return self.result_from_decision(
            decision,
            reason=result_reason,
            recovered=recovered,
        )

    def process_evidence(
        self,
        pack,
        watch_triggered=False,
        force_rebase=False,
        on_valid_decision=None,
    ):
        memory = self.memory_store.load(pack.symbol)
        self.log.snapshot(
            "evidence",
            f"{pack.timeframe}_{pack.last_closed_bar.strftime('%Y%m%dT%H%M%S')}",
            pack.model_dump(mode="json"),
        )
        should_call = self.policy.on_closed_bar(pack, memory, watch_triggered, force_rebase)
        if not should_call:
            return {"called": False, "reason": "NO_MATERIAL_DELTA"}

        reason = (
            "FORCE_REBASE"
            if force_rebase
            else "WATCH_TRIGGERED"
            if watch_triggered
            else "EVIDENCE_CHANGED"
        )

        prefix = self.cache.build_stable_prefix(SYSTEM_RULES, memory)
        delta = compact_delta(pack, memory)
        self.log.event("cache", "CACHE_CONTEXT_PREPARED", {
            "source": prefix.source,
            "memory_version": prefix.memory_version,
            "timeframe": pack.timeframe,
        })
        if on_valid_decision is None:
            decision = (
                self.claude.ask_until_valid(
                    prefix.stable_prefix,
                    delta,
                )
            )
        else:
            decision = (
                self.claude.ask_until_valid(
                    prefix.stable_prefix,
                    delta,
                    on_valid_decision=
                        on_valid_decision,
                )
            )
        return self.commit_validated_decision(
            pack,
            decision,
            force_rebase=force_rebase,
            result_reason=reason,
            save_reason=
                "CLAUDE_VALIDATED_UPDATE",
            recovered=False,
        )
