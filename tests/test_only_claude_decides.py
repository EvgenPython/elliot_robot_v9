import unittest
from waveframe.models import *
from waveframe.decision import claude_trade_intent
from datetime import datetime,timezone
class T(unittest.TestCase):
 def count(self): return ElliottCount(degree='minor',direction='bullish',current_wave='3')
 def test_wait_never_opens_even_if_libraries_would_be_bullish(self):
  d=ClaudeDecision(action='WAIT',primary_count=self.count(),alternate_count=None,structure_assessment='x',support_resistance_assessment='x',channel_assessment='x',pattern_assessment=[],evidence_disagreements=[],watch_conditions=[],entry=None,stop=None,target=None,confidence='medium',rationale_brief='x',evidence_interpretation='x')
  self.assertIsNone(claude_trade_intent(d))
 def test_ready_long_is_source_of_intent(self):
  d=ClaudeDecision(action='READY_LONG',primary_count=self.count(),alternate_count=None,structure_assessment='x',support_resistance_assessment='x',channel_assessment='x',pattern_assessment=[],evidence_disagreements=[],watch_conditions=[],entry=10,stop=9,target=13,confidence='medium',rationale_brief='x',evidence_interpretation='x')
  self.assertEqual('LONG',claude_trade_intent(d))
if __name__=='__main__': unittest.main()
