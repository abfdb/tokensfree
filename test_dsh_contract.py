import unittest
from check_dsh_contract import probe

class DshContractTest(unittest.TestCase):
    def test_http_matrix(self):
        result=probe()
        self.assertFalse(result['dsh_executed'])
        self.assertFalse(result['external_calls'])
        self.assertEqual(len(result['cases']),8)
        for case in result['cases']:
            self.assertTrue(case['expected_behavior_verified'],case['case'])
            if case['http_status'] != 200:
                self.assertEqual(case['gateway_provider_attempts'],0)
