import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal as D
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = server.DB_PATH
        server.DB_PATH = Path(self.temp.name) / 'test.sqlite3'
        server.init_db()
        with server.connection() as db:
            self.pid = server.create_portfolio(db, {'name': 'Test', 'currency': 'RUB'})
            self.aid = server.save_asset(db, self.pid, {'ticker': 'TEST', 'name': 'Test asset',
                'category': 'Stocks', 'currency': 'RUB', 'price': 150, 'annual_dividend': 10})

    def tearDown(self):
        server.DB_PATH = self.previous_path
        self.temp.cleanup()

    def transaction(self, kind, **kwargs):
        data = {'type': kind, 'date': '2025-01-10', 'currency': 'RUB', **kwargs}
        if kind in ('buy', 'sell', 'dividend', 'coupon'):
            data.setdefault('asset_id', self.aid)
        with server.connection() as db:
            tid = server.save_transaction(db, self.pid, data)
            server.snapshot(db, self.pid)
            return tid

    def state(self):
        with server.connection() as db:
            return server.calculate(db, self.pid)

    def test_partial_sale_weighted_cost_fees_tax_and_cash(self):
        self.transaction('deposit', amount=2000)
        self.transaction('buy', quantity=10, price=100, fee=10)
        self.transaction('sell', date='2025-02-01', quantity=4, price=150, fee=6)
        self.transaction('dividend', date='2025-03-01', amount=60, tax=9, fee=1)
        data = self.state()
        a, s = data['assets'][0], data['summary']
        self.assertEqual(a['quantity'], D(6))
        self.assertEqual(a['average_price'], D(101))
        self.assertEqual(a['cost_base'], D(606))
        self.assertEqual(s['realized'], D(190))
        self.assertEqual(s['unrealized'], D(294))
        self.assertEqual(s['cash'], D(1634))
        self.assertEqual(s['equity'], D(2534))
        self.assertEqual(s['profit'], D(534))
        self.assertEqual(s['income'], D(50))
        self.assertEqual(s['annual_income'], D(60))
        self.assertEqual(s['fees'], D(17))
        self.assertEqual(s['taxes'], D(9))

    def test_weighted_average_after_multiple_buys_and_full_close(self):
        self.transaction('deposit', amount=1000)
        self.transaction('buy', quantity=3, price=10)
        self.transaction('buy', quantity=7, price=20)
        self.transaction('sell', quantity=10, price=25)
        data = self.state()
        self.assertEqual(data['assets'][0]['quantity'], D(0))
        self.assertEqual(data['assets'][0]['cost_base'], D(0))
        self.assertEqual(data['summary']['realized'], D(80))
        self.assertEqual(data['summary']['profit'], D(80))

    def test_no_short_sales_and_failed_mutation_rolls_back(self):
        self.transaction('buy', quantity=2, price=100)
        with self.assertRaises(server.ValidationError):
            self.transaction('sell', date='2025-02-01', quantity=3, price=150)
        self.assertEqual(len(self.state()['transactions']), 1)
        self.assertEqual(self.state()['assets'][0]['quantity'], D(2))

    def test_historical_order_not_insertion_order(self):
        self.transaction('buy', date='2025-02-01', quantity=2, price=100)
        with self.assertRaises(server.ValidationError):
            self.transaction('sell', date='2025-01-01', quantity=1, price=150)
        self.assertEqual(len(self.state()['transactions']), 1)

    def test_edit_purchase_cannot_break_existing_sale(self):
        tid = self.transaction('buy', quantity=10, price=100)
        self.transaction('sell', date='2025-02-01', quantity=5, price=150)
        with self.assertRaises(server.ValidationError):
            with server.connection() as db:
                server.save_transaction(db, self.pid, {'type': 'buy', 'date': '2025-01-10',
                    'asset_id': self.aid, 'quantity': 3, 'price': 100}, tid)
                server.snapshot(db, self.pid)
        self.assertEqual(self.state()['assets'][0]['quantity'], D(5))

    def test_decimal_exactness_fractional_assets(self):
        self.transaction('deposit', amount='1')
        self.transaction('buy', quantity='0.1', price='0.2', fee='0.001')
        data = self.state()
        self.assertEqual(data['summary']['cash'], D('0.979'))
        self.assertEqual(data['assets'][0]['average_price'], D('0.21'))

    def test_withdrawals_and_standalone_fees(self):
        self.transaction('deposit', amount=1000, fee=10)
        self.transaction('withdrawal', amount=200, fee=5)
        self.transaction('fee', amount=20)
        data = self.state()['summary']
        self.assertEqual(data['net_contributions'], D(800))
        self.assertEqual(data['equity'], D(765))
        self.assertEqual(data['profit'], D(-35))
        self.assertEqual(data['fees'], D(35))

    def test_currency_rates_current_valuation_historical_cashflows(self):
        with server.connection() as db:
            db.execute('INSERT INTO rates VALUES (?,?,?)', ('USD', '100', server.now()))
            aid = server.save_asset(db, self.pid, {'ticker': 'US', 'name': 'Foreign',
                'currency': 'USD', 'price': 12})
        self.transaction('deposit', amount=200, currency='USD', fx_rate=90)
        self.transaction('buy', asset_id=aid, quantity=10, price=10, fx_rate=90)
        data = self.state()['summary']
        self.assertEqual(data['net_contributions'], D(18000))
        self.assertEqual(data['market_value'], D(12000))
        self.assertEqual(data['cash'], D(10000))
        self.assertEqual(data['equity'], D(22000))
        self.assertEqual(data['profit'], D(4000))
        self.assertEqual(data['unrealized'], D(3000))

    def test_cross_currency_default_rate_is_rounded_and_accepted(self):
        with server.connection() as db:
            db.execute('INSERT INTO rates VALUES (?,?,?)', ('USD', '91', server.now()))
            db.execute('INSERT INTO rates VALUES (?,?,?)', ('EUR', '103', server.now()))
            pid = server.create_portfolio(db, {'name': 'EUR', 'currency': 'EUR'})
            tid = server.save_transaction(db, pid, {'type': 'deposit', 'date': '2025-01-01',
                'currency': 'USD', 'amount': 10})
            stored = db.execute('SELECT fx_rate FROM transactions WHERE id=?', (tid,)).fetchone()[0]
            self.assertEqual(stored, '0.883495145631')

    def test_missing_rate_does_not_silently_assume_one(self):
        with self.assertRaises(server.ValidationError):
            with server.connection() as db:
                server.save_asset(db, self.pid, {'ticker': 'US', 'name': 'Foreign', 'currency': 'USD'})

    def test_income_can_be_recorded_after_position_closed(self):
        self.transaction('buy', quantity=1, price=100)
        self.transaction('sell', date='2025-02-01', quantity=1, price=110)
        self.transaction('dividend', date='2025-03-01', amount=10, tax=1)
        self.assertEqual(self.state()['summary']['income'], D(9))

    def test_over_taxed_dividend_rejected(self):
        with self.assertRaises(server.ValidationError):
            self.transaction('dividend', amount=10, tax=9, fee=2)

    def test_currency_cannot_change_after_trades(self):
        self.transaction('buy', quantity=1, price=100)
        with server.connection() as db:
            db.execute('INSERT INTO rates VALUES (?,?,?)', ('USD', '100', server.now()))
            with self.assertRaises(server.ValidationError):
                server.save_asset(db, self.pid, {'ticker': 'TEST', 'name': 'Test', 'currency': 'USD'}, self.aid)

    def test_portfolio_data_is_isolated(self):
        with server.connection() as db:
            other = server.create_portfolio(db, {'name': 'Other', 'currency': 'RUB'})
            with self.assertRaises(server.ValidationError):
                server.save_transaction(db, other, {'type': 'buy', 'date': '2025-01-01',
                    'asset_id': self.aid, 'quantity': 1, 'price': 10})
            self.assertEqual(server.calculate(db, other)['assets'], [])

    def test_bad_import_rolls_back_entire_file(self):
        text = 'date,type,ticker,currency,quantity,price,amount\n2025-01-01,deposit,,RUB,,,1000\n2025-01-02,buy,NEW,RUB,2,10,\n2025-01-03,sell,NEW,RUB,3,20,\n'
        with self.assertRaises(server.ValidationError):
            with server.connection() as db:
                server.import_csv(db, self.pid, {'csv': text})
        data = self.state()
        self.assertEqual(data['transactions'], [])
        self.assertEqual(len(data['assets']), 1)

    def test_import_preview_rollback_then_commit_and_duplicate_protection(self):
        text = (server.ROOT / 'static' / 'example.csv').read_text()
        with server.connection() as db:
            db.execute('SAVEPOINT preview')
            result = server.import_csv(db, self.pid, {'csv': text})
            self.assertEqual(result['count'], 3)
            db.execute('ROLLBACK TO preview')
            db.execute('RELEASE preview')
        self.assertEqual(self.state()['transactions'], [])
        with server.connection() as db:
            result = server.import_csv(db, self.pid, {'csv': text})
            self.assertEqual(result['created_assets'], 1)
        self.assertEqual(len(self.state()['transactions']), 3)
        with self.assertRaises(server.ValidationError):
            with server.connection() as db:
                server.import_csv(db, self.pid, {'csv': text})

    def test_semicolon_import_bom_decimal_comma(self):
        text = '\ufeffdate;type;currency;amount;fee\n2025-01-01;deposit;RUB;100,50;0,10\n'
        with server.connection() as db:
            server.import_csv(db, self.pid, {'csv': text})
        self.assertEqual(self.state()['summary']['equity'], D('100.40'))

    def test_import_validates_currency_of_existing_asset(self):
        text = 'date,type,ticker,currency,quantity,price\n2025-01-01,buy,TEST,USD,1,10\n'
        with self.assertRaisesRegex(server.ValidationError, 'Строка 2'):
            with server.connection() as db:
                server.import_csv(db, self.pid, {'csv': text})

    def test_base_currency_rate_must_be_one(self):
        with self.assertRaises(server.ValidationError):
            self.transaction('deposit', amount=100, fx_rate=2)

    def test_financial_numbers_reject_nonfinite_and_extreme_values(self):
        for value in ['NaN', 'Infinity', '-1', '1e30', '0.0000000000001']:
            with self.subTest(value=value), self.assertRaises(server.ValidationError):
                server.number(value)

    def test_invalid_and_future_dates_rejected(self):
        for value in ['2025-02-30', '9999-01-01', '01.01.2025', '1800-01-01']:
            with self.subTest(value=value), self.assertRaises(server.ValidationError):
                server.valid_date(value)

    def test_demo_is_optional_separate_and_idempotent(self):
        with server.connection() as db:
            pid = server.demo(db)
            self.assertNotEqual(pid, self.pid)
            self.assertEqual(server.demo(db), pid)
            data = server.calculate(db, pid)
            self.assertTrue(data['portfolio']['is_demo'])
            self.assertEqual(len(data['assets']), 5)
            self.assertGreater(data['summary']['equity'], D(1000000))
            self.assertEqual(server.calculate(db, self.pid)['transactions'], [])

    def test_daily_snapshots_are_upserted(self):
        self.transaction('deposit', amount=100)
        self.transaction('deposit', amount=200)
        data = self.state()
        self.assertEqual(len(data['history']), 1)
        self.assertEqual(D(data['history'][0]['equity']), D(300))

    def test_backup_preserves_database_and_relationships(self):
        self.transaction('buy', quantity=2, price=100)
        backup = Path(self.temp.name) / 'backup.sqlite3'
        with server.connection() as db, closing(sqlite3.connect(backup)) as target:
            db.backup(target)
        with closing(sqlite3.connect(backup)) as restored:
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(restored.execute('SELECT count(*) FROM transactions').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
