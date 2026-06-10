# timer.py
import asyncio
from datetime import datetime, timedelta
import database as db

active_timers = {}

async def start_gift_timer(bot, user_id: int, deal_id: int, buyout_display: str, is_escrow: bool = False):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    # Одразу пишемо юзеру що треба відправити NFT
    await bot.send_message(
        chat_id=user_id,
        text=(
            f"📦 <b>Отправьте NFT подарок!</b>\n\n"
            f"У вас есть <b>10 минут</b> чтобы отправить подарок менеджеру.\n\n"
            f"💰 После получения NFT выплата <b>{buyout_display}</b> "
            f"будет отправлена в ближайшее время."
        ),
        parse_mode="HTML"
    )

    deadline = datetime.utcnow() + timedelta(minutes=10)

    while datetime.utcnow() < deadline:
        await asyncio.sleep(30)

        if is_escrow:
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT status FROM escrow_deals WHERE id = ?",
                    (deal_id,)
                ).fetchone()
            if row and row['status'] == 'done':
                active_timers.pop(deal_id, None)
                return
        else:
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT status FROM deals WHERE id = ?",
                    (deal_id,)
                ).fetchone()
            if row and row['status'] == 'gift_received':
                active_timers.pop(deal_id, None)
                return

    # Час вийшов
    active_timers.pop(deal_id, None)

    if is_escrow:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT status FROM escrow_deals WHERE id = ?",
                (deal_id,)
            ).fetchone()
        if row and row['status'] != 'done':
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE escrow_deals SET status = 'cancelled' WHERE id = ?",
                    (deal_id,)
                )
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⏰ <b>Время вышло!</b>\n\n"
                    "NFT не был переведён в течение 10 минут.\n"
                    "Сделка отменена. Если это ошибка — напишите в поддержку."
                ),
                parse_mode="HTML"
            )
    else:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT status, buyout_ton, buyout_display FROM deals WHERE id = ?",
                (deal_id,)
            ).fetchone()
        if row and row['status'] != 'gift_received':
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE deals SET status = 'cancelled' WHERE id = ?",
                    (deal_id,)
                )
            # Списуємо баланс — те що було зараховано
            buyout_ton = row['buyout_ton'] or 0
            buyout_display = row['buyout_display'] or buyout_display
            deduct_str = f"-{round(buyout_ton, 4)} TON" if buyout_ton else f"-{buyout_display}"
            db.deduct_balance(
                user_id=user_id,
                deal_id=deal_id,
                amount_display=deduct_str,
                label="Списання: NFT не отримано вчасно"
            )
            await bot.send_message(
                chat_id=user_id,
                text=(
                    "⏰ <b>Время вышло!</b>\n\n"
                    "Подарок не был получен в течение 10 минут.\n"
                    f"❌ С вашего баланса списано <b>{deduct_str}</b>.\n\n"
                    "Если это ошибка — напишите в поддержку."
                ),
                parse_mode="HTML"
            )


def launch_gift_timer(bot, user_id: int, deal_id: int, buyout_display: str, is_escrow: bool = False):
    task = asyncio.create_task(
        start_gift_timer(bot, user_id, deal_id, buyout_display, is_escrow)
    )
    active_timers[deal_id] = task
