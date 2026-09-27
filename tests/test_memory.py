import unittest,tempfile
from waveframe.memory import MarketMemoryStore
class T(unittest.TestCase):
 def test_memory_persists_and_versions(self):
  with tempfile.TemporaryDirectory() as d:
   s=MarketMemoryStore(d); m=s.load(); old=m.memory_id; s.save(m,'test'); m2=s.load(); self.assertEqual(old,m2.memory_id); self.assertEqual(1,m2.memory_version)
if __name__=='__main__': unittest.main()
