import unittest
from waveframe.cache import CacheManager
class T(unittest.TestCase):
 def test_status(self):
  c=CacheManager(); self.assertEqual('HIT',c.cache_status({'cache_read_input_tokens':10})); self.assertEqual('CREATED_OR_REBUILT',c.cache_status({'cache_creation_input_tokens':10}))
if __name__=='__main__': unittest.main()
