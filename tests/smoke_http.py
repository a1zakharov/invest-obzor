"""Real HTTP checks; run separately because binding a port may require permission."""
import csv
import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


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
        for path in ['/', '/app.js', '/styles.css', '/favicon.svg', '/example.csv']:
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

    def test_rates_save_and_fixed_ruble(self):
        self.assertEqual(self.request('/api/rates', 'PUT', {'rates': {'USD': 90}})[0], 200)
        self.assertEqual(self.request('/api/rates', 'PUT', {'rates': {'RUB': 2}})[0], 400)
        rates = {r['currency']: r['rub'] for r in self.request('/api/meta')[1]['rates']}
        self.assertEqual(rates['RUB'], '1')
        self.assertEqual(rates['USD'], '90')


if __name__ == '__main__':
    unittest.main(verbosity=2)
