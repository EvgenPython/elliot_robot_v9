import unittest
from waveframe.logging import redact
class T(unittest.TestCase):
 def test_secrets_redacted(self): self.assertEqual('[REDACTED]',redact({'api_key':'abc'})['api_key'])
if __name__=='__main__': unittest.main()
