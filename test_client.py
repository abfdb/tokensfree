import threading
import unittest
from unittest.mock import patch
from client import LocalClient
from server import Gateway, make_server

class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()
    def setUp(self):
        self.server.gateway = Gateway()
        self.client = LocalClient(self.server.server_port)
    def test_end_to_end_and_dedup(self):
        a = self.client.complete('test', request_key='repeat')
        b = self.client.complete('test', request_key='repeat')
        self.assertEqual(a,b)
        self.assertEqual(a['model'], 'mock-beta')
        self.assertEqual(len(self.server.gateway.events),2)
    def test_server_authorization_wins(self):
        self.server.gateway = Gateway({'mock-alpha'})
        with self.assertRaises(RuntimeError):
            self.client.complete('test', allowed_providers=['mock-beta'])
        self.assertEqual(self.server.gateway.events[1]['result'], 'policy:not_authorized')
    def test_empty_server_authorization(self):
        g = Gateway(set())
        with patch('server.MockAdapter') as adapter:
            status, _ = g.run({'model':'tokensfree-auto','messages':[{'role':'user','content':'x'}]})
            self.assertEqual(status,503); adapter.assert_not_called()
    def test_invalid_server_provider(self):
        with self.assertRaises(ValueError): Gateway({'real-api'})
    def test_client_validation(self):
        for port in [True, 0, 65536, '443']:
            with self.assertRaises(ValueError): LocalClient(port)
        for timeout in [0,31,float('nan')]:
            with self.assertRaises(ValueError): LocalClient(timeout=timeout)
        with self.assertRaises(ValueError): self.client.complete('x'*4001)
    def test_no_retry(self):
        with patch.object(self.client,'_request',side_effect=TimeoutError) as call:
            with self.assertRaises(TimeoutError): self.client.complete('x')
            self.assertEqual(call.call_count,1)

    def test_reject_bad_keys_before_io(self):
        with patch.object(self.client, '_request') as call:
            for key in ['', False, 0, [], 'bad key']:
                with self.assertRaises(ValueError): self.client.complete('x', request_key=key)
            call.assert_not_called()
    def test_generated_key_available_after_timeout(self):
        with patch.object(self.client, '_request', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError): self.client.complete('x')
        self.assertEqual(len(self.client.last_request_key), 32)

    def test_key_not_read_from_mutable_observer(self):
        calls = []
        def request(method, path, body=None, headers=None):
            if method == 'GET':
                self.client.last_request_key = 'concurrent-other-key'
                return {'token':'fixture_'*5}
            calls.append(headers['Idempotency-Key'])
            return {}
        with patch.object(self.client, '_request', side_effect=request):
            self.client.complete('x', request_key='original')
        self.assertEqual(calls, ['original'])
