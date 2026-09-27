from __future__ import annotations
import numpy as np
from scipy.stats import linregress
from .models import StructureSnapshot, GeometryEvidence


def analyze_geometry(structure: StructureSnapshot) -> GeometryEvidence:
    highs=[p for p in structure.confirmed_pivots if p.kind=="high"][-6:]
    lows=[p for p in structure.confirmed_pivots if p.kind=="low"][-6:]
    if len(highs)<2 or len(lows)<2:
        return GeometryEvidence()
    hs=linregress([p.source_index for p in highs],[p.price for p in highs])
    ls=linregress([p.source_index for p in lows],[p.price for p in lows])
    a=float(hs.slope); b=float(ls.slope)
    scale=max(abs(a),abs(b),1e-12)
    parallelism=max(0.0,1.0-abs(a-b)/scale)
    convergence=float(a-b)
    hints=[]; ctype="channel"
    if a<0 and b>0:
        ctype="converging_triangle"; hints.append("symmetrical_triangle")
    elif a<=0 and b>0:
        hints.append("ascending_triangle_or_wedge")
    elif a<0 and b>=0:
        hints.append("descending_triangle_or_wedge")
    elif parallelism>0.75:
        ctype="ascending_channel" if a>0 and b>0 else "descending_channel" if a<0 and b<0 else "horizontal_channel"
    else:
        ctype="diverging_or_irregular"
    return GeometryEvidence(channel_type=ctype,support_slope=b,resistance_slope=a,parallelism=round(parallelism,4),convergence=convergence,pattern_hints=hints)
