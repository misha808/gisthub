"""
HubGift Bot — база даних SQLite
Зберігає дані про користувачів, NFT, угоди та реквізити.
"""

import sqlite3
import json
import logging
from datetime import datetime
from typing import Optional

DB_PATH = "hubgift.db"

logger = logging.getLogger(__name__)


# ==================== ІНІЦІАЛІЗАЦІЯ ====================

def get_conn() -> sqlite3.Connection:
    """Повертає з'єднання з базою даних."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row          # доступ до стовпців по імені
    conn.execute("PRAGMA foreign_keys = ON") # увімкнути зовнішні ключі
    return conn


def init_db():
    """Створює всі таблиці, якщо їх ще немає."""
    with get_conn() as conn:
        conn.executescript("""

        -- ================================================================
        -- USERS — дані про кожного користувача бота
        -- ================================================================
        CREATE TABLE IF NOT EXISTS users (
            user_id         INTEGER PRIMARY KEY,   -- Telegram user_id
            username        TEXT,                  -- @username (може бути NULL)
            full_name       TEXT,                  -- ім'я + прізвище
            language_code   TEXT,                  -- мова клієнта Telegram
            first_seen      TEXT NOT NULL,         -- дата першого /start
            last_active     TEXT NOT NULL,         -- остання активність
            is_banned       INTEGER DEFAULT 0,     -- 0=активний, 1=заблокований
            balance_frozen  INTEGER DEFAULT 0      -- 0=вільний, 1=заморожено
        );

        -- ================================================================
        -- NFT — кожен раз, коли бот оцінював NFT (один slug може
        --       зʼявлятися кілька разів від різних або одного юзера)
        -- ================================================================
        CREATE TABLE IF NOT EXISTS nft_lookups (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            slug            TEXT NOT NULL,          -- напр. HappyBrownie-90097
            title           TEXT,                   -- назва NFT
            attrs_json      TEXT,                   -- JSON {model, backdrop, ...}
            floor_ton       REAL,                   -- floor price (оригінал)
            avg_ton         REAL,                   -- середня ціна
            last_ton        REAL,                   -- остання угода
            floor_ton_up    REAL,                   -- floor * MARKET_MARKUP
            buyout_ton      REAL,                   -- наша пропозиція
            rates_json      TEXT,                   -- JSON {usd, uah, rub, ...}
            nft_link        TEXT,                   -- повне посилання
            looked_at       TEXT NOT NULL           -- дата/час оцінки
        );

        -- ================================================================
        -- REQUISITES — реквізити, які юзер надав для виплати
        -- ================================================================
        CREATE TABLE IF NOT EXISTS requisites (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            raw_text        TEXT NOT NULL,           -- введений текст
            detected_type   TEXT,                    -- результат detect_requisites()
            currency        TEXT,                    -- TON / USDT / UAH / RUB / ...
            created_at      TEXT NOT NULL
        );

        -- ================================================================
        -- DEALS — угода: юзер підтвердив реквізити і чекає виплати
        -- ================================================================
        CREATE TABLE IF NOT EXISTS deals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            nft_lookup_id   INTEGER REFERENCES nft_lookups(id),
            requisite_id    INTEGER REFERENCES requisites(id),
            buyout_ton      REAL,                    -- сума в TON
            buyout_display  TEXT,                    -- рядок «123 грн» / «1.5 TON»
            currency        TEXT,
            status          TEXT DEFAULT 'pending',  -- pending / paid / cancelled
            created_at      TEXT NOT NULL,
            paid_at         TEXT                     -- NULL поки не виплачено
        );

        -- ================================================================
        -- BALANCE_EVENTS — лог поповнень балансу (імітація виплат)
        -- ================================================================
        CREATE TABLE IF NOT EXISTS balance_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(user_id),
            deal_id         INTEGER REFERENCES deals(id),
            amount_display  TEXT NOT NULL,           -- «+1.5 TON»
            sent_at         TEXT NOT NULL
        );

        """)
    # Міграція — escrow_deals таблиця
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS escrow_deals (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id        INTEGER NOT NULL,
                joiner_id         INTEGER,
                role              TEXT NOT NULL,       -- 'buyer' або 'seller' (роль creator)
                amount_ton        REAL NOT NULL,
                gift_name         TEXT NOT NULL,
                seller_requisite  TEXT,
                status            TEXT DEFAULT 'waiting',  -- waiting/active/paid/done/cancelled
                created_at        TEXT NOT NULL
            )
        """)
        # escrow_nfts — список NFT які очікуємо від продавця
        conn.execute("""
            CREATE TABLE IF NOT EXISTS escrow_nfts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                deal_id     INTEGER NOT NULL,
                slug        TEXT NOT NULL,
                title       TEXT NOT NULL,
                received    INTEGER DEFAULT 0,
                created_at  TEXT NOT NULL
            )
        """)

        # Міграції існуючої таблиці
        for col, definition in [
            ("joiner_id", "INTEGER"),
            ("seller_requisite", "TEXT"),
            ("token", "TEXT"),
            ("deal_number", "INTEGER"),
        ]:
            try:
                conn.execute(f"ALTER TABLE escrow_deals ADD COLUMN {col} {definition}")
            except: pass

        # Міграція deals
        for col, definition in [
            ("expected_nft_title", "TEXT"),
            ("expected_nft_slug", "TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE deals ADD COLUMN {col} {definition}")
            except: pass

    # Міграція — додаємо колонки якщо ще немає
    with get_conn() as conn:
        for col, definition in [
            ("balance_frozen", "INTEGER DEFAULT 0"),
            ("label", "TEXT DEFAULT 'Отримано за NFT'"),
        ]:
            try:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {definition}")
            except: pass
        try:
            conn.execute("ALTER TABLE balance_events ADD COLUMN label TEXT DEFAULT 'Отримано за NFT'")
        except: pass

    logger.info("База даних ініціалізована: %s", DB_PATH)


# ==================== USERS ====================

def upsert_user(user_id: int, username: Optional[str], full_name: str,
                language_code: Optional[str] = None):
    """Додає нового або оновлює існуючого користувача."""
    now = _now()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, full_name, language_code,
                               first_seen, last_active)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username    = excluded.username,
                full_name   = excluded.full_name,
                last_active = excluded.last_active
        """, (user_id, username, full_name, language_code, now, now))


def touch_user(user_id: int):
    """Оновлює час останньої активності."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET last_active = ? WHERE user_id = ?",
            (_now(), user_id)
        )


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


# ==================== NFT ====================

def save_nft_lookup(user_id: int, data: dict, link: str,
                    market_markup: float, buyout_percent: float) -> int:
    """
    Зберігає результат оцінки NFT.
    data — словник з parse_nft().
    Повертає id нового запису.
    """
    floor = data.get('floor_ton')
    floor_up = round(floor * market_markup, 2) if floor else None
    buyout = round(floor_up * buyout_percent, 2) if floor_up else None

    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO nft_lookups
                (user_id, slug, title, attrs_json, floor_ton, avg_ton,
                 last_ton, floor_ton_up, buyout_ton, rates_json, nft_link, looked_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id,
            data.get('slug'),
            data.get('title'),
            json.dumps(data.get('attrs', {}), ensure_ascii=False),
            floor,
            data.get('avg_ton'),
            data.get('last_ton'),
            floor_up,
            buyout,
            json.dumps(data.get('rates', {})),
            link,
            _now(),
        ))
        return cur.lastrowid


def get_user_nft_history(user_id: int, limit: int = 20) -> list:
    """Повертає останні NFT-оцінки конкретного юзера."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM nft_lookups
            WHERE user_id = ?
            ORDER BY looked_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


def get_nft_lookup(lookup_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM nft_lookups WHERE id = ?", (lookup_id,)
        ).fetchone()


# ==================== REQUISITES ====================

def save_requisite(user_id: int, raw_text: str,
                   detected_type: str, currency: str) -> int:
    """Зберігає реквізити юзера. Повертає id запису."""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO requisites (user_id, raw_text, detected_type, currency, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, raw_text, detected_type, currency, _now()))
        return cur.lastrowid


def get_latest_requisite(user_id: int) -> Optional[sqlite3.Row]:
    """Повертає останні реквізити юзера."""
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM requisites
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (user_id,)).fetchone()


# ==================== DEALS ====================

def create_deal(user_id: int, nft_lookup_id: Optional[int],
                requisite_id: Optional[int], buyout_ton: Optional[float],
                buyout_display: str, currency: str) -> int:
    """Створює нову угоду зі статусом pending. Повертає id."""
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO deals
                (user_id, nft_lookup_id, requisite_id,
                 buyout_ton, buyout_display, currency, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (user_id, nft_lookup_id, requisite_id,
              buyout_ton, buyout_display, currency, _now()))
        return cur.lastrowid


def mark_deal_paid(deal_id: int):
    """Переводить угоду у статус paid."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE deals SET status = 'paid', paid_at = ?
            WHERE id = ?
        """, (_now(), deal_id))


def get_user_deals(user_id: int) -> list:
    with get_conn() as conn:
        return conn.execute("""
            SELECT * FROM deals WHERE user_id = ?
            ORDER BY created_at DESC
        """, (user_id,)).fetchall()


# ==================== BALANCE EVENTS ====================

def deduct_balance(user_id: int, deal_id: int, amount_display: str, label: str = "Списання: NFT не отримано"):
    """Фіксує подію списання балансу (від'ємна сума)."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO balance_events (user_id, deal_id, amount_display, label, sent_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, deal_id, amount_display, label, _now()))


def log_balance_topup(user_id: int, deal_id: Optional[int],
                      amount_display: str, label: str = "Отримано за NFT"):
    """Фіксує подію поповнення балансу."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO balance_events (user_id, deal_id, amount_display, label, sent_at)
            VALUES (?, ?, ?, ?, ?)
        """, (user_id, deal_id, amount_display, label, _now()))


# ==================== АНАЛІТИКА / АДМІН ====================

def get_stats() -> dict:
    """Загальна статистика бота."""
    with get_conn() as conn:
        users_total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        lookups_total = conn.execute("SELECT COUNT(*) FROM nft_lookups").fetchone()[0]
        deals_total = conn.execute("SELECT COUNT(*) FROM deals").fetchone()[0]
        deals_paid = conn.execute(
            "SELECT COUNT(*) FROM deals WHERE status = 'paid'"
        ).fetchone()[0]
        today = datetime.utcnow().strftime('%Y-%m-%d')
        new_users_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE first_seen LIKE ?", (today + '%',)
        ).fetchone()[0]
    return {
        'users_total': users_total,
        'lookups_total': lookups_total,
        'deals_total': deals_total,
        'deals_paid': deals_paid,
        'new_users_today': new_users_today,
    }


# ==================== УТИЛІТИ ====================


def set_balance_frozen(user_id: int, frozen: bool):
    """Заморожує або розморожує баланс юзера."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET balance_frozen = ? WHERE user_id = ?",
            (1 if frozen else 0, user_id)
        )


def is_balance_frozen(user_id: int) -> bool:
    """Повертає True якщо баланс юзера заморожено."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT balance_frozen FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    return bool(row and row["balance_frozen"])

def _now() -> str:
    """Поточний UTC-час у форматі ISO 8601."""
    return datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
