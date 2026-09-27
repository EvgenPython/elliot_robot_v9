from __future__ import annotations
import numpy as np
import pandas as pd
from .models import TrendEvidence


def analyze_trendln(df: pd.DataFrame, n_levels: int = 3) -> TrendEvidence:
    """Advisory support/resistance and trend-line summary using trendln."""
    try:
        import trendln
        lows = pd.Series(df["low"].astype(float).to_numpy())
        highs = pd.Series(df["high"].astype(float).to_numpy())
        calc = trendln.calc_support_resistance((lows, highs), accuracy=2)
        # Newer trendln versions expose helper get_levels. Prefer it.
        supports=[]; resistances=[]
        try:
            near_s, near_r, _rr = trendln.get_levels(calc, len(df)-1, float(df.iloc[-1]["close"]), n=n_levels)
            supports=[float(x[0]) for x in near_s[:n_levels]]
            resistances=[float(x[0]) for x in near_r[:n_levels]]
        except Exception:
            # Fallback: average global lines at last x.
            (mins, maxs)=calc
            pmin=mins[1]; pmax=maxs[1]; x=len(df)-1
            if pmin and len(pmin)>=2: supports=[float(pmin[0]*x+pmin[1])]
            if pmax and len(pmax)>=2: resistances=[float(pmax[0]*x+pmax[1])]
        support_slope=None; resistance_slope=None
        try:
            support_slope=float(calc[0][1][0])
            resistance_slope=float(calc[1][1][0])
        except Exception:
            pass
        return TrendEvidence(available=True, supports=supports, resistances=resistances, support_slope=support_slope, resistance_slope=resistance_slope)
    except Exception as e:
        return TrendEvidence(available=False, error=f"{type(e).__name__}: {e}")
