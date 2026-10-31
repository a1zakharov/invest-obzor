"""Real HTTP checks; run separately because binding a port may require permission."""
import csv
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import closing
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import tbank
import access_control


class QuietHandler(server.Handler):
    def log_message(self, *_):
        pass


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.DB_PATH = Path(cls.temp.name) / 'http.sqlite3'
        server.init_db()
        cls.httpd = server.ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, path, method='GET', data=None, headers=None, raw=False):
        conn = HTTPConnection('127.0.0.1', self.port, timeout=5)
        if data is not None and not raw:
            data = json.dumps(data).encode()
        headers = {'Content-Type': 'application/json', **(headers or {})}
        conn.request(method, path, data, headers)
        response = conn.getresponse()
        payload = response.read()
        status, returned_headers = response.status, dict(response.getheaders())
        conn.close()
        if returned_headers.get('Content-Type', '').startswith('application/json'):
            payload = json.loads(payload)
        return status, payload, returned_headers

    def setUp(self):
        status, result, _ = self.request('/api/portfolios', 'POST', {'name': 'HTTP test', 'currency': 'RUB'})
        self.assertEqual(status, 200)
        self.pid = result['id']
        self.base = f'/api/portfolios/{self.pid}'

    def tearDown(self):
        self.request(self.base, 'DELETE')

    def test_static_application_and_all_assets(self):
        for path in ['/', '/app.js', '/broker.js', '/styles.css', '/favicon.svg', '/example.csv']:
            with self.subTest(path=path):
                status, body, headers = self.request(path)
                self.assertEqual(status, 200)
                self.assertGreater(len(body), 50)
                self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertEqual(self.request('/../../server.py')[0], 404)
        self.assertEqual(self.request('/not-found')[0], 404)

    def test_health_and_empty_state(self):
        self.assertEqual(self.request('/api/health')[1]['status'], 'ok')
        status, result, _ = self.request(self.base)
        self.assertEqual(status, 200)
        self.assertEqual(result['summary']['equity'], '0')

    def test_portfolio_asset_transaction_crud(self):
        status, asset, _ = self.request(self.base+'/assets', 'POST', {'ticker': 'AAA', 'name': 'Asset', 'price': 200})
        self.assertEqual(status, 200)
        aid = asset['id']
        self.assertEqual(self.request(self.base+'/transactions', 'POST', {'type': 'deposit', 'date': '2025-01-01', 'amount': 1000})[0], 200)
        status, trade, _ = self.request(self.base+'/transactions', 'POST', {'type': 'buy', 'date': '2025-01-02', 'asset_id': aid, 'quantity': 2, 'price': 100})
        self.assertEqual(status, 200)
        result = self.request(self.base)[1]
        self.assertEqual(result['summary']['equity'], '1200')
        self.assertEqual(Decimal(result['summary']['profit']), Decimal(200))
        self.assertEqual(self.request(self.base+f'/assets/{aid}', 'DELETE')[0], 400)
        self.assertEqual(self.request(self.base+f'/transactions/{trade["id"]}', 'PUT', {'type': 'buy', 'date': '2025-01-02', 'asset_id': aid, 'quantity': 3, 'price': 100})[0], 200)
        self.assertEqual(self.request(self.base)[1]['assets'][0]['quantity'], '3')
        self.assertEqual(self.request(self.base+f'/transactions/{trade["id"]}', 'DELETE')[0], 200)
        self.assertEqual(self.request(self.base+f'/assets/{aid}', 'DELETE')[0], 200)
        self.assertEqual(self.request(self.base)[1]['assets'], [])

    def test_rejected_sale_has_no_side_effects(self):
        aid = self.request(self.base+'/assets', 'POST', {'ticker': 'AAA', 'name': 'Asset', 'price': 200})[1]['id']
        status, _, _ = self.request(self.base+'/transactions', 'POST', {'type': 'sell', 'date': '2025-01-02', 'asset_id': aid, 'quantity': 2, 'price': 100})
        self.assertEqual(status, 400)
        self.assertEqual(self.request(self.base)[1]['transactions'], [])

    def test_import_preview_commit_export_roundtrip(self):
        text = (server.ROOT/'static'/'example.csv').read_text()
        self.assertEqual(self.request(self.base+'/import', 'POST', {'csv': text, 'dry_run': True})[1]['count'], 3)
        self.assertEqual(self.request(self.base)[1]['transactions'], [])
        self.assertEqual(self.request(self.base+'/import', 'POST', {'csv': text, 'dry_run': False})[0], 200)
        result = self.request(self.base)[1]
        self.assertEqual(len(result['transactions']), 3)
        self.assertEqual(self.request(self.base+'/import', 'POST', {'csv': text, 'dry_run': False})[0], 400)
        status, exported, headers = self.request(self.base+'/export')
        self.assertEqual(status, 200)
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertEqual(len(list(csv.DictReader(io.StringIO(exported.decode('utf-8-sig'))))), 3)
        other = self.request('/api/portfolios', 'POST', {'name': 'Roundtrip', 'currency': 'RUB'})[1]['id']
        try:
            self.assertEqual(self.request(f'/api/portfolios/{other}/import', 'POST', {'csv': exported.decode('utf-8-sig'), 'dry_run': False})[0], 200)
            restored = self.request(f'/api/portfolios/{other}')[1]
            self.assertEqual(Decimal(restored['summary']['profit']), Decimal(result['summary']['profit']))
            self.assertEqual(restored['assets'][0]['quantity'], result['assets'][0]['quantity'])
        finally:
            self.request(f'/api/portfolios/{other}', 'DELETE')

    def test_invalid_import_is_atomic_at_http_boundary(self):
        text = 'date,type,ticker,currency,quantity,price,amount\n2025-01-01,deposit,,RUB,,,1000\n2025-01-02,buy,NEW,RUB,2,10,\n2025-01-03,sell,NEW,RUB,3,20,\n'
        self.assertEqual(self.request(self.base+'/import', 'POST', {'csv': text, 'dry_run': False})[0], 400)
        state = self.request(self.base)[1]
        self.assertEqual(state['assets'], [])
        self.assertEqual(state['transactions'], [])

    def test_backup_is_consistent_and_restorable(self):
        status, payload, headers = self.request('/api/backup')
        self.assertEqual(status, 200)
        self.assertTrue(payload.startswith(b'SQLite format 3'))
        target = Path(self.temp.name)/'downloaded.sqlite3'
        target.write_bytes(payload)
        with closing(sqlite3.connect(target)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertTrue(db.execute('SELECT * FROM portfolios WHERE id=?', (self.pid,)).fetchone())

    def test_local_request_protection_and_input_validation(self):
        self.assertEqual(self.request('/api/meta', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('/api/portfolios', 'POST', {'name': 'Bad'}, {'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('/api/portfolios', 'POST', b'{}', {'Content-Type': 'text/plain'}, raw=True)[0], 400)
        self.assertEqual(self.request('/api/portfolios', 'POST', b'{broken', raw=True)[0], 400)
        self.assertEqual(self.request('/api/portfolios', 'POST', [], raw=False)[0], 400)
        self.assertEqual(self.request(self.base+'/transactions', 'POST', {'type': 'buy', 'date': '2025-01-01', 'asset_id': [], 'quantity': 1, 'price': 100})[0], 400)
        self.assertEqual(self.request('/api/missing')[0], 404)

    def test_explicit_lan_host_preserves_origin_and_host_protection(self):
        headers = {'Host': '192.168.0.100:8900'}
        self.assertEqual(self.request('/api/meta', headers=headers)[0], 403)
        with patch.dict('os.environ', {'INVEST_ALLOWED_HOSTS': '192.168.0.100'}):
            self.assertEqual(self.request('/api/meta', headers=headers)[0], 200)
            self.assertEqual(self.request('/api/meta', headers={**headers, 'Origin': 'http://192.168.0.100:8900'})[0], 200)
            self.assertEqual(self.request('/api/meta', headers={**headers, 'Origin': 'https://evil.example'})[0], 403)
            self.assertEqual(self.request('/api/meta', headers={'Host': 'evil.example'})[0], 403)

    def test_rates_save_and_fixed_ruble(self):
        self.assertEqual(self.request('/api/rates', 'PUT', {'rates': {'USD': 90}})[0], 200)
        self.assertEqual(self.request('/api/rates', 'PUT', {'rates': {'RUB': 2}})[0], 400)
        rates = {r['currency']: r['rub'] for r in self.request('/api/meta')[1]['rates']}
        self.assertEqual(rates['RUB'], '1')
        self.assertEqual(rates['USD'], '90')

    def test_broker_token_is_never_returned_or_backed_up(self):
        token = 'test-readonly-token-' + 'x' * 40
        account = {'id': '123', 'accessLevel': 'ACCOUNT_ACCESS_LEVEL_READ_ONLY', 'name': 'Test'}
        keyfile = Path(self.temp.name)/'secret'/'master.key'
        with patch.dict('os.environ', {'INVEST_MASTER_KEY_FILE': str(keyfile)}), \
             patch.object(tbank.Client, 'accounts', return_value=[account]):
            status, body, _ = self.request('/api/tbank/token', 'PUT', {'token': token})
            self.assertEqual(status, 200)
            self.assertNotIn(token, json.dumps(body))
            status, body, _ = self.request('/api/tbank')
            self.assertTrue(body['configured'])
            self.assertNotIn(token, json.dumps(body))
            with server.connection() as db:
                ciphertext = db.execute('SELECT ciphertext FROM broker_credentials').fetchone()[0]
            status, backup, _ = self.request('/api/backup')
            self.assertEqual(status, 200)
            self.assertNotIn(token.encode(), backup)
            self.assertNotIn(ciphertext, backup)
            file = Path(self.temp.name)/'no-credentials.sqlite3'
            file.write_bytes(backup)
            with closing(sqlite3.connect(file)) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM broker_credentials').fetchone()[0], 0)
                self.assertEqual(db.execute('SELECT count(*) FROM broker_accounts').fetchone()[0], 1)
            self.assertEqual(self.request('/api/tbank/token', 'DELETE')[0], 200)
            self.assertFalse(self.request('/api/tbank')[1]['configured'])
        self.assertEqual(self.request('/.secrets/master.key')[0], 404)

    def test_login_protects_ui_data_and_broker_credentials(self):
        password = 'test-server-login-password-123'
        config = Path(self.temp.name)/'auth.json'
        access_control.write_password(config, 'chat', password, force=True)
        self.httpd.access = access_control.Access(config)
        try:
            for endpoint in ['/api/meta','/api/backup','/api/tbank']:
                self.assertEqual(self.request(endpoint)[0], 401)
            self.assertEqual(self.request('/api/tbank/token', 'PUT', {'token':'a'*40})[0], 401)
            self.assertEqual(self.request('/')[0], 303)
            self.assertEqual(self.request('/login.html')[0], 200)
            self.assertEqual(self.request('/api/health')[0], 200)
            self.assertEqual(self.request('/api/auth/login','POST', {'username':'chat','password':'wrong'})[0], 401)
            status,body,headers=self.request('/api/auth/login','POST', {'username':'chat','password':password})
            self.assertEqual(status,200)
            self.assertNotIn(password,json.dumps(body))
            cookie=headers['Set-Cookie'].split(';')[0]
            self.assertEqual(self.request('/api/meta',headers={'Cookie':cookie})[0],200)
            self.assertEqual(self.request('/api/auth/logout','POST',{},headers={'Cookie':cookie})[0],200)
            self.assertEqual(self.request('/api/meta',headers={'Cookie':cookie})[0],401)
            self.assertEqual(self.request('/api/auth/login','POST',{'username':'chat','password':password},headers={'Origin':'http://evil.example'})[0],403)
        finally:
            del self.httpd.access

    def test_password_change_endpoint_requires_current_password_and_revokes_sessions(self):
        current, new = 'old-http-test-password-123', 'new-pass9'
        config = Path(self.temp.name)/'change-password.json'
        access_control.write_password(config, 'chat', current, force=True)
        self.httpd.access = access_control.Access(config)
        data = {'current_password':current,'new_password':new,'confirm_password':new}
        try:
            self.assertEqual(self.request('/api/auth/password','POST',data)[0],401)
            status, _, headers = self.request('/api/auth/login','POST',{'username':'chat','password':current})
            self.assertEqual(status,200)
            cookie = headers['Set-Cookie'].split(';')[0]
            headers = {'Cookie':cookie}
            self.assertEqual(self.request('/api/auth/password','POST',{**data,'new_password':'short123','confirm_password':'short123'},headers=headers)[0],400)
            self.assertEqual(self.request('/api/auth/password','POST',data,headers={**headers,'Origin':'http://evil.example'})[0],403)
            self.assertEqual(self.request('/api/auth/password','POST',{**data,'current_password':'wrong'},headers=headers)[0],400)
            self.assertEqual(self.request('/api/meta',headers=headers)[0],200)
            status,body,response_headers = self.request('/api/auth/password','POST',data,headers=headers)
            self.assertEqual(status,200)
            self.assertNotIn(new,json.dumps(body))
            self.assertIn('Max-Age=0',response_headers['Set-Cookie'])
            self.assertEqual(self.request('/api/meta',headers=headers)[0],401)
            self.assertEqual(self.request('/api/auth/login','POST',{'username':'chat','password':current})[0],401)
            self.assertEqual(self.request('/api/auth/login','POST',{'username':'chat','password':new})[0],200)
        finally:
            del self.httpd.access


if __name__ == '__main__':
    unittest.main(verbosity=2)
