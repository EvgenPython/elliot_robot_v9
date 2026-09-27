import argparse
from datetime import datetime
from pathlib import Path
from waveframe.logging import export_audit
ap=argparse.ArgumentParser(); ap.add_argument("--days",type=int,default=7); ap.add_argument("--output"); args=ap.parse_args()
out=Path(args.output) if args.output else Path("diagnostics")/f"waveframe_audit_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
print(export_audit(".",out,args.days))
