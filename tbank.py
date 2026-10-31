"""Read-only T-Invest REST client and encrypted credential storage.

Only the four allowlisted read methods below can be called. Raw broker responses
are stored separately from the manual investment ledger.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import socket
import ssl
import stat
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

API_URL = "https://invest-public-api.tbank.ru/rest/"
METHODS = {
    "accounts": "UsersService/GetAccounts",
    "portfolio": "OperationsService/GetPortfolio",
    "positions": "OperationsService/GetPositions",
    "operations": "OperationsService/GetOperationsByCursor",
}
SYNC_LOCK = threading.Lock()
MAX_RESPONSE = 16_000_000
MAX_PAGES = 100
TLS_ERROR = "Не удалось проверить TLS-сертификат Т-Инвестиций. Проверьте сертификаты приложения (INVEST_TBANK_CA_FILE)."
NETWORK_ERROR = "Не удалось связаться с Т-Инвестициями. Проверьте интернет и повторите запрос."


class BrokerError(Exception):
    """Messages are safe for the UI: never include tokens or raw upstream errors."""


def initialize(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS broker_credentials (
      provider TEXT PRIMARY KEY, ciphertext BLOB NOT NULL,
      generation TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS broker_accounts (
      id TEXT PRIMARY KEY, info TEXT NOT NULL, snapshot TEXT,
      synced_at TEXT, operations_from TEXT, operations_to TEXT
    );
    CREATE TABLE IF NOT EXISTS broker_operations (
      account_id TEXT NOT NULL REFERENCES broker_accounts(id) ON DELETE CASCADE,
      id TEXT NOT NULL, date TEXT NOT NULL, data TEXT NOT NULL,
      PRIMARY KEY(account_id, id)
    );
    CREATE INDEX IF NOT EXISTS broker_operations_date ON broker_operations(account_id,date);
    """)


def utcnow():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def key_path():
    return Path(os.environ.get("INVEST_MASTER_KEY_FILE", str(Path(__file__).resolve().parent / ".secrets" / "master.key")))


def cipher(create=False):
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise BrokerError("Установите зависимости: python -m pip install -r requirements.txt") from None
    path = key_path()
    try:
        if create and not path.exists():
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            # Never replace a key created by another request/process.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(Fernet.generate_key())
                    handle.flush()
                    os.fsync(handle.fileno())
        # O_NOFOLLOW prevents a substituted symlink from changing the key source.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise BrokerError("Ключ шифрования должен быть обычным файлом")
            if os.name == "posix" and stat.S_IMODE(info.st_mode) & 0o077:
                raise BrokerError("Установите права 0600 на файл ключа шифрования")
            key = handle.read(128).strip()
        return Fernet(key)
    except BrokerError:
        raise
    except (OSError, ValueError):
        raise BrokerError("Ключ шифрования недоступен или повреждён. Восстановите его либо удалите подключение и сохраните токен заново.") from None


def credential(db):
    row = db.execute("SELECT * FROM broker_credentials WHERE provider='tbank'").fetchone()
    if not row:
        raise BrokerError("Сначала сохраните токен Т-Инвестиций")
    # No auto-creation when an existing encrypted credential loses its key.
    fernet = cipher()
    try:
        return fernet.decrypt(row["ciphertext"]).decode("ascii"), row["generation"]
    except Exception:
        raise BrokerError("Не удалось расшифровать токен. Восстановите ключ либо удалите подключение и введите токен заново.") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        # An Authorization header must never follow a redirect to another host.
        return None


def tls_context():
    # Extend only this client's context, never system/process-wide trust settings.
    context = ssl.create_default_context()
    filename = os.environ.get("INVEST_TBANK_CA_FILE") or str(Path(__file__).resolve().parent / "certs" / "russian-trusted-root-ca.crt")
    try:
        context.load_verify_locations(cafile=filename)
    except (OSError, ssl.SSLError):
        raise BrokerError("Не удалось загрузить сертификаты Т-Инвестиций. Проверьте файл INVEST_TBANK_CA_FILE.") from None
    return context


class Client:
    def __init__(self, token):
        self._token = token
        # Ignore ambient proxies: this client sends a financial credential.
        self._opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=tls_context()))

    def call(self, method, data):
        if method not in METHODS:
            raise BrokerError("Метод Т-Инвестиций не разрешён")
        url = API_URL + "tinkoff.public.invest.api.contract.v1." + METHODS[method]
        request = Request(url, data=json.dumps(data).encode(), method="POST", headers={
            "Authorization": "Bearer " + self._token,
            "Content-Type": "application/json", "Accept": "application/json",
            "User-Agent": "InvestObzor/1.1",
        })
        try:
            with self._opener.open(request, timeout=25) as response:
                raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise BrokerError("Ответ Т-Инвестиций слишком большой. Выберите меньший период.")
            result = json.loads(raw)
            if not isinstance(result, dict) or "code" in result:
                raise BrokerError("Т-Инвестиции вернули неожиданный ответ")
            return result
        except HTTPError as exc:
            status = exc.code
            exc.close()
            if status in (401, 403):
                message = "Т-Инвестиции отклонили доступ. Проверьте срок действия, права токена и доступ к счёту."
            elif status == 429:
                message = "Достигнут лимит запросов Т-Инвестиций. Повторите синхронизацию позже."
            else:
                message = f"Т-Инвестиции временно недоступны или отклонили запрос (HTTP {status})."
            raise BrokerError(message) from None
        except ssl.SSLCertVerificationError:
            raise BrokerError(TLS_ERROR) from None
        except URLError as exc:
            message = TLS_ERROR if isinstance(exc.reason, ssl.SSLCertVerificationError) else NETWORK_ERROR
            raise BrokerError(message) from None
        except (TimeoutError, socket.timeout, OSError):
            raise BrokerError(NETWORK_ERROR) from None
        except (ValueError, UnicodeError):
            raise BrokerError("Т-Инвестиции вернули некорректный ответ") from None

    def accounts(self):
        items = self.call("accounts", {"status": "ACCOUNT_STATUS_ALL"}).get("accounts")
        if not isinstance(items, list):
            raise BrokerError("Т-Инвестиции не вернули список счетов")
        accessible = [a for a in items if a.get("accessLevel") not in ("ACCOUNT_ACCESS_LEVEL_NO_ACCESS", 3)]
        if not accessible:
            raise BrokerError("У токена нет доступных инвестиционных счетов")
        if any(a.get("accessLevel") not in ("ACCOUNT_ACCESS_LEVEL_READ_ONLY", 2) for a in accessible):
            raise BrokerError("Создайте отдельный токен с правами «Только чтение». Токены с торговыми правами не принимаются.")
        if any(not isinstance(a.get("id"), str) or not a["id"] for a in accessible):
            raise BrokerError("Т-Инвестиции вернули некорректные идентификаторы счетов")
        return accessible

    def operations(self, account_id, start, end):
        cursor, seen, items = "", set(), {}
        for _ in range(MAX_PAGES):
            result = self.call("operations", {"accountId": account_id, "from": start, "to": end,
                "cursor": cursor, "limit": 1000, "state": "OPERATION_STATE_EXECUTED",
                "withoutCommissions": False, "withoutTrades": False, "withoutOvernights": False})
            rows = result.get("items", [])
            if not isinstance(rows, list):
                raise BrokerError("Некорректный список операций Т-Инвестиций")
            for row in rows:
                if not isinstance(row, dict) or not row.get("id") or not row.get("date"):
                    raise BrokerError("Некорректная операция Т-Инвестиций")
                items[str(row["id"])] = row
            if not result.get("hasNext", False):
                return list(items.values())
            cursor = result.get("nextCursor")
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise BrokerError("Т-Инвестиции вернули повторяющийся курсор. Старые данные сохранены.")
            seen.add(cursor)
            time.sleep(0.12)  # A bounded read request rate; no automatic retry of incomplete syncs.
        raise BrokerError("В периоде больше 100 страниц операций. Выберите меньший период.")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def save_accounts(db, accounts):
    ids = [a["id"] for a in accounts]
    db.execute("DELETE FROM broker_accounts WHERE id NOT IN (" + ",".join("?" for _ in ids) + ")", ids)
    for a in accounts:
        db.execute("INSERT INTO broker_accounts(id,info) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET info=excluded.info",
                   (a["id"], dumps(a)))


def save_token(db, value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._\-]{20,1024}", value):
        raise BrokerError("Введите токен Т-Инвестиций без пробелов и префикса Bearer")
    previous = db.execute("SELECT generation FROM broker_credentials WHERE provider='tbank'").fetchone()
    previous_generation = previous[0] if previous else None
    accounts = Client(value).accounts()  # Validate permissions before persisting anything.
    encrypted = cipher(create=previous is None).encrypt(value.encode("ascii"))
    db.execute("BEGIN IMMEDIATE")
    current = db.execute("SELECT generation FROM broker_credentials WHERE provider='tbank'").fetchone()
    if (current[0] if current else None) != previous_generation:
        raise BrokerError("Подключение изменилось во время проверки токена. Повторите запрос.")
    db.execute("INSERT OR REPLACE INTO broker_credentials VALUES ('tbank',?,?,?)",
               (encrypted, secrets.token_hex(16), utcnow()))
    save_accounts(db, accounts)
    return {"ok": True, "accounts": len(accounts)}


def status(db):
    row = db.execute("SELECT updated_at FROM broker_credentials WHERE provider='tbank'").fetchone()
    accounts = []
    for a in db.execute("SELECT * FROM broker_accounts ORDER BY id"):
        accounts.append({"id": a["id"], "info": json.loads(a["info"]), "synced_at": a["synced_at"],
                         "operations_from": a["operations_from"], "operations_to": a["operations_to"]})
    return {"configured": row is not None, "updated_at": row["updated_at"] if row else None, "accounts": accounts}


def money(value):
    if not isinstance(value, dict):
        return None
    try:
        amount = Decimal(str(value.get("units", 0))) + Decimal(str(value.get("nano", 0))) / Decimal(1_000_000_000)
        if not amount.is_finite():
            raise ValueError()
        return str(amount)
    except (ValueError, InvalidOperation):
        raise BrokerError("Некорректная денежная сумма в ответе Т-Инвестиций") from None


def account_data(db, account_id):
    row = db.execute("SELECT * FROM broker_accounts WHERE id=?", (account_id,)).fetchone()
    if not row:
        raise BrokerError("Счёт не найден. Обновите список счетов.")
    data = json.loads(row["snapshot"]) if row["snapshot"] else None
    # Broker ledger stays a source journal: never synthesize buys/deposits from holdings.
    page = operation_page(db, account_id, 0)
    return {"id": account_id, "info": json.loads(row["info"]), "snapshot": data,
            "synced_at": row["synced_at"], "operations_from": row["operations_from"],
            "operations_to": row["operations_to"], **page}


def operation_page(db, account_id, offset):
    if isinstance(offset, bool) or not isinstance(offset, int) or not 0 <= offset <= 100000:
        raise BrokerError("Недопустимая страница операций")
    operations = [json.loads(r[0]) for r in db.execute(
        "SELECT data FROM broker_operations WHERE account_id=? ORDER BY date DESC,id DESC LIMIT 100 OFFSET ?",
        (account_id, offset))]
    count = db.execute("SELECT count(*) FROM broker_operations WHERE account_id=?", (account_id,)).fetchone()[0]
    return {"operations": operations, "operations_count": count}


def timestamp(value, default):
    if value in (None, ""):
        return default
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            result = result.replace(tzinfo=timezone.utc)
        return result.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    except ValueError:
        raise BrokerError("Укажите корректную дату начала периода") from None


def sync(db, account_id, start=None):
    if not SYNC_LOCK.acquire(blocking=False):
        raise BrokerError("Синхронизация уже выполняется. Дождитесь её завершения.")
    try:
        token, generation = credential(db)
        client = Client(token)
        accounts = client.accounts()
        account = next((a for a in accounts if a["id"] == account_id), None)
        if not account:
            raise BrokerError("Токен не имеет доступа к выбранному счёту")
        end = utcnow()
        start = timestamp(start, account.get("openedDate") or "2000-01-01T00:00:00Z")
        if start > end or start < "1900-01-01T00:00:00Z":
            raise BrokerError("Дата начала должна быть между 1900 годом и сегодняшним днём")
        # Finish ALL remote reads before taking the SQLite write lock.
        portfolio = client.call("portfolio", {"accountId": account_id, "currency": "RUB"})
        positions = client.call("positions", {"accountId": account_id})
        operations = client.operations(account_id, start, end)
        if not isinstance(portfolio.get("positions", []), list) or "totalAmountPortfolio" not in portfolio:
            raise BrokerError("Т-Инвестиции не вернули полный портфель. Старые данные сохранены.")
        if positions.get("limitsLoadingInProgress"):
            raise BrokerError("Т-Инвестиции ещё рассчитывают остатки. Повторите синхронизацию позже.")
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT generation FROM broker_credentials WHERE provider='tbank'").fetchone()
        if not current or current[0] != generation:
            raise BrokerError("Подключение изменилось во время синхронизации. Повторите запрос.")
        save_accounts(db, accounts)
        db.execute("UPDATE broker_accounts SET snapshot=?,synced_at=?,operations_from=?,operations_to=? WHERE id=?",
                   (dumps({"portfolio": portfolio, "positions": positions}), end, start, end, account_id))
        # Replace only the requested interval: deletions/corrections do not leave stale rows.
        db.execute("DELETE FROM broker_operations WHERE account_id=? AND date>=? AND date<=?", (account_id, start, end))
        for operation in operations:
            db.execute("INSERT INTO broker_operations VALUES (?,?,?,?) ON CONFLICT(account_id,id) DO UPDATE SET date=excluded.date,data=excluded.data",
                       (account_id, str(operation["id"]), timestamp(operation["date"], None), dumps(operation)))
        return {"ok": True, "operations": len(operations), "synced_at": end}
    finally:
        SYNC_LOCK.release()


def route(db, path, method, data):
    if path == "/api/tbank" and method == "GET":
        return status(db)
    if path == "/api/tbank/token":
        if method == "PUT":
            return save_token(db, data.get("token"))
        if method == "DELETE":
            db.execute("DELETE FROM broker_credentials WHERE provider='tbank'")
            return {"ok": True}
    if path == "/api/tbank/accounts" and method == "POST":
        token, generation = credential(db)
        accounts = Client(token).accounts()
        db.execute("BEGIN IMMEDIATE")
        current = db.execute("SELECT generation FROM broker_credentials WHERE provider='tbank'").fetchone()
        if not current or current[0] != generation:
            raise BrokerError("Подключение изменилось. Повторите запрос.")
        save_accounts(db, accounts)
        return {"ok": True}
    match = re.fullmatch(r"/api/tbank/accounts/([A-Za-z0-9_-]{1,128})(/sync|/operations)?", path)
    if match:
        if not match[2] and method == "GET":
            return account_data(db, match[1])
        if match[2] == "/operations" and method == "POST":
            return operation_page(db, match[1], data.get("offset", 0))
        if match[2] == "/sync" and method == "POST":
            return sync(db, match[1], data.get("from"))
    raise BrokerError("Неизвестный запрос Т-Инвестиций")
