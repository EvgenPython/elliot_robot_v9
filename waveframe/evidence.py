from __future__ import annotations
from datetime import datetime, timezone
import pandas as pd
from .causal_structure import summarize_structure
from .smc_adapter import analyze_smc
from .trend_adapter import analyze_trendln
from .geometry import analyze_geometry
from .models import EvidencePack
from .hashing import stable_hash


def _validate_df(df: pd.DataFrame):
    req={"open_time","close_time","open","high","low","close"}
    missing=req-set(df.columns)
    if missing: raise ValueError(f"missing columns: {sorted(missing)}")
    if not df["close_time"].is_monotonic_increasing: raise ValueError("bars must be chronological")


def build_evidence(df: pd.DataFrame, symbol: str, timeframe: str, left=3, right=3, recent=24, generated_at: datetime|None=None) -> EvidencePack:
    _validate_df(df)
    structure=summarize_structure(df,timeframe,left,right)
    smc=analyze_smc(df,structure)
    trend=analyze_trendln(df)
    geometry=analyze_geometry(structure)
    compact=df.tail(recent)[["open_time","close_time","open","high","low","close"]].copy()
    compact["open_time"]=compact["open_time"].astype(str); compact["close_time"]=compact["close_time"].astype(str)
    rows=compact.to_dict("records")
    bars_hash=stable_hash(rows)
    last=pd.to_datetime(df.iloc[-1]["close_time"],utc=True).to_pydatetime()
    generated_at=generated_at or datetime.now(timezone.utc)
    return EvidencePack(symbol=symbol,timeframe=timeframe,generated_at=generated_at,last_closed_bar=last,bars_hash=bars_hash,structure=structure,smc=smc,trend=trend,geometry=geometry,recent_ohlc=rows)
