import unittest,pandas as pd
from waveframe.causal_structure import confirmed_pivots
class T(unittest.TestCase):
 def df(self,n):
  base=pd.Timestamp('2026-01-01T00:00:00Z'); vals=[1,2,5,2,1,2,3][:n]
  return pd.DataFrame([{"open_time":base+pd.Timedelta(minutes=15*i),"close_time":base+pd.Timedelta(minutes=15*(i+1)),"open":v,"high":v+.1,"low":v-.1,"close":v} for i,v in enumerate(vals)])
 def test_pivot_not_known_before_right_bars(self):
  self.assertEqual([],confirmed_pivots(self.df(4),'M15',2,2))
  piv=confirmed_pivots(self.df(5),'M15',2,2)
  hi=[p for p in piv if p.kind=='high'][0]
  self.assertEqual(2,hi.source_index); self.assertEqual(self.df(5).iloc[4].close_time.to_pydatetime(),hi.confirmed_at)
if __name__=='__main__': unittest.main()
