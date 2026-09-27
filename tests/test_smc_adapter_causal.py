import unittest,inspect
import waveframe.smc_adapter as m
class T(unittest.TestCase):
 def test_adapter_never_calls_library_swing_detector(self):
  src=inspect.getsource(m.analyze_smc)
  self.assertNotIn('swing_highs_lows(',src)
if __name__=='__main__': unittest.main()
