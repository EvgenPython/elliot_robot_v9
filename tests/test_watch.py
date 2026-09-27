import unittest
from waveframe.models import WatchCondition
from waveframe.watch import evaluate_watch
class T(unittest.TestCase):
 def test_exact_claude_trigger_is_followed(self):
  w=WatchCondition(timeframe='M15',kind='close_above',level=4277.50,direction='LONG')
  self.assertTrue(evaluate_watch(w,{"close":4279.55,"high":4280,"low":4270}))
if __name__=='__main__': unittest.main()
