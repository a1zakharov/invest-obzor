#!/usr/bin/env python3
"""Invest Obzor: local portfolio accounting and read-only broker integration."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import mimetypes
import os
import sqlite3
import tempfile
from contextlib import closing, contextmanager
from datetime import date, datetime, timezone
from decimal import DefaultContext, Decimal, InvalidOperation, getcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import tbank
import access_control

DefaultContext.prec = 36
getcontext().prec = 36
ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("INVEST_DB", str(ROOT / "data" / "invest.sqlite3")))
CURRENCIES = ("RUB", "USD", "EUR", "CNY", "GBP", "HKD", "CHF", "KZT")
KINDS = ("stock", "bond", "fund", "crypto", "metal", "other")
TYPES = ("deposit", "withdrawal", "buy", "sell", "dividend", "coupon", "fee")
CSV_FIELDS = ["date", "type", "ticker", "name", "kind", "category", "currency",
              "quantity", "price", "amount", "fee", "tax", "fx_rate", "note"]
ZERO = Decimal(0)


class ValidationError(Exception):
    pass


def number(value, label="Число", minimum=ZERO, positive=False):
    try:
        result = Decimal(str(value).strip().replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValidationError(f"{label}: укажите число") from None
    if not result.is_finite() or abs(result) > Decimal("1e18") or result.as_tuple().exponent < -12:
        raise ValidationError(f"{label}: недопустимое значение или больше 12 знаков после запятой")
    if result < minimum or (positive and result == 0):
        raise ValidationError(f"{label}: значение должно быть {'больше нуля' if positive else 'неотрицательным'}")
    return result


def label(value, name, limit=120):
    value = str(value or "").strip()
    if not value or len(value) > limit:
        raise ValidationError(f"{name}: от 1 до {limit} символов")
    return value


def choice(value, choices, name):
    if value not in choices:
        raise ValidationError(f"{name}: неизвестное значение")
    return value


def valid_date(value):
    try:
        result = date.fromisoformat(str(value))
        if result.isoformat() != value or result.year < 1900 or result > date.today():
            raise ValueError()
        return value
    except (TypeError, ValueError):
        raise ValidationError("Дата операции: укажите дату с 1900 года по сегодняшний день") from None


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      default=lambda obj: str(obj) if isinstance(obj, Decimal) else dict(obj)).encode()


@contextmanager
def connection():
    db = sqlite3.connect(DB_PATH, timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS portfolios (
          id INTEGER PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL,
          goal TEXT NOT NULL DEFAULT '0', monthly_contribution TEXT NOT NULL DEFAULT '0',
          is_demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rates (
          currency TEXT PRIMARY KEY, rub TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS assets (
          id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
          ticker TEXT NOT NULL, name TEXT NOT NULL, kind TEXT NOT NULL, category TEXT NOT NULL,
          currency TEXT NOT NULL, price TEXT NOT NULL, annual_dividend TEXT NOT NULL DEFAULT '0',
          target_weight TEXT NOT NULL DEFAULT '0', price_updated_at TEXT NOT NULL,
          UNIQUE(portfolio_id, ticker)
        );
        CREATE TABLE IF NOT EXISTS transactions (
          id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
          asset_id INTEGER REFERENCES assets(id) ON DELETE RESTRICT,
          date TEXT NOT NULL, type TEXT NOT NULL, currency TEXT NOT NULL,
          quantity TEXT NOT NULL DEFAULT '0', price TEXT NOT NULL DEFAULT '0', amount TEXT NOT NULL DEFAULT '0',
          fee TEXT NOT NULL DEFAULT '0', tax TEXT NOT NULL DEFAULT '0', fx_rate TEXT NOT NULL,
          note TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS transactions_order ON transactions(portfolio_id, date, id);
        CREATE TABLE IF NOT EXISTS snapshots (
          portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
          date TEXT NOT NULL, equity TEXT NOT NULL, contributions TEXT NOT NULL,
          PRIMARY KEY(portfolio_id, date)
        );
        CREATE TABLE IF NOT EXISTS imports (
          portfolio_id INTEGER NOT NULL REFERENCES portfolios(id) ON DELETE CASCADE,
          hash TEXT NOT NULL, PRIMARY KEY(portfolio_id, hash)
        );
        """)
        tbank.initialize(db)
        # Foreign currencies must be configured explicitly; never present example rates as live data.
        db.execute("INSERT OR IGNORE INTO rates VALUES ('RUB', '1', ?)", (now(),))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def portfolio(db, pid):
    result = db.execute("SELECT * FROM portfolios WHERE id=?", (pid,)).fetchone()
    if not result:
        raise ValidationError("Портфель не найден")
    return dict(result)


def fx(db, currency, base):
    if currency == base:
        return Decimal(1)
    rows = {r["currency"]: Decimal(r["rub"]) for r in db.execute("SELECT * FROM rates")}
    if currency not in rows or base not in rows:
        raise ValidationError(f"Сначала задайте курсы {currency} и {base} к рублю в настройках")
    return rows[currency] / rows[base]


def create_portfolio(db, data):
    name = label(data.get("name"), "Название портфеля")
    currency = choice(data.get("currency", "RUB"), CURRENCIES, "Валюта")
    goal = number(data.get("goal", 0), "Цель")
    monthly = number(data.get("monthly_contribution", 0), "Пополнение")
    return db.execute("INSERT INTO portfolios(name,currency,goal,monthly_contribution) VALUES (?,?,?,?)",
                      (name, currency, str(goal), str(monthly))).lastrowid


def save_asset(db, pid, data, aid=None):
    p = portfolio(db, pid)
    ticker = label(data.get("ticker"), "Тикер", 30).upper()
    name = label(data.get("name"), "Название актива")
    kind = choice(data.get("kind", "stock"), KINDS, "Тип актива")
    category = label(data.get("category", "Без категории"), "Категория", 80)
    currency = choice(data.get("currency", p["currency"]), CURRENCIES, "Валюта")
    fx(db, currency, p["currency"])
    price = number(data.get("price", 0), "Котировка")
    dividend = number(data.get("annual_dividend", 0), "Выплата на единицу в год")
    target = number(data.get("target_weight", 0), "Целевая доля")
    if target > 100:
        raise ValidationError("Целевая доля: не больше 100%")
    fields = (ticker, name, kind, category, currency, str(price), str(dividend), str(target), now())
    if aid is None:
        return db.execute("""INSERT INTO assets(ticker,name,kind,category,currency,price,
          annual_dividend,target_weight,price_updated_at,portfolio_id) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                          (*fields, pid)).lastrowid
    old = db.execute("SELECT * FROM assets WHERE id=? AND portfolio_id=?", (aid, pid)).fetchone()
    if not old:
        raise ValidationError("Актив не найден")
    if old["currency"] != currency and db.execute("SELECT 1 FROM transactions WHERE asset_id=?", (aid,)).fetchone():
        raise ValidationError("Нельзя менять валюту актива с операциями")
    db.execute("""UPDATE assets SET ticker=?,name=?,kind=?,category=?,currency=?,price=?,
      annual_dividend=?,target_weight=?,price_updated_at=? WHERE id=?""", (*fields, aid))
    return aid


def save_transaction(db, pid, data, tid=None):
    p = portfolio(db, pid)
    kind = choice(data.get("type"), TYPES, "Тип операции")
    day = valid_date(data.get("date"))
    aid = data.get("asset_id") or None
    if kind in ("buy", "sell", "dividend", "coupon"):
        if isinstance(aid, bool) or not isinstance(aid, (int, str)) or not str(aid).isdigit():
            raise ValidationError("Выберите актив из текущего портфеля")
        asset = db.execute("SELECT * FROM assets WHERE id=? AND portfolio_id=?", (aid, pid)).fetchone()
        if not asset:
            raise ValidationError("Выберите актив из текущего портфеля")
        currency = asset["currency"]
    else:
        aid = None
        currency = choice(data.get("currency", p["currency"]), CURRENCIES, "Валюта")
    default_rate = fx(db, currency, p["currency"]).quantize(Decimal("0.000000000001"))
    rate = number(data.get("fx_rate") or default_rate, "Курс операции", positive=True)
    if currency == p["currency"] and rate != 1:
        raise ValidationError("Курс операции в базовой валюте должен равняться 1")
    fee = number(data.get("fee", 0), "Комиссия")
    tax = number(data.get("tax", 0), "Налог")
    if kind in ("buy", "sell"):
        qty = number(data.get("quantity", 0), "Количество", positive=True)
        price = number(data.get("price", 0), "Цена", positive=True)
        amount = qty * price
    else:
        qty, price = ZERO, ZERO
        amount = number(data.get("amount", 0), "Сумма", positive=True)
    if kind not in ("dividend", "coupon") and tax:
        raise ValidationError("Налог в этом поле доступен только для дивидендов и купонов")
    if kind in ("dividend", "coupon") and tax + fee > amount:
        raise ValidationError("Налог и комиссия превышают выплату")
    note = str(data.get("note", "")).strip()
    if len(note) > 1000:
        raise ValidationError("Комментарий: не больше 1000 символов")
    fields = (aid, day, kind, currency, str(qty), str(price), str(amount), str(fee), str(tax), str(rate), note)
    if tid is None:
        return db.execute("""INSERT INTO transactions(asset_id,date,type,currency,quantity,price,amount,
          fee,tax,fx_rate,note,portfolio_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""", (*fields, pid)).lastrowid
    if not db.execute("SELECT 1 FROM transactions WHERE id=? AND portfolio_id=?", (tid, pid)).fetchone():
        raise ValidationError("Операция не найдена")
    db.execute("""UPDATE transactions SET asset_id=?,date=?,type=?,currency=?,quantity=?,price=?,amount=?,
      fee=?,tax=?,fx_rate=?,note=? WHERE id=?""", (*fields, tid))
    return tid


def calculate(db, pid):
    p = portfolio(db, pid)
    assets = {a["id"]: dict(a) for a in db.execute("SELECT * FROM assets WHERE portfolio_id=? ORDER BY ticker", (pid,))}
    holdings = {aid: {"quantity": ZERO, "cost": ZERO, "cost_base": ZERO, "realized": ZERO, "income": ZERO}
                for aid in assets}
    cash = {}
    contributions = income = fees = taxes = realized = ZERO
    months = {}
    transactions = []
    for row in db.execute("SELECT * FROM transactions WHERE portfolio_id=? ORDER BY date,id", (pid,)):
        t = dict(row)
        transactions.append(t)
        kind, cur = t["type"], t["currency"]
        qty, amount, fee, tax, rate = (Decimal(t[key]) for key in ("quantity", "amount", "fee", "tax", "fx_rate"))
        cash.setdefault(cur, ZERO)
        fees += fee * rate
        taxes += tax * rate
        if kind == "deposit":
            cash[cur] += amount - fee
            contributions += amount * rate
        elif kind == "withdrawal":
            cash[cur] -= amount + fee
            contributions -= amount * rate
        elif kind == "fee":
            cash[cur] -= amount + fee
            fees += amount * rate
        elif kind in ("dividend", "coupon"):
            net = (amount - fee - tax) * rate
            income += net
            holdings[t["asset_id"]]["income"] += net
            cash[cur] += amount - fee - tax
            month = t["date"][:7]
            months[month] = months.get(month, ZERO) + net
        else:
            h = holdings[t["asset_id"]]
            if kind == "buy":
                h["quantity"] += qty
                h["cost"] += amount + fee
                h["cost_base"] += (amount + fee) * rate
                cash[cur] -= amount + fee
            else:
                if qty > h["quantity"]:
                    raise ValidationError(f"{t['date']}: продажа {assets[t['asset_id']]['ticker']} превышает остаток на эту дату")
                fraction = qty / h["quantity"]
                cost, base_cost = h["cost"] * fraction, h["cost_base"] * fraction
                gain = (amount - fee) * rate - base_cost
                h["quantity"] -= qty
                h["cost"] -= cost
                h["cost_base"] -= base_cost
                h["realized"] += gain
                realized += gain
                cash[cur] += amount - fee
    positions = []
    market = cost_basis = annual = ZERO
    categories = {}
    for aid, a in assets.items():
        h = holdings[aid]
        rate = fx(db, a["currency"], p["currency"])
        value = h["quantity"] * Decimal(a["price"]) * rate
        payout = h["quantity"] * Decimal(a["annual_dividend"]) * rate
        gain = value - h["cost_base"]
        market += value
        cost_basis += h["cost_base"]
        annual += payout
        categories[a["category"]] = categories.get(a["category"], ZERO) + value
        positions.append({**a, **h, "value": value, "gain": gain,
                          "gain_pct": gain / h["cost_base"] * 100 if h["cost_base"] else None,
                          "average_price": h["cost"] / h["quantity"] if h["quantity"] else ZERO,
                          "annual_income": payout, "fx_rate": rate})
    cash_base = sum((value * fx(db, cur, p["currency"]) for cur, value in cash.items()), ZERO)
    equity = market + cash_base
    for a in positions:
        a["weight"] = a["value"] / market * 100 if market else ZERO
    return {"portfolio": p, "assets": positions, "transactions": list(reversed(transactions)),
            "cash": [{"currency": cur, "amount": amount, "value": amount * fx(db, cur, p["currency"])}
                     for cur, amount in cash.items()],
            "summary": {"equity": equity, "market_value": market, "cash": cash_base,
                        "cost_basis": cost_basis, "net_contributions": contributions,
                        "profit": equity - contributions, "unrealized": market - cost_basis,
                        "realized": realized, "income": income, "fees": fees, "taxes": taxes,
                        "annual_income": annual, "dividend_yield": annual / market * 100 if market else ZERO},
            "categories": [{"name": k, "value": v} for k, v in categories.items() if v],
            "income_history": [{"month": k, "amount": v} for k, v in sorted(months.items())],
            "history": [dict(r) for r in db.execute("SELECT * FROM snapshots WHERE portfolio_id=? ORDER BY date", (pid,))]}


def snapshot(db, pid):
    state = calculate(db, pid)
    s = state["summary"]
    db.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?)",
               (pid, date.today().isoformat(), str(s["equity"]), str(s["net_contributions"])))


def import_csv(db, pid, data):
    text = data.get("csv", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 1_000_000:
        raise ValidationError("Выберите непустой CSV-файл до 1 МБ")
    digest = hashlib.sha256(text.encode()).hexdigest()
    if db.execute("SELECT 1 FROM imports WHERE portfolio_id=? AND hash=?", (pid, digest)).fetchone():
        raise ValidationError("Этот файл уже импортирован в портфель")
    sample = text.lstrip("\ufeff")
    first = sample.splitlines()[0]
    delimiter = ";" if first.count(";") > first.count(",") else ","
    reader = csv.DictReader(io.StringIO(sample), delimiter=delimiter)
    if not reader.fieldnames or not {"date", "type", "currency"}.issubset(reader.fieldnames):
        raise ValidationError("В CSV обязательны столбцы date, type, currency. Скачайте шаблон.")
    rows = list(reader)
    if not rows or len(rows) > 5000:
        raise ValidationError("CSV должен содержать от 1 до 5000 операций")
    prepared = []
    created = 0
    # Imported rows are inserted in chronological order, preserving same-day source order.
    for index, row in sorted(enumerate(rows, 2), key=lambda item: (item[1].get("date", ""), item[0])):
        try:
            row = {k: v.strip() for k, v in row.items() if k is not None and v is not None}
            ticker = row.get("ticker", "").upper()
            aid = None
            if row.get("type") in ("buy", "sell", "dividend", "coupon"):
                if not ticker:
                    raise ValidationError("Для операции с активом нужен ticker")
                found = db.execute("SELECT * FROM assets WHERE portfolio_id=? AND ticker=?", (pid, ticker)).fetchone()
                if found:
                    aid = found["id"]
                    if row.get("currency") != found["currency"]:
                        raise ValidationError("Валюта не совпадает с валютой актива")
                else:
                    aid = save_asset(db, pid, {"ticker": ticker, "name": row.get("name") or ticker,
                        "kind": row.get("kind") or "stock", "category": row.get("category") or "Без категории",
                        "currency": row.get("currency"), "price": row.get("price") or "0"})
                    created += 1
            values = {key: value for key, value in row.items() if value != ""}
            values["asset_id"] = aid
            save_transaction(db, pid, values)
            prepared.append({"row": index, "date": row.get("date"), "type": row.get("type"), "ticker": ticker})
        except ValidationError as exc:
            raise ValidationError(f"Строка {index}: {exc}") from None
    calculate(db, pid)  # Validate sales against the entire chronological ledger.
    db.execute("INSERT INTO imports VALUES (?,?)", (pid, digest))
    return {"count": len(prepared), "created_assets": created, "preview": prepared[:30]}


def demo(db):
    existing = db.execute("SELECT id FROM portfolios WHERE is_demo=1").fetchone()
    if existing:
        return existing["id"]
    pid = create_portfolio(db, {"name": "Демо · Дивидендный", "currency": "RUB", "goal": 3000000, "monthly_contribution": 25000})
    db.execute("UPDATE portfolios SET is_demo=1 WHERE id=?", (pid,))
    today = date.today()
    year = today.year - 1
    save_transaction(db, pid, {"type": "deposit", "date": f"{year}-01-10", "amount": 1000000, "currency": "RUB"})
    examples = [("SBER", "Сбербанк", "stock", "Финансы", "285.4", "260", "1000", "34.84"),
                ("LKOH", "Лукойл", "stock", "Энергетика", "6850", "6200", "40", "900"),
                ("MOEX", "Московская биржа", "stock", "Финансы", "195.7", "180", "800", "20"),
                ("SU26238", "ОФЗ 26238", "bond", "Облигации", "640", "610", "200", "71"),
                ("GOLD", "Золотой фонд", "fund", "Защитные активы", "16.2", "13.5", "6000", "0")]
    for ticker, name, kind, category, price, cost, qty, div in examples:
        aid = save_asset(db, pid, {"ticker": ticker, "name": name, "kind": kind, "category": category,
            "currency": "RUB", "price": price, "annual_dividend": div, "target_weight": 20})
        save_transaction(db, pid, {"type": "buy", "date": f"{year}-01-15", "asset_id": aid,
                                 "quantity": qty, "price": cost, "fee": 50})
        if Decimal(div):
            for month in (3, 6, 9, 12):
                save_transaction(db, pid, {"type": "coupon" if kind == "bond" else "dividend",
                    "date": f"{year}-{month:02d}-20", "asset_id": aid,
                    "amount": str(Decimal(div) * Decimal(qty) / 4),
                    "tax": str(Decimal(div) * Decimal(qty) / 4 * Decimal("0.13"))})
    # Demonstration history is deliberately synthetic and is visibly marked in the UI.
    for m in range(1, 13):
        day = f"{year}-{m:02d}-28"
        value = Decimal(1000000) + Decimal(m * 11200 + (m % 3 - 1) * 8200)
        db.execute("INSERT INTO snapshots VALUES (?,?,?,?)", (pid, day, str(value), "1000000"))
    snapshot(db, pid)
    return pid


class Handler(BaseHTTPRequestHandler):
    server_version = "InvestObzor/1.0"

    def log_message(self, fmt, *args):
        # Request bodies and personal financial data are never logged.
        super().log_message(fmt, *args)

    def respond(self, status, body, content_type="application/json; charset=utf-8", extra=None):
        payload = encode(body) if content_type.startswith("application/json") else body
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def do_PUT(self):
        self.dispatch()

    def do_DELETE(self):
        self.dispatch()

    def dispatch(self):
        try:
            path = urlparse(self.path).path
            access = getattr(self.server, "access", None)
            logged_in = access is None or access.authenticated(self.headers.get("Cookie"))
            public_api = path in ("/api/health", "/api/auth/status", "/api/auth/login")
            public_static = path in ("/login.html", "/login.js", "/login.css", "/favicon.svg")
            if not logged_in and not public_api and not public_static:
                if path.startswith("/api/"):
                    self.respond(401, {"error": "Войдите в приложение"})
                else:
                    self.respond(303, b"", "text/plain", {"Location": "/login.html"})
                return
            if path.startswith("/api/"):
                # Reject cross-origin requests and DNS rebinding, including in explicit LAN deployments.
                host = self.headers.get("Host", "")
                hostname = urlparse("http://" + host).hostname
                allowed_hosts = {"localhost", "127.0.0.1", "::1", "testserver"}
                allowed_hosts.update(value.strip().lower() for value in os.environ.get("INVEST_ALLOWED_HOSTS", "").split(",") if value.strip())
                if hostname not in allowed_hosts:
                    self.respond(403, {"error": "Адрес не разрешён. Используйте настроенный адрес сервиса."})
                    return
                origin = self.headers.get("Origin")
                if origin and origin != "http://" + host:
                    self.respond(403, {"error": "Запрос с другого сайта запрещён"})
                    return
                data = {}
                if self.command in ("POST", "PUT"):
                    if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                        raise ValidationError("Ожидается JSON")
                    length = int(self.headers.get("Content-Length", "0"))
                    if length <= 0 or length > 2_000_000:
                        raise ValidationError("Недопустимый размер запроса")
                    data = json.loads(self.rfile.read(length))
                    if not isinstance(data, dict):
                        raise ValidationError("Ожидается объект JSON")
                with connection() as db:
                    result = self.api(db, path, data)
                if result is not None:
                    self.respond(200, result)
                return
            if self.command != "GET":
                self.respond(405, {"error": "Метод не поддерживается"})
                return
            if path == "/":
                file = ROOT / "static" / "index.html"
            else:
                file = (ROOT / "static" / path.removeprefix("/")).resolve()
            if not file.is_relative_to(ROOT / "static") or not file.is_file():
                self.respond(404, {"error": "Не найдено"})
                return
            mime = mimetypes.guess_type(str(file))[0] or "application/octet-stream"
            self.respond(200, file.read_bytes(), mime + ("; charset=utf-8" if mime.startswith("text/") or "javascript" in mime else ""))
        except (ValidationError, tbank.BrokerError, json.JSONDecodeError, ValueError, TypeError, InvalidOperation, OverflowError) as exc:
            self.respond(400, {"error": str(exc) if isinstance(exc, (ValidationError, tbank.BrokerError)) else "Проверьте формат данных запроса"})
        except sqlite3.IntegrityError:
            self.respond(400, {"error": "Тикер уже существует или запись используется в операциях"})
        except sqlite3.OperationalError:
            self.respond(503, {"error": "База данных временно недоступна. Повторите запрос."})

    def api(self, db, path, data):
        parts = path.strip("/").split("/")
        method = self.command
        access = getattr(self.server, "access", None)
        if path == "/api/auth/status" and method == "GET":
            return {"required": bool(access and access.enabled), "authenticated": access is None or access.authenticated(self.headers.get("Cookie"))}
        if path == "/api/auth/login" and method == "POST":
            try:
                if access is None:
                    raise access_control.AccessError("Вход по паролю не настроен")
                cookie = access.login(data, self.client_address[0])
                self.respond(200, {"ok": True}, extra={"Set-Cookie": cookie})
            except access_control.AccessError as exc:
                self.respond(401, {"error": str(exc)})
            return None
        if path == "/api/auth/logout" and method == "POST":
            cookie = access.logout(self.headers.get("Cookie")) if access else ""
            self.respond(200, {"ok": True}, extra={"Set-Cookie": cookie})
            return None
        if path == "/api/auth/password" and method == "POST":
            try:
                if access is None:
                    raise access_control.AccessError("Вход по паролю не настроен")
                cookie = access.change_password(data, self.headers.get("Cookie"), self.client_address[0])
                self.respond(200, {"ok": True}, extra={"Set-Cookie": cookie})
            except access_control.AccessError as exc:
                self.respond(400, {"error": str(exc)})
            return None
        if path == "/api/tbank" or path.startswith("/api/tbank/"):
            return tbank.route(db, path, method, data)
        if path == "/api/health" and method == "GET":
            return {"status": "ok", "version": 1}
        if path == "/api/meta" and method == "GET":
            return {"portfolios": [dict(r) for r in db.execute("SELECT * FROM portfolios ORDER BY is_demo,id")],
                    "rates": [dict(r) for r in db.execute("SELECT * FROM rates ORDER BY currency")],
                    "currencies": CURRENCIES, "today": date.today().isoformat()}
        if path == "/api/portfolios" and method == "POST":
            pid = create_portfolio(db, data)
            snapshot(db, pid)
            return {"id": pid}
        if path == "/api/demo" and method == "POST":
            return {"id": demo(db)}
        if path == "/api/rates" and method == "PUT":
            values = data.get("rates")
            if not isinstance(values, dict) or not values:
                raise ValidationError("Передайте курсы валют")
            for cur, val in values.items():
                choice(cur, CURRENCIES, "Валюта")
                rate = number(val, "Курс", positive=True)
                if cur == "RUB" and rate != 1:
                    raise ValidationError("Курс рубля должен равняться 1")
                db.execute("INSERT OR REPLACE INTO rates VALUES (?,?,?)", (cur, str(rate), now()))
            for p in db.execute("SELECT id FROM portfolios").fetchall():
                snapshot(db, p["id"])
            return {"ok": True}
        if path == "/api/backup" and method == "GET":
            with tempfile.TemporaryDirectory() as directory:
                filename = Path(directory) / "backup.sqlite3"
                with closing(sqlite3.connect(filename)) as target:
                    db.backup(target)
                    # Backups contain financial data, but never reusable credentials.
                    target.execute("DELETE FROM broker_credentials")
                    target.commit()
                    target.execute("VACUUM")
                self.respond(200, filename.read_bytes(), "application/octet-stream",
                             {"Content-Disposition": f'attachment; filename="invest-obzor-{date.today()}.sqlite3"'})
            return None
        if len(parts) < 3 or parts[1] != "portfolios" or not parts[2].isdigit():
            self.respond(404, {"error": "Не найдено"})
            return None
        pid = int(parts[2])
        p = portfolio(db, pid)
        if len(parts) == 3:
            if method == "GET":
                snapshot(db, pid)
                return calculate(db, pid)
            if method == "PUT":
                name = label(data.get("name"), "Название портфеля")
                goal = number(data.get("goal", 0), "Цель")
                monthly = number(data.get("monthly_contribution", 0), "Пополнение")
                db.execute("UPDATE portfolios SET name=?,goal=?,monthly_contribution=? WHERE id=?",
                           (name, str(goal), str(monthly), pid))
                return {"ok": True}
            if method == "DELETE":
                # Delete child operations first, as assets use RESTRICT to protect ordinary deletion.
                db.execute("DELETE FROM transactions WHERE portfolio_id=?", (pid,))
                db.execute("DELETE FROM portfolios WHERE id=?", (pid,))
                return {"ok": True}
        if len(parts) == 4 and parts[3] == "export" and method == "GET":
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=CSV_FIELDS)
            writer.writeheader()
            for row in db.execute("""SELECT t.*,a.ticker,a.name,a.kind,a.category FROM transactions t
                LEFT JOIN assets a ON a.id=t.asset_id WHERE t.portfolio_id=? ORDER BY t.date,t.id""", (pid,)):
                writer.writerow({key: row[key] or "" for key in CSV_FIELDS})
            self.respond(200, ("\ufeff" + output.getvalue()).encode(), "text/csv; charset=utf-8",
                         {"Content-Disposition": f'attachment; filename="portfolio-{pid}.csv"'})
            return None
        if len(parts) == 4 and parts[3] == "import" and method == "POST":
            db.execute("SAVEPOINT csv_import")
            result = import_csv(db, pid, data)
            if data.get("dry_run", True):
                db.execute("ROLLBACK TO csv_import")
                db.execute("RELEASE csv_import")
            else:
                db.execute("RELEASE csv_import")
                snapshot(db, pid)
            return result
        if len(parts) in (4, 5) and parts[3] in ("assets", "transactions"):
            resource = parts[3]
            rid = int(parts[4]) if len(parts) == 5 and parts[4].isdigit() else None
            if len(parts) == 5 and rid is None:
                raise ValidationError("Недопустимый идентификатор")
            if method == "POST" and len(parts) == 4:
                rid = save_asset(db, pid, data) if resource == "assets" else save_transaction(db, pid, data)
            elif method == "PUT" and rid:
                save_asset(db, pid, data, rid) if resource == "assets" else save_transaction(db, pid, data, rid)
            elif method == "DELETE" and rid:
                db.execute(f"DELETE FROM {resource} WHERE id=? AND portfolio_id=?", (rid, pid))
            else:
                self.respond(405, {"error": "Метод не поддерживается"})
                return None
            snapshot(db, pid)  # Full ledger validation makes mutations atomic.
            return {"id": rid, "ok": True}
        self.respond(404, {"error": "Не найдено"})
        return None


def main():
    global DB_PATH
    parser = argparse.ArgumentParser(description="Локальный учёт инвестиций")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    args = parser.parse_args()
    DB_PATH = args.db.resolve()
    init_db()
    access = access_control.Access.from_env()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.access = access
    print(f"Invest Obzor: http://localhost:{args.port} · данные: {DB_PATH}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
