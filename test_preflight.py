"""Offline adversarial tests; no credentials or external network are used."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from preflight import inspect_config, parse_config, PROVIDER, ENDPOINT

NOW = 1800000000

def valid_config():
    return dict(provider=PROVIDER, endpoint=ENDPOINT, model='gemini-test-model',
                credential_store='os-keyring', billing_tier='free', paid_fallback=False,
                region_supported=True, data_terms_accepted=True,
                free_model_verified=True, hard_stop_verified=True, checked_at=NOW)

class PreflightTests(unittest.TestCase):
    def assert_closed(self, result):
        self.assertIs(result['eligible'], False)
        self.assertIs(result['can_call'], False)
        self.assertIn('real_calls_disabled', result['blockers'])

    def test_complete_declaration_never_authorizes(self):
        result = inspect_config(valid_config(), NOW)
        self.assert_closed(result)
        self.assertTrue(result['declaration_checks_complete'])
        self.assertFalse(result['account_verified'])
        self.assertIn('unverified_account_declarations', result['blockers'])

    def test_incomplete_and_wrong_schema(self):
        for value in (None, [], 'secret-marker', 0, {}, valid_config() | {'api_key':'secret-marker'}):
            with self.subTest(value=type(value)):
                result = inspect_config(value, NOW)
                self.assert_closed(result)
                self.assertFalse(result['declaration_checks_complete'])
                self.assertNotIn('secret-marker', json.dumps(result))
        for field in valid_config():
            value = valid_config(); del value[field]
            self.assert_closed(inspect_config(value, NOW))

    def test_strict_field_types(self):
        for field in ('paid_fallback','region_supported','data_terms_accepted','free_model_verified','hard_stop_verified'):
            for value in (0, 1, 'true', None, [], {}):
                result = inspect_config(valid_config() | {field:value}, NOW)
                self.assert_closed(result)
                self.assertFalse(result['declaration_checks_complete'])

    def test_strings_and_endpoint_allowlist(self):
        for field, value in [('endpoint',ENDPOINT+'.evil.invalid'),('endpoint',ENDPOINT+'/'),
                             ('provider','other'),('model','gemini-../secret'),
                             ('model','gemini-'),('model',{}),('credential_store','env'),
                             ('billing_tier','paid')]:
            result = inspect_config(valid_config() | {field:value}, NOW)
            self.assert_closed(result)
            self.assertFalse(result['declaration_checks_complete'])

    def test_time_boundaries_and_invalid_types(self):
        for checked in (NOW+1,NOW-86401,None,True,'1800000000',[],{},float('nan'),float('inf')):
            result = inspect_config(valid_config() | {'checked_at':checked}, NOW)
            self.assert_closed(result)
            self.assertIn('declaration_stale_or_invalid',result['blockers'])
        for checked in (NOW,NOW-86400):
            self.assertTrue(inspect_config(valid_config() | {'checked_at':checked}, NOW)['declaration_checks_complete'])
        for now in (None,True,'bad',float('nan'),float('inf')):
            if now is None: continue
            self.assertIn('declaration_stale_or_invalid',inspect_config(valid_config(),now)['blockers'])

    def test_huge_timestamp_rejected_without_crash(self):
        # JSON permits integers much larger than floating-point ranges.
        result = inspect_config(valid_config() | {'checked_at':10**1000}, NOW)
        self.assert_closed(result)
        self.assertIn('declaration_stale_or_invalid',result['blockers'])

    def test_sensitive_values_never_echoed(self):
        for field in valid_config():
            result = inspect_config(valid_config() | {field:'secret-marker'}, NOW)
            self.assertNotIn('secret-marker',json.dumps(result))
            self.assert_closed(result)

    def test_parser_rejects_duplicates_constants_and_overflow_size(self):
        for raw in (b'{"x":1,"x":2}',b'{"x":NaN}',b'{"x":Infinity}',b'x'*8193,b'\xff',b'{'):
            with self.assertRaises((ValueError,UnicodeError)):
                parse_config(raw)

    def run_cli(self, raw):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json';path.write_bytes(raw)
            return subprocess.run([sys.executable,str(Path(__file__).with_name('preflight.py')),str(path)],capture_output=True,text=True,timeout=5)

    def test_cli_success_still_blocked(self):
        import time
        result=self.run_cli(json.dumps(valid_config() | {'checked_at':time.time()}).encode())
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stderr,'')
        self.assert_closed(json.loads(result.stdout))

    def test_cli_invalid_no_secret_echo(self):
        for raw in (b'{"api_key":"secret-marker"}',b'secret-marker',b'['*1100):
            result=self.run_cli(raw)
            self.assertEqual(result.returncode,2)
            self.assertNotIn('secret-marker',result.stdout+result.stderr)
            self.assertEqual(result.stderr,'')
            self.assert_closed(json.loads(result.stdout))

    def test_cli_huge_number_returns_safe_json(self):
        result=self.run_cli(json.dumps(valid_config() | {'checked_at':10**1000}).encode())
        self.assertEqual(result.returncode,2)
        self.assertEqual(result.stderr,'')
        self.assert_closed(json.loads(result.stdout))

if __name__ == '__main__': unittest.main()
