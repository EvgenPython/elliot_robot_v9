import unittest
from waveframe.prompts import SYSTEM_RULES
class T(unittest.TestCase):
 def test_prompt_says_libraries_are_advisory(self):
  self.assertIn('advisory',SYSTEM_RULES.lower()); self.assertIn('Trading decisions are yours alone',SYSTEM_RULES)
if __name__=='__main__': unittest.main()
