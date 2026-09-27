import argparse,json
from pathlib import Path
ap=argparse.ArgumentParser(); ap.add_argument("--date",required=True); args=ap.parse_args()
for p in sorted(Path("logs").rglob(f"{args.date}.jsonl")):
    for line in p.read_text(encoding="utf-8",errors="replace").splitlines():
        try:
            e=json.loads(line); print(e.get("at_utc"),e.get("category"),e.get("event"),json.dumps(e.get("data",{}),ensure_ascii=False))
        except: pass
