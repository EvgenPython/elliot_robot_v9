from __future__ import annotations
from .models import ElliottCount


def validate_elliott(count: ElliottCount) -> list[str]:
    """Formal hard-rule checks only. Findings are returned to Claude for revision;
    Python never substitutes its own trade direction."""
    legs={str(x.label).lower():x for x in count.legs}
    issues=[]
    # Only validate classic 1-5 impulse when all legs are present.
    if all(k in legs for k in ["1","2","3","4","5"]):
        w1,w2,w3,w4,w5=[legs[k] for k in ["1","2","3","4","5"]]
        bullish=count.direction=="bullish"
        if bullish and w2.end_price <= w1.start_price: issues.append("Wave 2 retraced beyond start of Wave 1")
        if not bullish and count.direction=="bearish" and w2.end_price >= w1.start_price: issues.append("Wave 2 retraced beyond start of Wave 1")
        l1=abs(w1.end_price-w1.start_price); l3=abs(w3.end_price-w3.start_price); l5=abs(w5.end_price-w5.start_price)
        if l3 < min(l1,l5): issues.append("Wave 3 is the shortest impulse wave")
        if bullish and w4.end_price <= w1.end_price: issues.append("Wave 4 overlaps Wave 1 price territory")
        if count.direction=="bearish" and w4.end_price >= w1.end_price: issues.append("Wave 4 overlaps Wave 1 price territory")
    return issues
