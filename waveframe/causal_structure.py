from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
import numpy as np
import pandas as pd
from scipy.signal import find_peaks
from .models import Pivot, StructureSnapshot
from .hashing import stable_hash


def _prominence(series: np.ndarray, index: int) -> float | None:
    if len(series) < 5:
        return None
    try:
        peaks, props = find_peaks(series, prominence=0)
        where = np.where(peaks == index)[0]
        if len(where):
            return float(props["prominences"][where[0]])
    except Exception:
        pass
    return None


def confirmed_pivots(df: pd.DataFrame, timeframe: str, left: int = 3, right: int = 3) -> list[Pivot]:
    """Causal pivot detector.

    A pivot at index i is only emitted after i+right exists. confirmed_at is the
    close time of that right-most confirming bar. Never use negative shift or
    edge pivots.
    """
    if len(df) < left + right + 1:
        return []
    highs = df["high"].to_numpy(float); lows = df["low"].to_numpy(float)
    closes = pd.to_datetime(df["close_time"], utc=True)
    opens = pd.to_datetime(df["open_time"], utc=True)
    out: list[Pivot] = []
    for i in range(left, len(df) - right):
        h = highs[i]; l = lows[i]
        hwin = highs[i-left:i+right+1]
        lwin = lows[i-left:i+right+1]
        if h == np.max(hwin) and np.sum(hwin == h) == 1:
            out.append(Pivot(timeframe=timeframe, kind="high", price=float(h), pivot_time=opens.iloc[i].to_pydatetime(), confirmed_at=closes.iloc[i+right].to_pydatetime(), source_index=i, prominence=_prominence(highs, i)))
        if l == np.min(lwin) and np.sum(lwin == l) == 1:
            out.append(Pivot(timeframe=timeframe, kind="low", price=float(l), pivot_time=opens.iloc[i].to_pydatetime(), confirmed_at=closes.iloc[i+right].to_pydatetime(), source_index=i, prominence=_prominence(-lows, i)))
    out.sort(key=lambda p: (p.confirmed_at, p.pivot_time, p.kind))
    return out


def summarize_structure(df: pd.DataFrame, timeframe: str, left: int = 3, right: int = 3) -> StructureSnapshot:
    pivots = confirmed_pivots(df, timeframe, left, right)
    highs = [p for p in pivots if p.kind == "high"]
    lows = [p for p in pivots if p.kind == "low"]
    hs = "NA" if len(highs) < 2 else ("HH" if highs[-1].price > highs[-2].price else "LH")
    ls = "NA" if len(lows) < 2 else ("HL" if lows[-1].price > lows[-2].price else "LL")
    trend = "bullish" if hs == "HH" and ls == "HL" else "bearish" if hs == "LH" and ls == "LL" else "neutral"
    fp_data = [(p.kind, round(p.price, 5), p.pivot_time.isoformat(), p.confirmed_at.isoformat()) for p in pivots[-16:]]
    return StructureSnapshot(timeframe=timeframe, confirmed_pivots=pivots[-20:], high_sequence=hs, low_sequence=ls, trend=trend, fingerprint=stable_hash(fp_data))
