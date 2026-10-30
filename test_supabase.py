"""Offline tests of Supabase API-key formats and atomic shared leaderboard RPC."""
import io
import os
import unittest
from unittest.mock import patch
import leaderboard

class MockResponse:
    def __init__(self, body): self.body=body
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def read(self):return self.body

class SupabaseTests(unittest.TestCase):
    def test_new_secret_key_uses_apikey_not_jwt_bearer(self):
        requests=[]
        def fake(request,timeout):
            requests.append(request)
            return MockResponse(b'[]')
        with patch.dict(os.environ,{'SUPABASE_URL':'https://my-test.supabase.co', 'SUPABASE_SECRET_KEY':'sb_secret_test-placeholder','SUPABASE_SERVICE_ROLE_KEY':''}):
            with patch('urllib.request.urlopen',side_effect=fake):
                self.assertEqual(leaderboard.top()['entries'],[])
        self.assertEqual(len(requests),1)
        self.assertEqual(requests[0].get_header('Apikey'),'sb_secret_test-placeholder')
        self.assertIsNone(requests[0].get_header('Authorization'))
        self.assertIn('limit=20',requests[0].full_url)

    def test_legacy_service_role_jwt_bearer(self):
        requests=[]
        def fake(request,timeout):
            requests.append(request)
            return MockResponse(b'[]')
        with patch.dict(os.environ,{'SUPABASE_URL':'https://my-test.supabase.co', 'SUPABASE_SECRET_KEY':'','SUPABASE_SERVICE_ROLE_KEY':'eyJtestjwt'}):
            with patch('urllib.request.urlopen',side_effect=fake):
                leaderboard.top('RU')
        self.assertEqual(requests[0].get_header('Authorization'),'Bearer eyJtestjwt')
        self.assertIn('country=eq.RU',requests[0].full_url)

    def test_atomic_rpc_posts_and_preserves_true_false(self):
        requests=[]
        def fake(request,timeout):
            requests.append(request)
            return MockResponse(b'true' if len(requests)==1 else b'false')
        with patch.dict(os.environ,{'SUPABASE_URL':'https://my-test.supabase.co', 'SUPABASE_SECRET_KEY':'sb_secret_example','SUPABASE_SERVICE_ROLE_KEY':''}):
            with patch('urllib.request.urlopen',side_effect=fake):
                self.assertTrue(leaderboard.record('Тест','RU',20,2300)['saved'])
                self.assertFalse(leaderboard.record('Тест','RU',10,900)['saved'])
        self.assertTrue(all('/rest/v1/rpc/ministry_submit_score' in req.full_url for req in requests))
        self.assertTrue(all(req.method=='POST' for req in requests))
        self.assertIn(b'"p_score": 2300',requests[0].data)

    def test_rpc_wrong_reply_is_error(self):
        with patch.dict(os.environ,{'SUPABASE_URL':'https://my-test.supabase.co', 'SUPABASE_SECRET_KEY':'sb_secret_example','SUPABASE_SERVICE_ROLE_KEY':''}):
            with patch('urllib.request.urlopen',return_value=MockResponse(b'{}')):
                with self.assertRaises(ValueError):leaderboard.record('Тест','RU',15,1200)

    def test_config_requires_url_and_key(self):
        with patch.dict(os.environ,{'SUPABASE_URL':'','SUPABASE_SECRET_KEY':'sb_secret_xxx','SUPABASE_SERVICE_ROLE_KEY':''}):
            self.assertEqual(leaderboard.mode(),'local')

    def test_sql_has_unique_and_private_rpc(self):
        from pathlib import Path
        sql=Path(__file__).with_name('supabase.sql').read_text(encoding='utf-8')
        self.assertIn('ministry_scores_unique_nick_country',sql)
        self.assertIn('REVOKE ALL ON FUNCTION',sql)
        self.assertIn('ENABLE ROW LEVEL SECURITY',sql)
        self.assertIn('ON CONFLICT (player_key,country)',sql)

if __name__=='__main__':unittest.main()

class HttpIntegrationTests(unittest.TestCase):
    def test_live_local_route_and_global_check_with_mocked_cloud(self):
        import socket, threading, urllib.request
        from http.server import ThreadingHTTPServer
        from server import Handler
        with ThreadingHTTPServer(('127.0.0.1',0), Handler) as server:
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            try:
                with patch.dict(os.environ,{'SUPABASE_URL':'','SUPABASE_SECRET_KEY':'','SUPABASE_SERVICE_ROLE_KEY':''}):
                    import json
                    local=json.load(urllib.request.urlopen(base+'/api/leaderboard/check',timeout=4))
                    self.assertFalse(local['connected'])
                    self.assertEqual(local['source'],'local')
                with patch.dict(os.environ,{'SUPABASE_URL':'https://example.supabase.co','SUPABASE_SECRET_KEY':'sb_secret_fake','SUPABASE_SERVICE_ROLE_KEY':''}):
                    with patch.object(leaderboard,'supabase_request',return_value=[]):
                        cloud=json.load(urllib.request.urlopen(base+'/api/leaderboard/check',timeout=4))
                        self.assertTrue(cloud['connected'])
                        self.assertEqual(cloud['source'],'global')
                        entries=json.load(urllib.request.urlopen(base+'/api/leaderboard?country=JP',timeout=4))
                        self.assertEqual(entries['source'],'global')
                        self.assertEqual(entries['entries'],[])
            finally:
                server.shutdown();thread.join(timeout=5)
