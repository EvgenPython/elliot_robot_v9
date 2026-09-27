from __future__ import annotations
from .models import ClaudeDecision

def claude_trade_intent(decision: ClaudeDecision) -> str | None:
    """Only Claude's action can create trade intent. Library evidence is absent by design."""
    if decision.action=="READY_LONG": return "LONG"
    if decision.action=="READY_SHORT": return "SHORT"
    return None
