import unittest
from datetime import datetime, timezone, timedelta
from waveframe.models import EvidencePack, StructureSnapshot, SmcEvidence, TrendEvidence, GeometryEvidence, MarketMemory, TimeframeMemory, ElliottCount
from waveframe.delta import evidence_changed, material_fingerprint


def pack(at, fp="same"):
    return EvidencePack(
        symbol="XAUUSD",
        timeframe="M30",
        generated_at=at,
        last_closed_bar=at,
        bars_hash=str(at),
        structure=StructureSnapshot(timeframe="M30", fingerprint=fp),
        smc=SmcEvidence(),
        trend=TrendEvidence(),
        geometry=GeometryEvidence(channel_type="horizontal_channel"),
        recent_ohlc=[{"close": 4000.0}],
    )


class MaterialDeltaTests(unittest.TestCase):
    def test_new_bar_alone_does_not_force_claude_call(self):
        t = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
        p1 = pack(t)
        mem = MarketMemory(memory_id="x", updated_at=t)
        mem.timeframes["M30"] = TimeframeMemory(
            timeframe="M30",
            evidence_fingerprint=material_fingerprint(p1),
            last_closed_bar=t,
            elliott=ElliottCount(degree="M30", direction="neutral", current_wave="x"),
        )
        p2 = pack(t + timedelta(minutes=30))
        self.assertFalse(evidence_changed(p2, mem))

    def test_structure_change_is_material(self):
        t = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)
        p1 = pack(t, "a")
        mem = MarketMemory(memory_id="x", updated_at=t)
        mem.timeframes["M30"] = TimeframeMemory(
            timeframe="M30",
            evidence_fingerprint=material_fingerprint(p1),
            last_closed_bar=t,
            elliott=ElliottCount(degree="M30", direction="neutral", current_wave="x"),
        )
        self.assertTrue(evidence_changed(pack(t + timedelta(minutes=30), "b"), mem))


if __name__ == "__main__":
    unittest.main()
