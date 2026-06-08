# gift_checker.py
import database as db
from telethon import events
from telethon.tl.types import MessageActionStarGiftUnique

_telethon_client = None
_bot = None
ADMIN_ID = 7562324979

def set_telethon_client(client):
    global _telethon_client
    _telethon_client = client

    @client.on(events.Raw())
    async def gift_handler(update):
        try:
            from telethon.tl.types import UpdateNewMessage, MessageService
            if not isinstance(update, UpdateNewMessage):
                return
            msg = update.message
            if not isinstance(msg, MessageService):
                return
            if not isinstance(msg.action, MessageActionStarGiftUnique):
                return

            peer = msg.peer_id
            sender_id = getattr(peer, 'user_id', None)
            if not sender_id:
                return

            # Дістаємо інфо про подарунок
            gift = msg.action.gift
            gift_title = getattr(gift, 'title', 'Невідомий NFT')
            gift_slug = getattr(gift, 'slug', '')
            value_usd = getattr(gift, 'value_usd_amount', 0)
            value_rub = getattr(gift, 'value_amount', 0)
            value_currency = getattr(gift, 'value_currency', 'RUB')

            print(f"[gift_checker] NFT подарунок від {sender_id}: {gift_title}")

            with db.get_conn() as conn:
                deal = conn.execute(
                    "SELECT * FROM deals WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                    (sender_id,)
                ).fetchone()

            if deal:
                # Ставимо статус gift_received — таймер це побачить і зупиниться
                with db.get_conn() as conn:
                    conn.execute(
                        "UPDATE deals SET status = 'gift_received' WHERE id = ?",
                        (deal['id'],)
                    )
                print(f"[gift_checker] NFT отримано, угода #{deal['id']} gift_received!")

                if _bot:
                    # Пишемо юзеру
                    keyboard_review = [[
                        {"text": "⭐ Оставить отзыв", "url": "https://t.me/+tqkAlrl7H55iZjYy"}
                    ]]
                    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                    review_kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("⭐ Оставить отзыв", url="https://t.me/+tqkAlrl7H55iZjYy")
                    ]])
                    await _bot.send_message(
                        chat_id=sender_id,
                        text=f"✅ <b>Подарок получен!</b>\n\n"
                             f"🎁 <b>{gift_title}</b>\n"
                             f"💰 Выплата <b>{deal['buyout_display']}</b> будет отправлена в ближайшее время.\n\n"
                             f"Спасибо за сделку! Если остались довольны — оставьте отзыв 👇",
                        parse_mode="HTML",
                        reply_markup=review_kb
                    )

                    # Пишемо адміну
                    gift_link = f"https://t.me/nft/{gift_slug}" if gift_slug else "—"
                    await _bot.send_message(
                        chat_id=ADMIN_ID,
                        text=f"🎁 <b>Новый NFT получен!</b>\n\n"
                             f"👤 От: <code>{sender_id}</code>\n"
                             f"🏷 Название: <b>{gift_title}</b>\n"
                             f"💵 Цена: <b>${value_usd / 100:.2f}</b> / <b>{value_rub / 100:.0f} {value_currency}</b>\n"
                             f"💰 Выплата: <b>{deal['buyout_display']}</b>\n"
                             f"🔗 {gift_link}\n\n"
                             f"✅ Угода #{deal['id']} закрита.",
                        parse_mode="HTML"
                    )

            # ===== ESCROW — перевіряємо чи це NFT для escrow сделки =====
            if _bot:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                with db.get_conn() as conn:
                    escrow = conn.execute(
                        """SELECT * FROM escrow_deals
                           WHERE status = 'paid'
                           AND (creator_id = ? OR joiner_id = ?)
                           ORDER BY id DESC LIMIT 1""",
                        (sender_id, sender_id)
                    ).fetchone()

                if escrow:
                    seller_id = escrow['creator_id'] if escrow['role'] == 'seller' else escrow['joiner_id']
                    buyer_id = escrow['joiner_id'] if escrow['role'] == 'seller' else escrow['creator_id']

                    with db.get_conn() as conn:
                        conn.execute("UPDATE escrow_deals SET status = 'done' WHERE id = ?", (escrow['id'],))

                    # Знімаємо заморозку продавця
                    db.set_balance_frozen(seller_id, False)

                    review_kb = InlineKeyboardMarkup([[
                        InlineKeyboardButton("⭐ Оставить отзыв", url="https://t.me/+tqkAlrl7H55iZjYy")
                    ]])
                    deal_num = escrow['deal_number'] if escrow['deal_number'] else escrow['id']
                    await _bot.send_message(
                        chat_id=seller_id,
                        text=(
                            f"✅ <b>Сделка завершена #{deal_num}</b>\n\n"
                            f"🎁 <b>{escrow['gift_name']}</b>\n"
                            f"💰 <b>{escrow['amount_ton']} TON</b>\n\n"
                            f"Средства будут зачислены на ваш способ оплаты в течение 1–3 рабочих дней.\n\n"
                            f"Спасибо за сделку! Оставьте отзыв 👇"
                        ),
                        parse_mode="HTML",
                        reply_markup=review_kb
                    )

                    if buyer_id:
                        await _bot.send_message(
                            chat_id=buyer_id,
                            text=(
                                f"✅ <b>Сделка завершена #{deal_num}</b>\n\n"
                                f"🎁 <b>{escrow['gift_name']}</b>\n"
                                f"💰 <b>{escrow['amount_ton']} TON</b>\n\n"
                                f"NFT переведён вам. Спасибо за сделку!"
                            ),
                            parse_mode="HTML"
                        )

                    gift_link = f"https://t.me/nft/{gift_slug}" if gift_slug else "—"
                    await _bot.send_message(
                        chat_id=ADMIN_ID,
                        text=(
                            f"🔒 <b>Escrow сделка завершена #{deal_num}</b>\n\n"
                            f"🎁 NFT: <b>{gift_title}</b>\n"
                            f"💰 Сумма: <b>{escrow['amount_ton']} TON</b>\n"
                            f"👤 Продавець: <code>{seller_id}</code>\n"
                            f"👤 Покупець: <code>{buyer_id}</code>\n"
                            f"🔗 {gift_link}"
                        ),
                        parse_mode="HTML"
                    )
                    print(f"[gift_checker] Escrow #{escrow['id']} завершено!")

        except Exception as e:
            print(f"[gift_handler] Помилка: {e}")


def set_bot(bot):
    global _bot
    _bot = bot


async def check_gift_for_deal(deal_id: int) -> bool:
    with db.get_conn() as conn:
        deal = conn.execute(
            "SELECT * FROM deals WHERE id = ? AND status = 'paid'",
            (deal_id,)
        ).fetchone()
    return deal is not None
