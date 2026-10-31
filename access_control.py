"""Optional single-user password authentication for a private LAN deployment."""
from __future__ import annotations

import argparse
import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import stat
import tempfile
import threading
import time
from collections import deque
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

COOKIE = "invest_session"
ITERATIONS = 600_000
SESSION_TTL = 12 * 3600


class AccessError(Exception):
    pass


def digest(password, salt, iterations=ITERATIONS):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)


def write_password(path, username, password, force=False):
    path = Path(path)
    if not isinstance(username, str) or not 1 <= len(username) <= 80:
        raise AccessError("Логин должен содержать от 1 до 80 символов")
    if not isinstance(password, str) or not 16 <= len(password) <= 1024:
        raise AccessError("Пароль должен содержать от 16 до 1024 символов")
    if path.exists() and not force:
        raise AccessError("Файл уже существует. Для смены пароля используйте --force и перезапустите сервис.")
    salt = secrets.token_bytes(32)
    data = {"version": 1, "username": username, "iterations": ITERATIONS,
            "salt": base64.b64encode(salt).decode(), "hash": base64.b64encode(digest(password, salt)).decode()}
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".access-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Access:
    def __init__(self, filename=None, secure=False):
        self.enabled = bool(filename)
        self.secure = secure
        self.sessions = {}
        self.attempts = {}
        self.lock = threading.Lock()
        if not self.enabled:
            return
        try:
            fd = os.open(filename, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
            with os.fdopen(fd, "r") as handle:
                info = os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or (os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077):
                    raise ValueError()
                config = json.loads(handle.read(8192))
            self.username = config["username"]
            self.salt = base64.b64decode(config["salt"], validate=True)
            self.expected = base64.b64decode(config["hash"], validate=True)
            self.iterations = config["iterations"]
            if (config["version"] != 1 or not isinstance(self.username, str) or not 1 <= len(self.username) <= 80
                    or not isinstance(self.iterations, int) or not 600000 <= self.iterations <= 2000000
                    or len(self.salt) != 32 or len(self.expected) != 32):
                raise ValueError()
        except (OSError, ValueError, TypeError, KeyError):
            raise AccessError("Файл входа недоступен, повреждён или имеет права шире 0600. Создайте его через access_control.py.") from None

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("INVEST_AUTH_FILE"), os.environ.get("INVEST_COOKIE_SECURE") == "1")

    def session_key(self, cookie):
        try:
            parsed = SimpleCookie()
            parsed.load(cookie or "")
            value = parsed.get(COOKIE)
            if value is None or len(value.value) > 128:
                return None
            return hashlib.sha256(value.value.encode()).digest()
        except CookieError:
            return None

    def authenticated(self, cookie):
        if not self.enabled:
            return True
        key = self.session_key(cookie)
        with self.lock:
            return self.sessions.get(key, 0) > time.monotonic()

    def login(self, data, client_ip):
        if not self.enabled:
            raise AccessError("Вход по паролю не настроен")
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 80 or len(password) > 1024:
            raise AccessError("Неверный логин или пароль")
        moment = time.monotonic()
        with self.lock:
            self.attempts = {ip: history for ip, history in self.attempts.items() if history and history[-1] > moment - 60}
            attempts = self.attempts.setdefault(client_ip, deque())
            while attempts and attempts[0] <= moment - 60:
                attempts.popleft()
            if len(attempts) >= 5 or sum(len(history) for history in self.attempts.values()) >= 30:
                raise AccessError("Слишком много попыток. Повторите вход через минуту.")
            attempts.append(moment)
        # Compute the password hash for unknown usernames as well.
        valid_password = hmac.compare_digest(digest(password, self.salt, self.iterations), self.expected)
        valid_username = hmac.compare_digest(username.encode(), self.username.encode())
        if not (valid_username and valid_password):
            raise AccessError("Неверный логин или пароль")
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > moment}
            if len(self.sessions) >= 100:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[hashlib.sha256(token.encode()).digest()] = moment + SESSION_TTL
        return self.cookie(token, SESSION_TTL)

    def cookie(self, value, max_age):
        return f"{COOKIE}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}" + ("; Secure" if self.secure else "")

    def logout(self, cookie):
        with self.lock:
            self.sessions.pop(self.session_key(cookie), None)
        return self.cookie("", 0)


def main():
    parser = argparse.ArgumentParser(description="Создать или сменить пароль входа в Инвест Обзор")
    parser.add_argument("--file", required=True, type=Path)
    parser.add_argument("--username", default="chat")
    parser.add_argument("--password-stdin", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if args.password_stdin:
        import sys
        password = sys.stdin.readline(1026).rstrip("\n")
    else:
        password = getpass.getpass("Новый пароль (от 16 символов): ")
        if password != getpass.getpass("Повторите пароль: "):
            parser.error("Пароли не совпали")
    try:
        write_password(args.file, args.username, password, args.force)
    except AccessError as exc:
        parser.error(str(exc))
    print("Хеш пароля сохранён. Перезапустите приложение для применения.")


if __name__ == "__main__":
    main()
