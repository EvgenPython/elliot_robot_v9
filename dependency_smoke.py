from __future__ import annotations
import sys

checks=[]
for name in ["numpy","pandas","scipy","smartmoneyconcepts","trendln","anthropic","pydantic"]:
    try:
        mod=__import__(name)
        ver=getattr(mod,"__version__",getattr(getattr(mod,"smc",None),"__version__","unknown"))
        checks.append((name,True,str(ver),getattr(mod,"__file__","")))
    except Exception as e:
        checks.append((name,False,f"{type(e).__name__}: {e}",""))
for row in checks:
    print(f"{row[0]:20} {'OK' if row[1] else 'FAIL':4} {row[2]}")
failed=[x for x in checks if not x[1]]
if failed:
    sys.exit(1)
print("DEPENDENCY SMOKE: PASS")
