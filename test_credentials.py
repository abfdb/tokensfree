"""Synthetic-only credential boundary tests. Never consult an OS vault."""
import traceback
import unittest
from unittest.mock import patch
from credentials import Binding, CredentialBroker, CredentialUnavailable, SyntheticVault, DisabledSystemVault

PROVIDER='google-gemini-developer'
ORIGIN='https://generativelanguage.googleapis.com'
MODEL='gemini-fixture'
FIXTURE='tokensfree-synthetic-credential-not-a-key'

def binding(**overrides):
    fields=dict(alias='fixture',provider=PROVIDER,origin=ORIGIN,model=MODEL,expires_at=200)
    fields.update(overrides)
    return Binding(**fields)

def resolve(broker, **overrides):
    fields=dict(alias='fixture',provider=PROVIDER,origin=ORIGIN,model=MODEL,now=100)
    fields.update(overrides)
    return broker.resolve_fixture(**fields)

class CredentialTests(unittest.TestCase):
    def test_default_hard_block_before_backend(self):
        broker=CredentialBroker([binding()])
        with patch.object(broker._vault,'read') as read:
            with self.assertRaisesRegex(CredentialUnavailable,'real_credentials_disabled'):
                resolve(broker)
            read.assert_not_called()

    def test_disabled_vault(self):
        with self.assertRaisesRegex(CredentialUnavailable,'system_vault_not_implemented'):
            DisabledSystemVault().read('fixture')

    def test_fixture_only_and_redaction(self):
        broker=CredentialBroker([binding()],fixture=True)
        lease=resolve(broker)
        self.assertEqual(lease.value,FIXTURE)
        for display in (repr(lease),str(lease),repr([lease]),repr({'lease':lease})):
            self.assertNotIn(FIXTURE,display)
            self.assertIn('redacted',display)
        self.assertEqual(SyntheticVault().read('fixture'),FIXTURE)
        with self.assertRaises(CredentialUnavailable): SyntheticVault().read('other')

    def test_scope_mismatch_before_backend(self):
        broker=CredentialBroker([binding()],fixture=True)
        for overrides in ({'alias':'other'},{'alias':[]},{'alias':None},
                          {'provider':'other'},{'origin':ORIGIN+'.evil.invalid'},
                          {'origin':ORIGIN+'/'},{'model':'gemini-other'},
                          {'provider':[]},{'origin':None},{'model':{}}):
            with patch.object(broker._vault,'read') as read:
                with self.assertRaisesRegex(CredentialUnavailable,'credential_scope_denied'):
                    resolve(broker,**overrides)
                read.assert_not_called()

    def test_expired_invalid_now_before_backend(self):
        broker=CredentialBroker([binding()],fixture=True)
        for now in (200,201,float('inf'),float('nan'),10**1000,True,None,'100',[],{}):
            with patch.object(broker._vault,'read') as read:
                with self.assertRaisesRegex(CredentialUnavailable,'credential_expired'):
                    resolve(broker,now=now)
                read.assert_not_called()
        self.assertEqual(resolve(broker,now=199.99).value,FIXTURE)

    def test_invalid_binding_fields(self):
        invalid=[('alias','../secret'),('alias',''),('alias','a'*41),('alias',[]),
                 ('provider','secret-marker'),('origin',ORIGIN+'@evil.invalid'),
                 ('model','gemini-../secret-marker'),('model',{}),
                 ('expires_at',True),('expires_at',float('nan')),
                 ('expires_at',float('inf')),('expires_at',10**1000)]
        for key,value in invalid:
            with self.assertRaises(ValueError) as error: binding(**{key:value})
            self.assertNotIn('secret-marker',str(error.exception))

    def test_constructor_validation(self):
        for value in (1,0,'true',None):
            with self.assertRaises(ValueError): CredentialBroker(fixture=value)
        with self.assertRaises(ValueError): CredentialBroker([binding(),binding()])
        with self.assertRaises(ValueError): CredentialBroker([{'raw_secret':'secret-marker'}])
        with self.assertRaises(TypeError): CredentialBroker(raw_secret='secret-marker')

    def test_backend_exception_diagnostic_hidden(self):
        broker=CredentialBroker([binding()],fixture=True)
        with patch.object(broker._vault,'read',side_effect=RuntimeError('secret-marker')):
            try: resolve(broker)
            except CredentialUnavailable as error:
                output=''.join(traceback.format_exception(error))
                self.assertNotIn('secret-marker',output)
                self.assertEqual(str(error),'credential_unavailable')
            else: self.fail('backend error accepted')

    def test_backend_nonfixture_values_rejected(self):
        broker=CredentialBroker([binding()],fixture=True)
        for value in ('secret-marker','',None,b'secret-marker',[],{},123):
            with patch.object(broker._vault,'read',return_value=value):
                with self.assertRaisesRegex(CredentialUnavailable,'non_fixture_credential_rejected') as error:
                    resolve(broker)
                self.assertNotIn('secret-marker',str(error.exception))

    def test_backend_object_cannot_spoof_fixture_equality(self):
        class Spoof:
            def __eq__(self,other): return True
            def __ne__(self,other): return False
        broker=CredentialBroker([binding()],fixture=True)
        with patch.object(broker._vault,'read',return_value=Spoof()):
            with self.assertRaisesRegex(CredentialUnavailable,'non_fixture_credential_rejected'):
                resolve(broker)

    def test_resolution_has_no_io_dependency(self):
        # Guards exercise the complete supported path, with no real keyring loaded.
        with patch('builtins.open',side_effect=AssertionError('file read forbidden')), \
             patch('os.getenv',side_effect=AssertionError('env read forbidden')), \
             patch('socket.socket',side_effect=AssertionError('network forbidden')):
            self.assertEqual(resolve(CredentialBroker([binding()],fixture=True)).value,FIXTURE)
            with self.assertRaises(CredentialUnavailable): resolve(CredentialBroker([binding()]))

if __name__=='__main__': unittest.main()
