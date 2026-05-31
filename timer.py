# timer.py
import asyncio
from datetime import datetime, timedelta
import database as db
from gift_checker import check_gift_for_deal

active_timers = {}

async def start_gift_timer(bot, user_id: int, deal_id: int, buyout_display: str):
    deadline = datetime.utcnow() + timedelta(minutes=10)

    while datetime.utcnow() < deadline:
        await asyncio.sleep(30)

        result = await check_gift_for_deal(deal_id)

        if result:
            await bot.send_message(
                chat_id=user_id,
                text=f"✅ *Подарок получен!*\n\n"
                     f"💰 Выплата {buyout_display} будет отправлена в ближайшее время.",
                parse_mode="Markdown"
            )
            active_timers.pop(deal_id, None)
            return

    active_timers.pop(deal_id, None)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE deals SET status = 'cancelled' WHERE id = ?",
            (deal_id,)
        )
    await bot.send_message(
        chat_id=user_id,
        text="⏰ *Время вышло!*\n\n"
             "Подарок не был получен в течение 10 минут.\n"
             "Сделка отменена. Если это ошибка — напишите в поддержку.",
        parse_mode="Markdown"
    )

def launch_gift_timer(bot, user_id: int, deal_id: int, buyout_display: str):
    task = asyncio.create_task(
        start_gift_timer(bot, user_id, deal_id, buyout_display)
    )
    active_timers[deal_id] = task