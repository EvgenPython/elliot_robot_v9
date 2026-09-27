from __future__ import annotations
from .models import ClaudeDecision, ElliottCount


class StubClaudeGateway:
    """Zero-cost deterministic gateway used only for integration smoke tests."""

    def __init__(self, logger=None):
        self.logger = logger

    def ask_until_valid(self, stable_prefix: str, delta: dict, **_kwargs) -> ClaudeDecision:
        count = ElliottCount(
            degree=str(delta.get("timeframe") or "unknown"),
            direction="neutral",
            current_wave="unconfirmed",
            legs=[],
            invalidation=None,
            summary="STUB integration decision; no Anthropic API call.",
        )
        if self.logger:
            self.logger.event("claude", "STUB_DECISION", {
                "timeframe": delta.get("timeframe"),
                "last_closed_bar": delta.get("last_closed_bar"),
                "api_called": False,
            })
        return ClaudeDecision(
            action="WAIT",
            primary_count=count,
            alternate_count=None,
            structure_assessment="stub",
            support_resistance_assessment="stub",
            channel_assessment="stub",
            pattern_assessment=[],
            evidence_disagreements=[],
            watch_conditions=[],
            entry=None,
            stop=None,
            target=None,
            confidence="low",
            rationale_brief="Integration smoke test only.",
            evidence_interpretation="No real Claude call was made.",
        )
