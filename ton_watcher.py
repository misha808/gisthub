"""
ton_watcher.py — слухає вхідні TON-транзакції на адресу гаманця.
Кожні 30 секунд перевіряє нові перекази через tonapi.io.
Якщо в коментарі є memo юзера — зараховує суму на його баланс.
"""

import asyncio
import aiohttp
import database as db
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# ==================== НАЛАШТУВАННЯ ====================

TON_WALLET = "UQChbu2113zlcZ8H8DMOqafnWp-gnzRKDCaeqf18b3WmaLMh"
MINI_APP_URL = "https://gisthub-production.up.railway.app/"

# tonapi.io — безкоштовний публічний API
TONAPI_URL = f"https://tonapi.io/v2/blockchain/accounts/{TON_WALLET}/transactions"

POLL_INTERVAL = 30  # секунд між перевірками


# ==================== ГЕНЕРАЦІЯ MEMO ====================

def memo_to_user_id(memo: str, all_user_ids: list) -> int | None:
    try:
        memo_int = int(memo.strip())
    except:
        return None
    for uid in all_user_ids:
        if (uid % 900000) + 100000 == memo_int:
            return uid
    return None


def get_all_user_ids() -> list:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT user_id FROM users").fetchall()
    return [r["user_id"] for r in rows]


def already_processed(lt: int) -> bool:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM ton_deposits WHERE lt = ?", (lt,)
        ).fetchone()
    return row is not None


def mark_processed(lt: int, user_id: int, amount_ton: float, memo: str):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO ton_deposits (lt, user_id, amount_ton, memo, processed_at) VALUES (?, ?, ?, ?, ?)",
            (lt, user_id, amount_ton, memo, db._now())
        )


def init_deposits_table():
    with db.get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ton_deposits (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                lt           INTEGER UNIQUE NOT NULL,
                user_id      INTEGER NOT NULL,
                amount_ton   REAL NOT NULL,
                memo         TEXT,
                processed_at TEXT NOT NULL
            )
        """)


# ==================== ОСНОВНИЙ WATCHER ====================

async def fetch_transactions(session: aiohttp.ClientSession) -> list:
    try:
        async with session.get(
            TONAPI_URL,
            params={"limit": 20},
            headers={"Accept": "application/json"},
            timeout=aiohttp.ClientTimeout(total=10)
        ) as resp:
            data = await resp.json()

        return data.get("transactions", [])

    except Exception as e:
        print(f"[ton_watcher] Помилка запиту: {e}")
        return []


async def process_transactions(txs: list, bot) -> int:
    if not txs:
        return 0

    all_user_ids = get_all_user_ids()
    count = 0

    for tx in txs:
        try:
            lt = tx.get("lt")
            if not lt:
                continue
            lt = int(lt)

            if already_processed(lt):
                continue

            # Тільки вхідні транзакції
            in_msg = tx.get("in_msg")
            if not in_msg:
                continue

            # Перевіряємо що це переказ на наш гаманець (не outgoing)
            if tx.get("account", {}).get("address", "") == "":
                continue

            # Сума в нанотонах
            value_nano = int(in_msg.get("value", 0))
            if value_nano <= 0:
                continue
            amount_ton = round(value_nano / 1_000_000_000, 4)

            # Коментар (memo)
            decoded = in_msg.get("decoded_body") or {}
            memo = decoded.get("text", "").strip()
            if not memo:
                # спробуємо raw_body
                memo = in_msg.get("raw_body", "")[:20].strip()

            if not memo:
                print(f"[ton_watcher] lt={lt} без memo — {amount_ton} TON — ігноруємо")
                mark_processed(lt, 0, amount_ton, "")
                continue

            user_id = memo_to_user_id(memo, all_user_ids)
            if not user_id:
                print(f"[ton_watcher] Memo '{memo}' не знайдено — ігноруємо")
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
    init_deposits_table()
    print("[ton_watcher] Запущено ✅ — перевірка кожні 30 сек")

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                txs = await fetch_transactions(session)
                print(f"[ton_watcher] Отримано транзакцій: {len(txs)}")
                new = await process_transactions(txs, bot)
                if new:
                    print(f"[ton_watcher] Оброблено нових поповнень: {new}")
            except Exception as e:
                print(f"[ton_watcher] Критична помилка: {e}")

            await asyncio.sleep(POLL_INTERVAL)
