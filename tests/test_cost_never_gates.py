import unittest, pathlib
class T(unittest.TestCase):
 def test_no_old_budget_gate_symbols(self):
  text='\n'.join(p.read_text(encoding='utf-8') for p in pathlib.Path('waveframe').glob('*.py'))
  for token in ['TARGET_DAILY_USD','SOFT_DAILY_USD','EVENT_HARD_CEILING_USD','MAX_M30_EVENT_DECISIONS_PER_DAY','PERMANENT_BLOCKED']:
   self.assertNotIn(token,text)
if __name__=='__main__': unittest.main()
