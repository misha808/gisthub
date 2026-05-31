import re
import asyncio
import aiohttp
from telethon import TelegramClient
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)
from timer import launch_gift_timer
from gift_checker import check_gift_for_deal
import database as db

# ==================== НАЛАШТУВАННЯ ====================
BOT_TOKEN = "8858984918:AAHMwoQRpiLhxhCloq9_HvI26pL9Dfuq2Os"
API_ID = 36954581
API_HASH = "fa0d629367552da18ec8db6430f2a620"
PRICE_BOT = "PriceNFTbot"

MARKET_MARKUP = 1.70
BUYOUT_PERCENT = 0.95

STARS_PER_RUB = 1.5  # 1 зірка = 1.5 руб

WELCOME_TEXT = '''Привет <tg-emoji emoji-id="5440431182602842059">👋</tg-emoji>
Добро пожаловать в HubGift— надёжного бота для безопасных сделок с NFT подарками.
Немного о нашем боте:
<tg-emoji emoji-id="5296369303661067030">🔒</tg-emoji> Гарант сделок между покупателем и продавцом
<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji> Автоскуп NFT подарков
<tg-emoji emoji-id="5249381781622247862">⚡</tg-emoji> Быстрый и удобный сервис
<tg-emoji emoji-id="5373174941095050893">💸</tg-emoji> Комиссия — 10%
Выберите нужный раздел ниже <tg-emoji emoji-id="5231102735817918643">👇</tg-emoji>'''

PHOTO_URL = "https://i.ibb.co/hFppHpVp/IMG-20260524-130341-793.jpg"

telethon_client: TelegramClient = None

# ==================== КУРС TON ====================

async def get_ton_rates() -> dict:
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.coingecko.com/api/v3/simple/price",
                params={
                    "ids": "the-open-network",
                    "vs_currencies": "usd,uah,rub,kzt,eur,byn,uzs,azn,amd,gel"
                },
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                data = await resp.json()
        rates = data.get("the-open-network", {})
        return {
            "usd": rates.get("usd", 0),
            "uah": rates.get("uah", 0),
            "rub": rates.get("rub", 0),
            "kzt": rates.get("kzt", 0),
            "eur": rates.get("eur", 0),
            "byn": rates.get("byn", 0),
            "uzs": rates.get("uzs", 0),
            "azn": rates.get("azn", 0),
            "amd": rates.get("amd", 0),
            "gel": rates.get("gel", 0),
        }
    except Exception as e:
        print(f"[get_ton_rates] Помилка: {e}")
        return {}


def format_price(ton: float, rates: dict) -> str:
    lines = [f"💎 {ton} TON"]
    if rates.get("usd"):
        lines.append(f"💵 ${round(ton * rates['usd'], 2)}")
    if rates.get("uah"):
        lines.append(f"🇺🇦 {round(ton * rates['uah'])} грн")
    if rates.get("rub"):
        lines.append(f"🇷🇺 {round(ton * rates['rub'])} руб")
    if rates.get("kzt"):
        lines.append(f"🇰🇿 {round(ton * rates['kzt'])} тенге")
    if rates.get("eur"):
        lines.append(f"💶 €{round(ton * rates['eur'], 2)}")
    return " | ".join(lines)


# ==================== ПАРСИНГ NFT ====================

async def get_price_from_pricebot(slug: str) -> dict:
    try:
        link = f"t.me/nft/{slug}"
        await telethon_client.send_message(PRICE_BOT, link)
        await asyncio.sleep(2)

        msgs = await telethon_client.get_messages(PRICE_BOT, limit=3)
        for msg in msgs:
            if msg.buttons:
                for row in msg.buttons:
                    for btn in row:
                        btn_text = btn.text.lower()
                        if any(x in btn_text for x in ['информация', 'подарк', 'gift info', 'information']):
                            await btn.click()
                            await asyncio.sleep(4)
                            break

        msgs = await telethon_client.get_messages(PRICE_BOT, limit=1)
        if not msgs:
            return {}

        text = msgs[0].text or ""
        print(f"[PriceBot]: {text[:300]}")

        clean = re.sub(r'\*\*', '', text)
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)

        result = {}

        floor_match = re.search(r'Floor:\s*([\d.]+)', clean)
        if floor_match:
            result['floor_ton'] = float(floor_match.group(1))

        avg_match = re.search(r'AVG:\s*([\d.]+)', clean)
        if avg_match:
            result['avg_ton'] = float(avg_match.group(1))

        last_match = re.search(r'Последняя продажа:\s*([\d.]+)', clean)
        if last_match:
            result['last_ton'] = float(last_match.group(1))

        return result

    except Exception as e:
        print(f"[get_price] Помилка: {e}")
        return {}


async def get_attrs_from_telegram(slug: str) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"https://t.me/nft/{slug}",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                html = await resp.text()

        result = {}
        title_match = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if title_match:
            result['title'] = title_match.group(1).strip()

        desc_match = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if desc_match:
            desc = desc_match.group(1).strip()
            attrs = {}
            for line in desc.splitlines():
                line = line.strip()
                if ':' in line:
                    key, _, val = line.partition(':')
                    attrs[key.strip()] = val.strip()
            if attrs:
                result['attrs'] = attrs

        return result
    except Exception as e:
        print(f"[get_attrs] Помилка: {e}")
        return {}


async def parse_nft(link: str) -> dict | None:
    slug_match = re.search(r't\.me/nft/([\w-]+)', link, re.IGNORECASE)
    if not slug_match:
        return None
    slug = slug_match.group(1)

    attrs_task = asyncio.create_task(get_attrs_from_telegram(slug))
    price_task = asyncio.create_task(get_price_from_pricebot(slug))
    rates_task = asyncio.create_task(get_ton_rates())

    attrs_data = await attrs_task
    price_data = await price_task
    rates = await rates_task

    if not attrs_data.get('title'):
        return None

    return {
        'slug': slug,
        'title': attrs_data.get('title', slug),
        'attrs': attrs_data.get('attrs', {}),
        'floor_ton': price_data.get('floor_ton'),
        'avg_ton': price_data.get('avg_ton'),
        'last_ton': price_data.get('last_ton'),
        'rates': rates,
    }


def format_nft_message(data: dict, link: str) -> str:
    lines = []
    lines.append(f"🎁 *{data['title']}*\n")

    if data.get('attrs'):
        icons = {'model': '📦', 'backdrop': '🎨', 'symbol': '🔷', 'pattern': '🌀'}
        for k, v in data['attrs'].items():
            icon = icons.get(k.lower(), '▪️')
            lines.append(f"{icon} {k}: `{v}`")

    rates = data.get('rates', {})
    floor = data.get('floor_ton')
    avg = data.get('avg_ton')
    last = data.get('last_ton')

    if floor:
        floor_up = round(floor * MARKET_MARKUP, 2)
        avg_up = round(avg * MARKET_MARKUP, 2) if avg else None
        last_up = round(last * MARKET_MARKUP, 2) if last else None

        lines.append("\n📊 *Рыночные данные:*")
        lines.append(f"  📉 Floor: {format_price(floor_up, rates)}")
        if avg_up:
            lines.append(f"  📈 AVG: {format_price(avg_up, rates)}")
        if last_up:
            lines.append(f"  🔄 Последняя продажа: {format_price(last_up, rates)}")

        buyout = round(floor_up * BUYOUT_PERCENT, 2)
        lines.append(f"\n🛒 *Наше предложение выкупа:*")
        lines.append(f"  {format_price(buyout, rates)}")
    else:
        lines.append("\n💰 _Цена не найдена — уточните у менеджера_")

    lines.append(f"\n🔗 [Посмотреть NFT]({link})")
    return '\n'.join(lines)


# ==================== ВИЗНАЧЕННЯ СПОСОБУ ВИПЛАТИ ====================

def detect_payout(text: str) -> tuple[str, str]:
    """Повертає (опис, валюта)"""
    text = text.strip()
    digits_only = re.sub(r'\D', '', text)

    # Зірки
    if re.match(r'^\d+\s*[⭐*зв]', text, re.IGNORECASE) or text.lower() in ('звезды', 'зірки', 'stars', '⭐'):
        return ("⭐ *Telegram Stars*\nВыплата звёздами на ваш аккаунт.", 'STARS')

    # Крипта
    if re.match(r'^(UQ|EQ)[A-Za-z0-9_-]{46}$', text):
        return ("💎 *Криптовалюта — TON*\nАдрес TON кошелька принят.", 'TON')
    if re.match(r'^0x[0-9a-fA-F]{40}$', text):
        return ("💎 *Криптовалюта — USDT/ETH (ERC-20)*\nАдрес принят.", 'USDT')
    if re.match(r'^T[1-9A-HJ-NP-Za-km-z]{33}$', text):
        return ("💎 *Криптовалюта — USDT (TRC-20)*\nАдрес принят.", 'USDT')
    if re.match(r'^(1|3)[1-9A-HJ-NP-Za-km-z]{25,34}$', text) or re.match(r'^bc1[a-z0-9]{39,59}$', text):
        return ("💎 *Криптовалюта — Bitcoin (BTC)*\nАдрес принят.", 'BTC')

    # Карти
    if re.match(r'^[\d\s\-]{16,23}$', text) and len(digits_only) == 16:
        first4 = digits_only[:4]
        first6 = digits_only[:6]

        # Україна
        mono_bins = ('5375', '4149', '5168', '4731', '5355', '4441', '5209')
        privat_bins = ('4405', '5169', '4276', '4627', '4246', '5363')
        oschadbank_bins = ('4058', '4731', '6762')
        pumb_bins = ('5168', '4552')
        ukrsib_bins = ('4650', '5229')

        if first4 in mono_bins:
            return ("🇺🇦 *Monobank (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in privat_bins:
            return ("🇺🇦 *ПриватБанк (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in oschadbank_bins:
            return ("🇺🇦 *Ощадбанк (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in pumb_bins:
            return ("🇺🇦 *ПУМБ (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in ukrsib_bins:
            return ("🇺🇦 *УкрСибБанк (Украина)*\nНомер карты принят.", 'UAH')

        # Росія
        tbank_bins = ('5213', '4377', '5484', '5189', '4592', '5296', '2200', '2201', '2202', '2203')
        sber_bins = ('4276', '4279', '4272', '5469', '5336')
        vtb_bins = ('4272', '4455', '5157')
        alfa_bins = ('5561', '4154', '5594')
        raif_bins = ('5100', '5101', '4960')

        if first4 in tbank_bins or first6[:4] in tbank_bins:
            return ("🇷🇺 *Т-Банк / Tinkoff (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in sber_bins:
            return ("🇷🇺 *Сбербанк (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in vtb_bins:
            return ("🇷🇺 *ВТБ (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in alfa_bins:
            return ("🇷🇺 *Альфа-Банк (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in raif_bins:
            return ("🇷🇺 *Райффайзен (Россия)*\nНомер карты принят.", 'RUB')

        # Казахстан
        kaspi_bins = ('4400', '5169', '5170', '4149')
        halyk_bins = ('4405', '5562', '5478')
        if first4 in kaspi_bins:
            return ("🇰🇿 *Kaspi Bank (Казахстан)*\nНомер карты принят.", 'KZT')
        if first4 in halyk_bins:
            return ("🇰🇿 *Halyk Bank (Казахстан)*\nНомер карты принят.", 'KZT')

        # Загальне
        if digits_only[0] in ('4', '5'):
            return ("🏦 *Банковская карта*\nНомер принят. Менеджер уточнит банк.", 'USD')
        return ("🏦 *Банковская карта*\nНомер принят. Менеджер уточнит детали.", 'USD')

    # Телефон
    phone_match = re.match(r'^(\+?[\d\s\-()]{10,15})$', text)
    if phone_match and 10 <= len(digits_only) <= 13:
        if digits_only.startswith('380') or digits_only.startswith('80'):
            return ("🇺🇦 *Monobank / ПриватБанк (Украина)*\nВыплата по номеру телефона.", 'UAH')
        if digits_only.startswith('7') or (digits_only.startswith('8') and len(digits_only) == 11):
            return ("🇷🇺 *Банк (Россия)*\nВыплата через СБП по номеру телефона.", 'RUB')
        if digits_only.startswith('375'):
            return ("🇧🇾 *Банк (Беларусь)*\nВыплата по номеру телефона.", 'BYN')
        if digits_only.startswith('77') or digits_only.startswith('76'):
            return ("🇰🇿 *Банк (Казахстан)*\nВыплата по номеру телефона.", 'KZT')
        if digits_only.startswith('998'):
            return ("🇺🇿 *Банк (Узбекистан)*\nВыплата по номеру телефона.", 'UZS')
        if digits_only.startswith('994'):
            return ("🇦🇿 *Банк (Азербайджан)*\nВыплата по номеру телефона.", 'AZN')
        if digits_only.startswith('374'):
            return ("🇦🇲 *Банк (Армения)*\nВыплата по номеру телефона.", 'AMD')
        if digits_only.startswith('995'):
            return ("🇬🇪 *Банк (Грузия)*\nВыплата по номеру телефона.", 'GEL')
        return ("📱 *Номер телефона принят*\nМенеджер уточнит способ выплаты.", 'USD')

    return ("❓ *Не удалось определить способ выплаты*\nПожалуйста, отправьте:\n— Номер карты (16 цифр)\n— Крипто-адрес (TON, USDT, BTC)\n— Номер телефона\n— «звезды» для выплаты звёздами", 'USD')


# ==================== ХЕНДЛЕРИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
        language_code=user.language_code,
    )
    keyboard = [
        [InlineKeyboardButton("Безопасная сделка", callback_data="btn3")],
        [InlineKeyboardButton("Скуп NFT-подарков 🎁", callback_data="btn4")],
        [InlineKeyboardButton("Отзывы", url="https://t.me/+tqkAlrl7H55iZjYy")],
        [InlineKeyboardButton("Инструкция", url="https://telegra.ph/Instrukciya-05-24-28")],
    ]
    await update.message.reply_photo(
        photo=PHOTO_URL,
        caption=WELCOME_TEXT,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "btn3":
        await query.message.reply_text(
            "🔒 *Безопасная сделка*\n\n"
            "Мы выступаем гарантом между покупателем и продавцом.\n"
            "Комиссия — 10%.\n\n"
            "Напишите менеджеру для деталей 👇",
            parse_mode="Markdown"
        )

    elif query.data == "btn4":
        context.user_data['waiting_nft_link'] = True
        context.user_data['waiting_payout'] = False
        await query.message.reply_text(
            "🎁 *Скуп NFT-подарков*\n\n"
            "Отправьте ссылку на ваш NFT в формате:\n"
            "`t.me/nft/НазваПодарка-Номер`\n\n"
            "Например:\n"
            "`t.me/nft/HappyBrownie-90097`\n\n"
            "⏳ Я оценю подарок и предложу цену выкупа!",
            parse_mode="Markdown"
        )

    elif query.data == "sell_nft":
        context.user_data['waiting_payout'] = True
        context.user_data['waiting_nft_link'] = False
        await query.message.reply_text(
            "💳 *Выберите способ выплаты*\n\n"
            "🏦 *Банковские карты:*\n"
            "  🇺🇦 Monobank, ПриватБанк, Ощадбанк, ПУМБ (Украина)\n"
            "  🇷🇺 Т-Банк, Сбербанк, ВТБ, Альфа-Банк (Россия)\n"
            "  🇧🇾 Беларусбанк (Беларусь)\n"
            "  🇰🇿 Kaspi, Halyk Bank (Казахстан)\n"
            "  🇺🇿 Банки Узбекистана\n"
            "  🇦🇿 Банки Азербайджана\n"
            "  🇦🇲 Банки Армении\n"
            "  🇬🇪 Банки Грузии\n\n"
            "💎 *Криптовалюта:*\n"
            "  TON, USDT (TRC-20 / ERC-20), BTC\n\n"
            "⭐ *Telegram Stars:*\n"
            "  Напишите «звезды» — выплата по курсу 1⭐ = 1.5 руб\n\n"
            "📝 *Примеры:*\n"
            "  Номер карты: `4441 1144 1234 5678`\n"
            "  Крипто-адрес: `UQB...` или `T...`\n"
            "  Телефон: `+380XXXXXXXXX`\n"
            "  Звёзды: напишите `звезды`\n\n"
            "⬇️ Введите способ выплаты:",
            parse_mode="Markdown"
        )

    elif query.data == "stars_own":
        context.user_data['stars_target'] = 'own'
        await _finalize_stars(query, context)

    elif query.data == "stars_other":
        context.user_data['stars_target'] = 'other'
        await _finalize_stars(query, context)


async def _finalize_stars(query, context):
    buyout_ton = context.user_data.get('buyout_ton', 0)
    rates = context.user_data.get('nft_rates', {})
    rub = rates.get('rub', 0)
    buyout_rub = round(buyout_ton * rub)
    stars_count = round(buyout_rub / STARS_PER_RUB)
    target = "на ваш аккаунт" if context.user_data.get('stars_target') == 'own' else "на другой аккаунт"

    context.user_data['req_currency'] = 'STARS'
    context.user_data['waiting_payout'] = False

    buyout_str = f"{stars_count} ⭐"

    req_id = db.save_requisite(
        user_id=query.from_user.id,
        raw_text=f"stars:{target}",
        detected_type=f"⭐ Telegram Stars ({target})",
        currency='STARS',
    )
    context.user_data['req_db_id'] = req_id

    keyboard = [[
        InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_req"),
        InlineKeyboardButton("✏️ Изменить", callback_data="sell_nft"),
    ]]
    await query.message.reply_text(
        f"⭐ *Способ выплаты: Telegram Stars*\n\n"
        f"Выплата: *{buyout_str}* {target}\n"
        f"Курс: 1⭐ = {STARS_PER_RUB} руб\n\n"
        f"Подтвердите выплату 👇",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    db.touch_user(update.effective_user.id)

    if context.user_data.get('waiting_payout'):
        # Перевіряємо чи це зірки
        if re.search(r'звезд|зірк|stars|⭐', text, re.IGNORECASE):
            context.user_data['waiting_payout'] = False
            buyout_ton = context.user_data.get('buyout_ton', 0)
            rates = context.user_data.get('nft_rates', {})
            rub = rates.get('rub', 0)
            buyout_rub = round(buyout_ton * rub)
            stars_count = round(buyout_rub / STARS_PER_RUB)
            keyboard = [[
                InlineKeyboardButton("На свой аккаунт", callback_data="stars_own"),
                InlineKeyboardButton("На другой аккаунт", callback_data="stars_other"),
            ]]
            await update.message.reply_text(
                f"⭐ *Telegram Stars*\n\n"
                f"Сумма выплаты: *{stars_count} ⭐*\n"
                f"(курс: 1⭐ = {STARS_PER_RUB} руб)\n\n"
                f"Куда отправить звёзды?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        result, currency = detect_payout(text)
        context.user_data['waiting_payout'] = False
        context.user_data['req_currency'] = currency

        req_id = db.save_requisite(
            user_id=update.effective_user.id,
            raw_text=text,
            detected_type=result,
            currency=currency,
        )
        context.user_data['req_db_id'] = req_id
        keyboard = [[
            InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_req"),
            InlineKeyboardButton("✏️ Изменить", callback_data="sell_nft"),
        ]]
        await update.message.reply_text(
            f"✅ *Способ выплаты принят:*\n`{text}`\n\n{result}\n\n"
            "Менеджер свяжется с вами для завершения сделки 🤝",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if not context.user_data.get('waiting_nft_link'):
        return

    nft_pattern = re.compile(r'(https?://)?t\.me/nft/[\w-]+', re.IGNORECASE)
    if not nft_pattern.search(text):
        await update.message.reply_text(
            "❌ Не похоже на NFT-ссылку.\n\n"
            "Нужен формат: `t.me/nft/НазваПодарка-Номер`",
            parse_mode="Markdown"
        )
        return

    link = text if text.startswith('http') else f"https://{text}"
    wait_msg = await update.message.reply_text("⏳ Анализирую NFT, подождите ~5 секунд...")

    data = await parse_nft(link)
    await wait_msg.delete()

    if not data:
        await update.message.reply_text(
            "😔 Не удалось получить данные.\n"
            "Проверьте ссылку и попробуйте снова."
        )
        return

    msg = format_nft_message(data, link)
    if data.get('floor_ton'):
        floor_up = round(data['floor_ton'] * MARKET_MARKUP, 2)
        context.user_data['buyout_ton'] = round(floor_up * BUYOUT_PERCENT, 2)
        context.user_data['nft_rates'] = data.get('rates', {})
    lookup_id = db.save_nft_lookup(
        user_id=update.effective_user.id,
        data=data,
        link=link,
        market_markup=MARKET_MARKUP,
        buyout_percent=BUYOUT_PERCENT,
    )
    context.user_data['nft_lookup_db_id'] = lookup_id
    keyboard = [[
        InlineKeyboardButton("💰 Продать NFT", callback_data="sell_nft"),
        InlineKeyboardButton("🔄 Оценить другой", callback_data="btn4"),
    ]]

    await update.message.reply_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
        disable_web_page_preview=True
    )
    context.user_data['waiting_nft_link'] = False


# ==================== ПІДТВЕРДЖЕННЯ ====================

def get_buyout_in_currency(buyout_ton: float, rates: dict, currency: str) -> str:
    if currency == 'TON':
        return f"{buyout_ton} TON"
    elif currency == 'USDT':
        usd = rates.get('usd', 0)
        return f"{round(buyout_ton * usd, 2)} USDT"
    elif currency == 'BTC':
        usd = rates.get('usd', 0)
        return f"{round(buyout_ton * usd / 105000, 6)} BTC"
    elif currency == 'UAH':
        uah = rates.get('uah', 0)
        return f"{round(buyout_ton * uah)} грн"
    elif currency == 'RUB':
        rub = rates.get('rub', 0)
        return f"{round(buyout_ton * rub)} руб"
    elif currency == 'KZT':
        kzt = rates.get('kzt', 0)
        return f"{round(buyout_ton * kzt)} тенге"
    elif currency == 'BYN':
        byn = rates.get('byn', 0)
        return f"{round(buyout_ton * byn, 2)} BYN"
    elif currency == 'UZS':
        uzs = rates.get('uzs', 0)
        return f"{round(buyout_ton * uzs)} сум"
    elif currency == 'AZN':
        azn = rates.get('azn', 0)
        return f"{round(buyout_ton * azn, 2)} AZN"
    elif currency == 'AMD':
        amd = rates.get('amd', 0)
        return f"{round(buyout_ton * amd)} AMD"
    elif currency == 'GEL':
        gel = rates.get('gel', 0)
        return f"{round(buyout_ton * gel, 2)} GEL"
    elif currency == 'STARS':
        rub = rates.get('rub', 0)
        stars = round((buyout_ton * rub) / STARS_PER_RUB)
        return f"{stars} ⭐"
    else:
        usd = rates.get('usd', 0)
        return f"{round(buyout_ton * usd, 2)} USD"


def get_zero_balance(currency: str) -> str:
    symbols = {
        'TON': '💎 0 TON', 'USDT': '💵 0 USDT', 'BTC': '₿ 0 BTC',
        'UAH': '🇺🇦 0 грн', 'RUB': '🇷🇺 0 руб', 'KZT': '🇰🇿 0 тенге',
        'BYN': '🇧🇾 0 BYN', 'UZS': '🇺🇿 0 сум', 'AZN': '🇦🇿 0 AZN',
        'AMD': '🇦🇲 0 AMD', 'GEL': '🇬🇪 0 GEL', 'STARS': '⭐ 0 Stars',
        'USD': '💵 0 USD',
    }
    return symbols.get(currency, '0')


async def confirm_requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_req":
        currency = context.user_data.get('req_currency', 'USD')
        buyout_ton = context.user_data.get('buyout_ton')
        rates = context.user_data.get('nft_rates', {})

        zero_balance = get_zero_balance(currency)

        await query.message.reply_text(
            f"✅ *Способ выплаты подтверждён!*\n\n"
            f"💼 *Ваш внутренний баланс:*\n"
            f"{zero_balance}\n\n"
            f"⏳ Ожидайте пополнение баланса...",
            parse_mode="Markdown"
        )

        if buyout_ton and rates:
            buyout_str = get_buyout_in_currency(buyout_ton, rates, currency)
            deal_id = db.create_deal(
                user_id=query.from_user.id,
                nft_lookup_id=context.user_data.get('nft_lookup_db_id'),
                requisite_id=context.user_data.get('req_db_id'),
                buyout_ton=buyout_ton,
                buyout_display=buyout_str,
                currency=currency,
            )
            context.user_data['current_deal_id'] = deal_id
            launch_gift_timer(
                bot=context.bot,
                user_id=query.from_user.id,
                deal_id=deal_id,
                buyout_display=buyout_str,
            )
            await query.message.reply_text(
                "⏳ *У вас есть 10 минут чтобы отправить NFT подарок!*\n\n"
                "Как только получим — сразу уведомим.",
                parse_mode="Markdown"
            )


# ==================== АДМІН ====================

async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = db.get_stats()
    await update.message.reply_text(
        f"📊 *Статистика HubGift*\n\n"
        f"👥 Юзерів всього: {stats['users_total']}\n"
        f"🆕 Нових сьогодні: {stats['new_users_today']}\n"
        f"🔍 Оцінок NFT: {stats['lookups_total']}\n"
        f"🤝 Угод всього: {stats['deals_total']}\n"
        f"✅ Виплачено: {stats['deals_paid']}",
        parse_mode="Markdown"
    )


# ==================== ЗАПУСК ====================

async def main():
    global telethon_client

    db.init_db()
    print("База даних ініціалізована ✅")

    telethon_client = TelegramClient('auth', API_ID, API_HASH)
    await telethon_client.start()
    print("Telethon підключено ✅")

    from gift_checker import set_telethon_client, set_bot
    set_telethon_client(telethon_client)

    app = ApplicationBuilder().token(BOT_TOKEN).build()
    set_bot(app.bot)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CallbackQueryHandler(confirm_requisites, pattern="^confirm_req$"))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("Бот запущений 🚀")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
 