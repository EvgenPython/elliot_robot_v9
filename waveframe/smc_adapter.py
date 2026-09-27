from __future__ import annotations
import pandas as pd
from .models import StructureSnapshot, SmcEvidence


def _causal_swings_frame(df: pd.DataFrame, structure: StructureSnapshot) -> pd.DataFrame:
    swings = pd.DataFrame({"HighLow": [0]*len(df), "Level": [float("nan")]*len(df)})
    for p in structure.confirmed_pivots:
        if 0 <= p.source_index < len(swings):
            swings.loc[p.source_index, "HighLow"] = 1 if p.kind == "high" else -1
            swings.loc[p.source_index, "Level"] = p.price
    return swings


def analyze_smc(df: pd.DataFrame, structure: StructureSnapshot) -> SmcEvidence:
    """Use smartmoneyconcepts only on OUR already-confirmed causal pivots.

    Deliberately never invokes the library pivot-discovery helper, because it may use
    future bars to identify historical pivots. Library output is advisory only.
    """
    try:
        from smartmoneyconcepts import smc
        ohlc = df[["open", "high", "low", "close"]].copy().reset_index(drop=True)
        swings = _causal_swings_frame(df.reset_index(drop=True), structure)
        bosdf = smc.bos_choch(ohlc, swings, close_break=True)
        liqdf = smc.liquidity(ohlc, swings, range_percent=0.01)
        last_bos = None; last_choch = None; liquidity = []
        if bosdf is not None:
            for i, row in bosdf.iterrows():
                if pd.notna(row.get("BOS")) and float(row.get("BOS")) != 0:
                    last_bos = {"index": int(i), "direction": "bullish" if float(row["BOS"]) > 0 else "bearish", "level": _num(row.get("Level"))}
                if pd.notna(row.get("CHOCH")) and float(row.get("CHOCH")) != 0:
                    last_choch = {"index": int(i), "direction": "bullish" if float(row["CHOCH"]) > 0 else "bearish", "level": _num(row.get("Level"))}
        if liqdf is not None:
            for i, row in liqdf.tail(20).iterrows():
                marker = _num(row.get("Liquidity"))
                level = _num(row.get("Level"))
                if marker not in (None, 0) and level is not None:
                    liquidity.append({"index": int(i), "side": "above" if marker > 0 else "below", "level": level})
        return SmcEvidence(available=True, last_bos=last_bos, last_choch=last_choch, liquidity=liquidity[-6:])
    except Exception as e:
        return SmcEvidence(available=False, error=f"{type(e).__name__}: {e}")


def _num(v):
    try:
        x=float(v)
        return None if pd.isna(x) else x
    except Exception:
        return None
