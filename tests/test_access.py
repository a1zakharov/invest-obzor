import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import access_control as auth

PASSWORD = 'test-password-for-auth-only-123'


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


if __name__ == '__main__':
    unittest.main()
