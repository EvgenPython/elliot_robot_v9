import unittest,pandas as pd
from waveframe.causal_structure import confirmed_pivots
class T(unittest.TestCase):
 def test_future_append_does_not_change_old_confirmed_pivots(self):
  base=pd.Timestamp('2026-01-01T00:00:00Z'); vals=[1,2,5,2,1,3,2,1,9,1]
  df=pd.DataFrame([{"open_time":base+pd.Timedelta(minutes=15*i),"close_time":base+pd.Timedelta(minutes=15*(i+1)),"open":v,"high":v+.1,"low":v-.1,"close":v} for i,v in enumerate(vals)])
  a=confirmed_pivots(df.iloc[:7].copy(),'M15',2,2)
  b=[p for p in confirmed_pivots(df.copy(),'M15',2,2) if p.confirmed_at<=df.iloc[6].close_time.to_pydatetime()]
  self.assertEqual([(p.kind,p.price,p.pivot_time) for p in a],[(p.kind,p.price,p.pivot_time) for p in b])
if __name__=='__main__': unittest.main()
