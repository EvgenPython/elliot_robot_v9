import unittest,tempfile,json
from waveframe.claude_gateway import ClaudeGateway
class T(unittest.TestCase):
 def test_partial_repairs_until_valid(self):
  calls=[]
  full={
   'action':'WAIT',
   'primary_count':{'degree':'minor','direction':'neutral','current_wave':'unclear','legs':[],'summary':'x'},
   'alternate_count':None,
   'structure_assessment':'neutral',
   'support_resistance_assessment':'levels noted',
   'channel_assessment':'none',
   'pattern_assessment':[],
   'evidence_disagreements':[],
   'watch_conditions':[],'entry':None,'stop':None,'target':None,'confidence':'medium','rationale_brief':'x','evidence_interpretation':'x'
  }
  def tr(prefix,prompt):
   calls.append(prompt)
   if len(calls)==1: body={'action':'WAIT'}
   else: body={k:v for k,v in full.items() if k!='action'}
   return {'text':json.dumps(body),'usage':{'input_tokens':10,'output_tokens':10},'model':'claude-sonnet-5'}
  with tempfile.TemporaryDirectory() as d:
   g=ClaudeGateway(d,transport=tr); dec=g.ask_until_valid('prefix',{'x':1},sleep=lambda x:None)
   self.assertEqual('WAIT',dec.action); self.assertGreaterEqual(len(calls),2); self.assertIn('missing top-level fields',calls[1])
if __name__=='__main__': unittest.main()
