"""
ton_watcher.py — слухає вхідні TON-транзакції на адресу гаманця.
Кожні 30 секунд перевіряє нові перекази через TON Center API.
Якщо в коментарі є memo юзера — зараховує суму на його баланс.
"""

import asyncio
import aiohttp
import database as db
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ==================== НАЛАШТУВАННЯ ====================

TON_WALLET = "UQChbu2113zlcZ8H8DMOqafnWp-gnzRKDCaeqf18b3WmaLMh"
MINI_APP_URL = "https://gisthub-production.up.railway.app/"

# TON Center API — безкоштовний, без ключа (є ліміт ~1 req/sec)
TONCENTER_API = "https://toncenter.com/api/v2/getTransactions"

# Якщо є API ключ від toncenter.com (безкоштовно на сайті) — вкажи тут,
# щоб збільшити ліміт запитів. Якщо немає — залиш пустим рядком.
TONCENTER_KEY = ""

POLL_INTERVAL = 30  # секунд між перевірками


# ==================== ГЕНЕРАЦІЯ MEMO ====================

def memo_to_user_id(memo: str, all_user_ids: list[int]) -> int | None:
    """
    Перевіряє чи memo відповідає комусь із юзерів.
    Memo генерується як (user_id % 900000) + 100000 — дивись miniapp.
    """
    try:
        memo_int = int(memo.strip())
    except:
        return None

    for uid in all_user_ids:
        if (uid % 900000) + 100000 == memo_int:
            return uid
    return None


def get_all_user_ids() -> list[int]:
    """Повертає список всіх user_id з бази."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]


def already_processed(lt: int) -> bool:
    """Перевіряє чи транзакція вже оброблена (по lt — logical time)."""
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM ton_deposits WHERE lt = ?", (lt,)
        ).fetchone()
    return row is not None


def mark_processed(lt: int, user_id: int, amount_ton: float, memo: str):
    """Зберігає оброблену транзакцію щоб не зарахувати двічі."""
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ton_deposits (lt, user_id, amount_ton, memo, processed_at) VALUES (?, ?, ?, ?, ?)",
            (lt, user_id, amount_ton, memo, db._now())
        )


def init_deposits_table():
    """Створює таблицю для зберігання оброблених депозитів."""
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ton_deposits (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                lt           INTEGER UNIQUE NOT NULL,  -- logical time транзакції (унікальний ID)
                user_id      INTEGER NOT NULL,
                amount_ton   REAL NOT NULL,
                memo         TEXT,
                processed_at TEXT NOT NULL
            )
        """)


# ==================== ОСНОВНИЙ WATCHER ====================

async def fetch_transactions(session: aiohttp.ClientSession) -> list:
    """Отримує останні 20 вхідних транзакцій на гаманець."""
    params = {
        "address": TON_WALLET,
        "limit": 20,
    }
    headers = {}
    if TONCENTER_KEY:
        headers["X-API-Key"] = TONCENTER_KEY

    try:
        async with session.get(
            TONCENTER_API,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()

        if not data.get("ok"):
            print(f"[ton_watcher] API помилка: {data}")
            return []

        return data.get("result", [])

    except Exception as e:
        print(f"[ton_watcher] Помилка запиту: {e}")
        return []


async def process_transactions(txs: list, bot) -> int:
    """Обробляє транзакції, повертає кількість нових поповнень."""
    if not txs:
        return 0

    all_user_ids = get_all_user_ids()
    count = 0

    for tx in txs:
        try:
            # Беремо тільки вхідні повідомлення (in_msg)
            in_msg = tx.get("in_msg")
            if not in_msg:
                continue

            # Перевіряємо що це переказ на наш гаманець
            dest = in_msg.get("destination", "")
            if dest != TON_WALLET:
                continue

            # Logical time — унікальний ID транзакції
            lt = tx.get("transaction_id", {}).get("lt")
            if not lt:
                continue
            lt = int(lt)

            # Вже оброблено?
            if already_processed(lt):
                continue

            # Сума в TON (приходить в нанотонах)
            value_nano = int(in_msg.get("value", 0))
            if value_nano <= 0:
                continue
            amount_ton = round(value_nano / 1_000_000_000, 4)

            # Коментар (memo)
            memo = in_msg.get("message", "").strip()
            if not memo:
                print(f"[ton_watcher] Транзакція {lt} без memo — {amount_ton} TON — ігноруємо")
                mark_processed(lt, 0, amount_ton, "")  # щоб не спамив в лог
                continue

            # Знаходимо юзера по memo
            user_id = memo_to_user_id(memo, all_user_ids)
            if not user_id:
                print(f"[ton_watcher] Memo '{memo}' не відповідає жодному юзеру — ігноруємо")
                mark_processed(lt, 0, amount_ton, memo)
                continue

            # Зараховуємо баланс
            display = f"+{amount_ton} TON"
            deal_id = db.create_deal(
                user_id=user_id,
                nft_lookup_id=None,
                requisite_id=None,
                buyout_ton=amount_ton,
                buyout_display=display,
                currency="TON",
            )
            db.mark_deal_paid(deal_id)
            db.log_balance_topup(user_id=user_id, deal_id=deal_id, amount_display=display)
            mark_processed(lt, user_id, amount_ton, memo)

            print(f"[ton_watcher] ✅ Зараховано {amount_ton} TON → юзер {user_id} (memo: {memo})")

            # Повідомляємо юзера в бот
            try:
                keyboard = [[InlineKeyboardButton("💼 Открыть кошелёк", web_app={"url": MINI_APP_URL})]]
                await bot.send_message(
                    chat_id=user_id,
                    text=(
                        f"✅ <b>Баланс пополнен!</b>\n\n"
                        f"➕ <b>{amount_ton} TON</b>\n\n"
                        f"Нажмите кнопку ниже, чтобы открыть кошелёк 👇"
                    ),
                    parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            except Exception as e:
                print(f"[ton_watcher] Не вдалось повідомити юзера {user_id}: {e}")

            count += 1

        except Exception as e:
            print(f"[ton_watcher] Помилка обробки транзакції: {e}")

    return count


async def start_ton_watcher(bot):
    """
    Головний цикл — запускається один раз в фоні разом з ботом.
    Виклик: asyncio.create_task(start_ton_watcher(app.bot))
    """
    init_deposits_table()
    print("[ton_watcher] Запущено ✅ — перевірка кожні 30 сек")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                txs = await fetch_transactions(session)
                new = await process_transactions(txs, bot)
                if new:
                    print(f"[ton_watcher] Оброблено нових поповнень: {new}")
            except Exception as e:
                print(f"[ton_watcher] Критична помилка: {e}")

            await asyncio.sleep(POLL_INTERVAL)
