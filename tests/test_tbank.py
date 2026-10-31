import copy
import io
import json
import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import tbank

TOKEN = 'test-readonly-token-' + 'a' * 40
ACCOUNT = {'id': '12345', 'name': 'Broker test', 'accessLevel': 'ACCOUNT_ACCESS_LEVEL_READ_ONLY',
           'status': 'ACCOUNT_STATUS_OPEN', 'openedDate': '2024-01-01T00:00:00Z'}


def operation(oid='op1', amount='100'):
    return {'id': oid, 'date': '2025-02-01T10:00:00Z', 'type': 'OPERATION_TYPE_DIVIDEND',
            'payment': {'units': amount, 'nano': 0, 'currency': 'rub'}, 'ticker': 'SBER'}


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous_path = server.DB_PATH
        server.DB_PATH = Path(self.temp.name) / 'data' / 'test.sqlite3'
        self.key = Path(self.temp.name) / 'keys' / 'master.key'
        self.env = patch.dict(os.environ, {'INVEST_MASTER_KEY_FILE': str(self.key)})
        self.env.start()
        server.init_db()

    def tearDown(self):
        self.env.stop()
        server.DB_PATH = self.previous_path
        self.temp.cleanup()

    def save(self, token=TOKEN):
        with patch.object(tbank.Client, 'accounts', return_value=[copy.deepcopy(ACCOUNT)]):
            with server.connection() as db:
                return tbank.save_token(db, token)

    def sync(self, rows=None, start='2025-01-01'):
        def call(_client, method, _data):
            if method == 'portfolio':
                return {'positions': [], 'totalAmountPortfolio': {'units': '500', 'currency': 'rub'}}
            if method == 'positions':
                return {'money': [{'units': '500', 'currency': 'rub'}]}
            raise AssertionError(method)
        with patch.object(tbank.Client, 'accounts', return_value=[copy.deepcopy(ACCOUNT)]), \
             patch.object(tbank.Client, 'call', autospec=True, side_effect=call), \
             patch.object(tbank.Client, 'operations', return_value=rows if rows is not None else [operation()]):
            with server.connection() as db:
                return tbank.sync(db, ACCOUNT['id'], start)

    def test_token_encrypted_no_plaintext_in_db_or_api(self):
        self.save()
        with server.connection() as db:
            row = db.execute('SELECT * FROM broker_credentials').fetchone()
            self.assertNotIn(TOKEN.encode(), row['ciphertext'])
            self.assertEqual(tbank.credential(db)[0], TOKEN)
            self.assertNotIn(TOKEN, json.dumps(tbank.status(db)))
            self.assertNotIn('ciphertext', json.dumps(tbank.status(db)))
        self.assertNotIn(TOKEN.encode(), server.DB_PATH.read_bytes())
        self.assertEqual(stat.S_IMODE(self.key.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.key.parent.stat().st_mode), 0o700)

    def test_key_is_outside_financial_database_directory(self):
        self.save()
        self.assertFalse(self.key.is_relative_to(server.DB_PATH.parent))

    def test_missing_key_never_silently_replaced(self):
        self.save()
        self.key.unlink()
        with self.assertRaises(tbank.BrokerError):
            with server.connection() as db:
                tbank.credential(db)
        self.assertFalse(self.key.exists())
        with self.assertRaises(tbank.BrokerError):
            self.save('replacement-token-' + 'b' * 40)
        self.assertFalse(self.key.exists())

    def test_wrong_or_modified_key_cannot_decrypt(self):
        self.save()
        from cryptography.fernet import Fernet
        self.key.write_bytes(Fernet.generate_key())
        with self.assertRaises(tbank.BrokerError):
            with server.connection() as db:
                tbank.credential(db)

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions')
    def test_insecure_key_permissions_are_rejected(self):
        self.save()
        self.key.chmod(0o644)
        with self.assertRaisesRegex(tbank.BrokerError, '0600'):
            with server.connection() as db:
                tbank.credential(db)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlinks')
    def test_key_symlink_is_rejected(self):
        self.save()
        moved = self.key.with_suffix('.real')
        self.key.rename(moved)
        self.key.symlink_to(moved)
        with self.assertRaises(tbank.BrokerError):
            with server.connection() as db:
                tbank.credential(db)

    def test_full_access_token_rejected(self):
        account = {**ACCOUNT, 'accessLevel': 'ACCOUNT_ACCESS_LEVEL_FULL_ACCESS'}
        with patch.object(tbank.Client, 'call', return_value={'accounts': [account]}):
            with self.assertRaisesRegex(tbank.BrokerError, 'Только чтение'):
                with server.connection() as db:
                    tbank.save_token(db, TOKEN)
        self.assertFalse(self.key.exists())
        with server.connection() as db:
            self.assertFalse(tbank.status(db)['configured'])

    def test_scoped_readonly_token_filters_inaccessible_accounts(self):
        other = {**ACCOUNT, 'id': '999', 'accessLevel': 'ACCOUNT_ACCESS_LEVEL_NO_ACCESS'}
        with patch.object(tbank.Client, 'call', return_value={'accounts': [ACCOUNT, other]}):
            self.assertEqual(tbank.Client(TOKEN).accounts(), [ACCOUNT])

    def test_invalid_token_rejected_before_network(self):
        for value in ['tiny', TOKEN+'\r\nAuthorization: bad', {'token': TOKEN}, 'Bearer '+TOKEN]:
            with self.subTest(value=type(value).__name__), patch.object(tbank.Client, 'accounts') as remote:
                with self.assertRaises(tbank.BrokerError):
                    with server.connection() as db:
                        tbank.save_token(db, value)
                remote.assert_not_called()

    def test_replacing_token_reuses_master_key_and_updates_generation(self):
        self.save()
        old_key = self.key.read_bytes()
        with server.connection() as db:
            old_generation = tbank.credential(db)[1]
        replacement = 'replacement-token-' + 'b' * 40
        self.save(replacement)
        with server.connection() as db:
            token, generation = tbank.credential(db)
            self.assertEqual(token, replacement)
            self.assertNotEqual(generation, old_generation)
        self.assertEqual(self.key.read_bytes(), old_key)

    def test_invalid_replacement_preserves_working_credential(self):
        self.save()
        with patch.object(tbank.Client, 'accounts', side_effect=tbank.BrokerError('Invalid')):
            with self.assertRaises(tbank.BrokerError):
                with server.connection() as db:
                    tbank.save_token(db, 'new-token-'+'z'*40)
        with server.connection() as db:
            self.assertEqual(tbank.credential(db)[0], TOKEN)

    def test_pending_token_replacement_cannot_undo_disconnect(self):
        self.save()
        def disconnect(_client):
            with server.connection() as db:
                db.execute("DELETE FROM broker_credentials WHERE provider='tbank'")
            return [ACCOUNT]
        with patch.object(tbank.Client, 'accounts', autospec=True, side_effect=disconnect):
            with self.assertRaisesRegex(tbank.BrokerError, 'изменилось'):
                with server.connection() as db:
                    tbank.save_token(db, 'replacement-token-' + 'c' * 40)
        with server.connection() as db:
            self.assertFalse(tbank.status(db)['configured'])

    def test_disconnect_removes_credential_and_preserves_cached_account(self):
        self.save()
        self.sync()
        with server.connection() as db:
            tbank.route(db, '/api/tbank/token', 'DELETE', {})
        with server.connection() as db:
            self.assertFalse(tbank.status(db)['configured'])
            self.assertEqual(tbank.account_data(db, ACCOUNT['id'])['operations_count'], 1)
            with self.assertRaises(tbank.BrokerError):
                tbank.credential(db)

    def test_transport_only_allows_read_methods(self):
        client = tbank.Client(TOKEN)
        client._opener = Mock()
        with self.assertRaises(tbank.BrokerError):
            client.call('OrdersService/PostOrder', {})
        client._opener.open.assert_not_called()

    def test_transport_auth_in_header_only_and_no_redirects(self):
        client = tbank.Client(TOKEN)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"accounts":[]}'
        client._opener = Mock()
        client._opener.open.return_value = response
        client.call('accounts', {})
        request = client._opener.open.call_args.args[0]
        self.assertTrue(request.full_url.startswith('https://invest-public-api.tbank.ru/rest/'))
        self.assertNotIn(TOKEN, request.full_url)
        self.assertNotIn(TOKEN.encode(), request.data)
        self.assertEqual(request.get_header('Authorization'), 'Bearer '+TOKEN)
        self.assertIsNone(tbank.NoRedirect().redirect_request(None, None, 302, None, {}, 'https://evil.example'))

    def test_upstream_errors_are_sanitized(self):
        for error in [HTTPError('https://example', 403, TOKEN, {}, io.BytesIO(TOKEN.encode())),
                      URLError(TOKEN), TimeoutError(TOKEN)]:
            with self.subTest(error=type(error).__name__):
                client = tbank.Client(TOKEN)
                client._opener = Mock()
                client._opener.open.side_effect = error
                with self.assertRaises(tbank.BrokerError) as caught:
                    client.call('accounts', {})
                self.assertNotIn(TOKEN, str(caught.exception))

    @patch('tbank.time.sleep')
    def test_cursor_pagination_deduplicates_and_uses_next_cursor(self, _sleep):
        client = tbank.Client(TOKEN)
        client.call = Mock(side_effect=[{'items':[operation()], 'hasNext':True, 'nextCursor':'next'},
                                      {'items':[operation(),operation('op2')], 'hasNext':False}])
        result = client.operations(ACCOUNT['id'], '2025-01-01T00:00:00Z', tbank.utcnow())
        self.assertEqual(len(result), 2)
        self.assertEqual(client.call.call_args_list[1].args[1]['cursor'], 'next')
        self.assertFalse(client.call.call_args_list[0].args[1]['withoutCommissions'])

    @patch('tbank.time.sleep')
    def test_repeating_cursor_is_rejected(self, _sleep):
        client = tbank.Client(TOKEN)
        client.call = Mock(return_value={'items':[], 'hasNext':True, 'nextCursor':'same'})
        with self.assertRaisesRegex(tbank.BrokerError, 'курсор'):
            client.operations(ACCOUNT['id'], '2025-01-01T00:00:00Z', tbank.utcnow())

    @patch('tbank.time.sleep')
    def test_too_many_pages_fail_instead_of_importing_partial_history(self, _sleep):
        client = tbank.Client(TOKEN)
        client.call = Mock(return_value={'items':[], 'hasNext':True, 'nextCursor':'one'})
        with patch.object(tbank, 'MAX_PAGES', 1), self.assertRaises(tbank.BrokerError):
            client.operations(ACCOUNT['id'], '2025-01-01T00:00:00Z', tbank.utcnow())

    def test_sync_is_idempotent_and_updates_changed_broker_ids(self):
        self.save()
        self.sync()
        self.sync()
        with server.connection() as db:
            self.assertEqual(tbank.account_data(db, ACCOUNT['id'])['operations_count'], 1)
        self.sync([operation('changed-id', '150')])
        with server.connection() as db:
            data = tbank.account_data(db, ACCOUNT['id'])
            self.assertEqual(data['operations_count'], 1)
            self.assertEqual(data['operations'][0]['id'], 'changed-id')
            self.assertEqual(data['operations'][0]['payment']['units'], '150')
            self.assertEqual(db.execute('SELECT count(*) FROM transactions').fetchone()[0], 0)

    def test_failed_sync_keeps_entire_previous_snapshot(self):
        self.save()
        self.sync()
        with server.connection() as db:
            before = tbank.account_data(db, ACCOUNT['id'])
        with patch.object(tbank.Client, 'accounts', return_value=[ACCOUNT]), \
             patch.object(tbank.Client, 'call', return_value={'positions': [], 'totalAmountPortfolio': {'units': '999'}}), \
             patch.object(tbank.Client, 'operations', side_effect=tbank.BrokerError('Unavailable')):
            with self.assertRaises(tbank.BrokerError):
                with server.connection() as db:
                    tbank.sync(db, ACCOUNT['id'], '2025-01-01')
        with server.connection() as db:
            self.assertEqual(tbank.account_data(db, ACCOUNT['id']), before)

    def test_sync_rechecks_token_generation_before_commit(self):
        self.save()
        def changed(*_):
            with server.connection() as db:
                db.execute("UPDATE broker_credentials SET generation='changed'")
            return []
        with patch.object(tbank.Client, 'accounts', return_value=[ACCOUNT]), \
             patch.object(tbank.Client, 'call', return_value={'positions': [], 'totalAmountPortfolio': {'units': '999'}}), \
             patch.object(tbank.Client, 'operations', side_effect=changed):
            with self.assertRaisesRegex(tbank.BrokerError, 'изменилось'):
                with server.connection() as db:
                    tbank.sync(db, ACCOUNT['id'], '2025-01-01')
        with server.connection() as db:
            self.assertIsNone(tbank.account_data(db, ACCOUNT['id'])['snapshot'])

    def test_history_outside_requested_interval_is_preserved(self):
        self.save()
        self.sync()
        self.sync([], '2025-03-01')
        with server.connection() as db:
            self.assertEqual(tbank.account_data(db, ACCOUNT['id'])['operations_count'], 1)

    def test_operations_are_paginated_locally(self):
        self.save()
        self.sync([operation(str(i)) for i in range(125)])
        with server.connection() as db:
            data = tbank.account_data(db, ACCOUNT['id'])
            self.assertEqual(data['operations_count'], 125)
            self.assertEqual(len(data['operations']), 100)
            self.assertEqual(len(tbank.operation_page(db, ACCOUNT['id'], 100)['operations']), 25)

    def test_sync_lock_is_released_after_failure(self):
        with server.connection() as db:
            with self.assertRaises(tbank.BrokerError):
                tbank.sync(db, 'missing')
        self.assertTrue(tbank.SYNC_LOCK.acquire(blocking=False))
        tbank.SYNC_LOCK.release()

    def test_decimal_quotation_positive_and_negative_nano(self):
        self.assertEqual(tbank.money({'units': '-2', 'nano': -500000000}), '-2.5')
        self.assertEqual(tbank.money({'units': '0', 'nano': 1}), '1E-9')


if __name__ == '__main__':
    unittest.main()
