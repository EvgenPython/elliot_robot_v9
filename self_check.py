from pathlib import Path
import re, sys
ROOT=Path(__file__).resolve().parent
FORBIDDEN=[
    "TARGET_DAILY_USD","SOFT_DAILY_USD","EVENT_HARD_CEILING_USD",
    "MAX_M30_EVENT_DECISIONS_PER_DAY","MAX_H1_EVENT_DECISIONS_PER_DAY",
    "daily_m30_event_slots_used","PERMANENT_BLOCKED",
]
# Comments/docs may name old bugs; scan runtime Python only.
text="\n".join(p.read_text(encoding="utf-8",errors="ignore") for p in (ROOT/"waveframe").glob("*.py"))
bad=[x for x in FORBIDDEN if x in text]
# Cost observation must not gate calls.
if re.search(r"if\s+.*estimated_cost|if\s+.*cost_usd|if\s+.*budget",text,re.I): bad.append("cost/budget conditional in runtime")
print("WaveFrame V9.1 self-check")
print("Forbidden runtime gates:", bad or "NONE")
print("AI CALL/BUDGET GATE AUDIT:", "PASS" if not bad else "FAIL")
sys.exit(1 if bad else 0)
