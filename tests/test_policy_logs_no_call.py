import unittest,tempfile,pandas as pd
from waveframe.logging import AuditLogger
from waveframe.policy import AnalysisPolicy
from waveframe.models import *
from datetime import datetime,timezone
class T(unittest.TestCase):
 def test_no_call_is_logged(self):
  with tempfile.TemporaryDirectory() as d:
   lg=AuditLogger(d); p=AnalysisPolicy(lg); now=datetime.now(timezone.utc)
   s=StructureSnapshot(timeframe='M30',fingerprint='x')
   e=EvidencePack(symbol='XAUUSD',timeframe='M30',generated_at=now,last_closed_bar=now,bars_hash='b',structure=s,smc=SmcEvidence(),trend=TrendEvidence(),geometry=GeometryEvidence(),recent_ohlc=[])
   mem=MarketMemory(memory_id='m',updated_at=now,timeframes={'M30':TimeframeMemory(timeframe='M30',evidence_fingerprint='x',last_closed_bar=now,elliott=ElliottCount(degree='x',direction='neutral',current_wave='x'))})
   self.assertFalse(p.on_closed_bar(e,mem))
   files=list((__import__('pathlib').Path(d)/'logs'/'decisions').glob('*.jsonl')); self.assertTrue(files); self.assertIn('NO_MATERIAL_DELTA',files[0].read_text())
if __name__=='__main__': unittest.main()
