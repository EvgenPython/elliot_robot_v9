from __future__ import annotations
import argparse, json
from collections import Counter,defaultdict
from datetime import datetime,timedelta,timezone
from pathlib import Path

def read_jsonl(root, rel, days):
    base=Path(root)/"logs"/rel
    if not base.exists(): return []
    cutoff=datetime.now(timezone.utc).date()-timedelta(days=days-1)
    out=[]
    for p in base.glob("*.jsonl"):
        try:
            if datetime.fromisoformat(p.stem).date()<cutoff: continue
        except Exception: pass
        for line in p.read_text(encoding="utf-8",errors="replace").splitlines():
            try: out.append(json.loads(line))
            except: pass
    return out

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--days",type=int,default=7); ap.add_argument("--root",default="."); args=ap.parse_args()
    cost=read_jsonl(args.root,"cost",args.days); dec=read_jsonl(args.root,"decisions",args.days); claude=read_jsonl(args.root,"claude",args.days); errs=read_jsonl(args.root,"errors",args.days); tf=read_jsonl(args.root,"trade_funnel",args.days)
    total=sum(float(x.get("data",{}).get("estimated_cost_usd",0) or 0) for x in cost)
    cache=Counter(x.get("data",{}).get("cache_status","unknown") for x in cost)
    calls=sum(1 for x in dec if x.get("data",{}).get("called")); nocalls=Counter(x.get("data",{}).get("reason") for x in dec if not x.get("data",{}).get("called"))
    actions=Counter(x.get("data",{}).get("action") for x in tf if x.get("event")=="CLAUDE_TRADE_INTENT")
    repairs=Counter(x.get("event") for x in claude)
    print("="*64); print(f"WAVEFRAME V9.1 AUDIT — last {args.days} day(s)"); print("="*64)
    print(f"Claude calls requested by policy: {calls}")
    print(f"Estimated Claude cost: ${total:.6f}")
    print("Cache status:",dict(cache)); print("No-call reasons:",dict(nocalls)); print("Claude repair events:",dict(repairs)); print("Trade actions:",dict(actions)); print(f"Errors: {len(errs)}")
if __name__=="__main__": main()
