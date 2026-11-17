import unittest
from unittest.mock import patch
from dataclasses import replace
from providers import ProviderSpec, evaluate, MockAdapter, Outcome
from server import Gateway
P={'model':'tokensfree-auto','messages':[{'role':'user','content':'test'}]}
class PolicyTests(unittest.TestCase):
 def setUp(self): self.spec=ProviderSpec('a','model-a','mock_zero_cost',200)
 def check(self,spec,reason):self.assertEqual(evaluate(spec,frozenset({'a'}),100).reason,reason)
 def test_mock_allowed(self):self.assertTrue(evaluate(self.spec,frozenset({'a'}),100).allowed)
 def test_unknown_rejected(self):self.check(replace(self.spec,cost_evidence='unknown'),'unverified_free_status')
 def test_paid_rejected(self):self.check(replace(self.spec,cost_evidence='paid'),'unverified_free_status')
 def test_expired_rejected(self):self.check(replace(self.spec,evidence_expires_at=100),'expired_free_evidence')
 def test_invalid_expiry_rejected(self):
  for x in (float('inf'),float('nan')):self.check(replace(self.spec,evidence_expires_at=x),'expired_free_evidence')
 def test_real_provider_hard_block(self):self.check(replace(self.spec,simulation=False),'real_provider_not_implemented')
 def test_permission(self):self.assertEqual(evaluate(self.spec,frozenset(),100).reason,'not_authorized')
 def test_capability(self):self.assertEqual(evaluate(self.spec,frozenset({'a'}),100,frozenset({'vision'})).reason,'capability_mismatch')
 def test_invalid_clock(self):
  for now in (float('nan'),float('inf')):self.assertEqual(evaluate(self.spec,frozenset({'a'}),now).reason,'invalid_clock')
 def test_disabled(self):self.check(replace(self.spec,enabled=False),'disabled')
 def test_adapter(self):self.assertEqual(MockAdapter('limited').complete([]),Outcome.LIMITED)
class IntegrationTests(unittest.TestCase):
 def test_unknown_skipped_before_adapter(self):
  g=Gateway();g.reset('unknown_cost')
  with patch('server.MockAdapter',wraps=MockAdapter) as adapter:code,result=g.run(P);self.assertEqual(adapter.call_count,1)
  self.assertEqual(code,200);self.assertEqual(result['model'],'mock-beta');self.assertEqual(result['tokensfree']['trace'][0]['result'],'policy:unverified_free_status')
 def test_all_unknown_stop_without_call(self):
  g=Gateway();g.reset('all_unknown_cost')
  with patch('server.MockAdapter') as adapter:self.assertEqual(g.run(P)[0],503);adapter.assert_not_called()
 def test_expiry_fallback(self):
  g=Gateway();g.reset('expired_evidence');self.assertEqual(g.run(P)[1]['model'],'mock-beta')
 def test_no_unauthorized_fallback(self):
  g=Gateway();code,data=g.run(P|{'allowed_providers':['mock-alpha']})
  self.assertEqual(code,503);self.assertEqual(data['trace'][1]['result'],'policy:not_authorized')
 def test_snapshot_reports_blocked(self):
  g=Gateway();g.reset('all_unknown_cost');s=g.snapshot()
  self.assertTrue(all(p['state']=='blocked' for p in s['providers']))
  self.assertTrue(all(p['policy_reason']=='unverified_free_status' for p in s['providers']))
 def test_invalid_allowlist(self):
  for value in ([],None,'mock-alpha',['evil'],[{}]):
   with self.assertRaises(ValueError):Gateway().run(P|{'allowed_providers':value})
if __name__=='__main__':unittest.main()
