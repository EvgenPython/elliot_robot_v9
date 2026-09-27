from __future__ import annotations
from datetime import datetime
from typing import Literal, Any
from pydantic import BaseModel, Field, model_validator

Timeframe = Literal["H4", "H1", "M30", "M15"]
Direction = Literal["bullish", "bearish", "neutral"]
Action = Literal["READY_LONG", "READY_SHORT", "WAIT"]

class Bar(BaseModel):
    symbol: str = "XAUUSD"
    timeframe: Timeframe
    open_time: datetime
    close_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    closed: bool = True

    @model_validator(mode="after")
    def _check(self):
        if self.close_time <= self.open_time:
            raise ValueError("close_time must be after open_time")
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("invalid high")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("invalid low")
        if not self.closed:
            raise ValueError("only closed bars may enter the analytical pipeline")
        return self

class Pivot(BaseModel):
    timeframe: Timeframe
    kind: Literal["high", "low"]
    price: float
    pivot_time: datetime
    confirmed_at: datetime
    source_index: int
    prominence: float | None = None

class StructureSnapshot(BaseModel):
    timeframe: Timeframe
    confirmed_pivots: list[Pivot] = Field(default_factory=list)
    high_sequence: Literal["HH", "LH", "NA"] = "NA"
    low_sequence: Literal["HL", "LL", "NA"] = "NA"
    trend: Direction = "neutral"
    fingerprint: str

class SmcEvidence(BaseModel):
    available: bool = False
    last_bos: dict[str, Any] | None = None
    last_choch: dict[str, Any] | None = None
    liquidity: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None

class TrendEvidence(BaseModel):
    available: bool = False
    supports: list[float] = Field(default_factory=list)
    resistances: list[float] = Field(default_factory=list)
    support_slope: float | None = None
    resistance_slope: float | None = None
    error: str | None = None

class GeometryEvidence(BaseModel):
    channel_type: str = "unknown"
    support_slope: float | None = None
    resistance_slope: float | None = None
    parallelism: float | None = None
    convergence: float | None = None
    pattern_hints: list[str] = Field(default_factory=list)

class EvidencePack(BaseModel):
    symbol: str
    timeframe: Timeframe
    generated_at: datetime
    last_closed_bar: datetime
    bars_hash: str
    structure: StructureSnapshot
    smc: SmcEvidence
    trend: TrendEvidence
    geometry: GeometryEvidence
    recent_ohlc: list[dict[str, Any]]
    advisory_note: str = (
        "Python/library evidence is advisory context only. It must never open, "
        "close, block or choose a trade. Claude makes the trading decision."
    )

class WaveLeg(BaseModel):
    label: str
    start_price: float
    end_price: float
    start_time: datetime | None = None
    end_time: datetime | None = None

class ElliottCount(BaseModel):
    degree: str
    direction: Direction
    current_wave: str
    legs: list[WaveLeg] = Field(default_factory=list)
    invalidation: float | None = None
    summary: str = ""

class WatchCondition(BaseModel):
    timeframe: Timeframe
    kind: Literal["close_above", "close_below", "touch_above", "touch_below"]
    level: float
    direction: Literal["LONG", "SHORT"]
    rationale: str = ""

class ClaudeDecision(BaseModel):
    # Every top-level field is required in JSON. Nullable fields must still be
    # returned as explicit null, allowing field-level repair to detect omissions.
    action: Action
    primary_count: ElliottCount
    alternate_count: ElliottCount | None
    structure_assessment: str
    support_resistance_assessment: str
    channel_assessment: str
    pattern_assessment: list[str]
    evidence_disagreements: list[str]
    watch_conditions: list[WatchCondition]
    entry: float | None
    stop: float | None
    target: float | None
    confidence: Literal["low", "medium", "high"]
    rationale_brief: str
    evidence_interpretation: str

    @model_validator(mode="after")
    def _trade_geometry(self):
        if self.action in {"READY_LONG", "READY_SHORT"}:
            if None in (self.entry, self.stop, self.target):
                raise ValueError("READY_* requires entry, stop and target")
        return self

class TimeframeMemory(BaseModel):
    timeframe: Timeframe
    evidence_fingerprint: str
    last_closed_bar: datetime
    elliott: ElliottCount
    alternate: ElliottCount | None = None
    claude_context: dict[str, Any] = Field(default_factory=dict)

class MarketMemory(BaseModel):
    schema_version: int = 1
    memory_version: int = 0
    memory_id: str
    symbol: str = "XAUUSD"
    updated_at: datetime
    timeframes: dict[str, TimeframeMemory] = Field(default_factory=dict)
    last_decision: ClaudeDecision | None = None
    active_watches: list[WatchCondition] = Field(default_factory=list)
    last_rebase_at: datetime | None = None

class ClaudeUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    estimated_cost_usd: float = 0.0
