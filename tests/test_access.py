import json
import os
import stat
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import access_control as auth

PASSWORD = 'test-password-for-auth-only-123'
NEW_PASSWORD = 'new-test-password-for-auth-456'


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'secrets' / 'access.json'
        auth.write_password(self.path, 'chat', PASSWORD)
        self.access = auth.Access(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def test_password_file_contains_salted_hash_only(self):
        content = self.path.read_text()
        self.assertNotIn(PASSWORD, content)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        other = self.path.with_name('other.json')
        auth.write_password(other, 'chat', PASSWORD)
        self.assertNotEqual(json.loads(content)['hash'], json.loads(other.read_text())['hash'])

    def test_login_cookie_not_visible_to_scripts_and_logout_revokes_it(self):
        cookie = self.access.login({'username':'chat', 'password':PASSWORD}, '127.0.0.1')
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)
        self.assertIn('Max-Age=43200', cookie)
        self.assertTrue(self.access.authenticated(cookie))
        expired = self.access.logout(cookie)
        self.assertIn('Max-Age=0', expired)
        self.assertFalse(self.access.authenticated(cookie))

    def test_wrong_username_and_password_return_same_error(self):
        errors = []
        for username,password in [('bad',PASSWORD), ('chat','wrong')]:
            with self.assertRaises(auth.AccessError) as caught:
                self.access.login({'username':username, 'password':password}, '127.0.0.1')
            errors.append(str(caught.exception))
        self.assertEqual(errors[0], errors[1])

    def test_guessed_cookie_is_rejected(self):
        self.assertFalse(self.access.authenticated('invest_session=guessed'))
        self.assertFalse(self.access.authenticated(''))

    def test_sessions_expire_and_do_not_survive_restart(self):
        cookie = self.access.login({'username':'chat','password':PASSWORD}, '127.0.0.1')
        self.assertFalse(auth.Access(self.path).authenticated(cookie))
        with patch.object(auth.time, 'monotonic', return_value=auth.time.monotonic()+auth.SESSION_TTL+1):
            self.assertFalse(self.access.authenticated(cookie))

    def test_rate_limit_stops_repeated_guesses(self):
        for _ in range(5):
            with self.assertRaises(auth.AccessError):
                self.access.login({'username':'chat','password':'wrong'}, '127.0.0.1')
        with self.assertRaisesRegex(auth.AccessError, 'Слишком много'):
            self.access.login({'username':'chat','password':PASSWORD}, '127.0.0.1')

    def test_https_cookie_uses_secure_flag(self):
        secure = auth.Access(self.path, secure=True)
        cookie = secure.login({'username':'chat','password':PASSWORD}, '127.0.0.1')
        self.assertIn('; Secure', cookie)

    def test_missing_and_insecure_config_fail_closed(self):
        with self.assertRaises(auth.AccessError):
            auth.Access(self.path.with_name('absent'))
        if os.name == 'posix':
            self.path.chmod(0o644)
            with self.assertRaises(auth.AccessError):
                auth.Access(self.path)

    def test_local_mode_stays_available_without_auth_config(self):
        self.assertTrue(auth.Access().authenticated(None))

    def test_existing_password_is_not_replaced_implicitly(self):
        with self.assertRaises(auth.AccessError):
            auth.write_password(self.path, 'chat', 'a-different-test-password-123')

    def change_data(self, **updates):
        return {'current_password':PASSWORD, 'new_password':NEW_PASSWORD, 'confirm_password':NEW_PASSWORD, **updates}

    def test_password_change_persists_and_revokes_all_sessions(self):
        first = self.access.login({'username':'chat','password':PASSWORD}, 'first')
        second = self.access.login({'username':'chat','password':PASSWORD}, 'second')
        cookie = self.access.change_password(self.change_data(), first, 'first')
        self.assertIn('Max-Age=0', cookie)
        self.assertFalse(self.access.authenticated(first))
        self.assertFalse(self.access.authenticated(second))
        self.assertNotIn(NEW_PASSWORD, self.path.read_text())
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        for instance in [self.access, auth.Access(self.path)]:
            with self.assertRaises(auth.AccessError):
                instance.login({'username':'chat','password':PASSWORD}, 'old')
            new_cookie = instance.login({'username':'chat','password':NEW_PASSWORD}, 'new')
            self.assertTrue(instance.authenticated(new_cookie))

    def test_wrong_current_password_does_not_change_file_or_session(self):
        cookie = self.access.login({'username':'chat','password':PASSWORD}, 'client')
        original = self.path.read_bytes()
        with self.assertRaisesRegex(auth.AccessError, 'Текущий пароль'):
            self.access.change_password(self.change_data(current_password='wrong'), cookie, 'client')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(self.access.authenticated(cookie))

    def test_invalid_new_password_and_confirmation_leave_credentials_unchanged(self):
        cookie = self.access.login({'username':'chat','password':PASSWORD}, 'client')
        original = self.path.read_bytes()
        updates = [{'new_password':'short','confirm_password':'short'},
                   {'confirm_password':'different'}, {'new_password':None},
                   {'new_password':PASSWORD,'confirm_password':PASSWORD},
                   {'new_password':'x'*1025,'confirm_password':'x'*1025}]
        for values in updates:
            with self.subTest(values=list(values)), self.assertRaises(auth.AccessError):
                self.access.change_password(self.change_data(**values), cookie, 'client')
            self.assertEqual(self.path.read_bytes(), original)
            self.assertTrue(self.access.authenticated(cookie))

    def test_password_change_requires_valid_session(self):
        with self.assertRaises(auth.AccessError):
            self.access.change_password(self.change_data(), 'invest_session=guessed', 'client')

    def test_password_change_failure_preserves_credentials_and_session(self):
        cookie = self.access.login({'username':'chat','password':PASSWORD}, 'client')
        original = self.path.read_bytes()
        with patch.object(auth, 'write_password', side_effect=OSError('read-only filesystem')):
            with self.assertRaisesRegex(auth.AccessError, 'сохранить пароль'):
                self.access.change_password(self.change_data(), cookie, 'client')
        self.assertEqual(self.path.read_bytes(), original)
        self.assertTrue(self.access.authenticated(cookie))

    def test_current_password_guesses_are_rate_limited(self):
        cookie = self.access.login({'username':'chat','password':PASSWORD}, 'login')
        for _ in range(5):
            with self.assertRaises(auth.AccessError):
                self.access.change_password(self.change_data(current_password='wrong'), cookie, 'guessing')
        with self.assertRaisesRegex(auth.AccessError, 'Слишком много'):
            self.access.change_password(self.change_data(), cookie, 'guessing')

    def test_inflight_old_password_login_cannot_create_session_after_change(self):
        cookie = self.access.login({'username':'chat','password':PASSWORD}, 'current')
        ready, resume = threading.Event(), threading.Event()
        original_digest = auth.digest
        result = []
        def delayed_digest(*args):
            value = original_digest(*args)
            if threading.current_thread().name == 'inflight-login':
                ready.set()
                if not resume.wait(5):
                    raise RuntimeError('Login test timed out')
            return value
        def login():
            try:
                result.append(self.access.login({'username':'chat','password':PASSWORD}, 'inflight'))
            except auth.AccessError as error:
                result.append(error)
        with patch.object(auth, 'digest', side_effect=delayed_digest):
            thread = threading.Thread(target=login, name='inflight-login', daemon=True)
            thread.start()
            try:
                self.assertTrue(ready.wait(5))
                self.access.change_password(self.change_data(), cookie, 'current')
            finally:
                resume.set()
                thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], auth.AccessError)


if __name__ == '__main__':
    unittest.main()
