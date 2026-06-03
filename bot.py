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
BOT_TOKEN = "8609277263:AAGk1cmnfBTxF98neiDcbP80B6Yc88hkGo0"
API_ID = 34537170
API_HASH = "41711038f3c2952df1b3610b1b47443b"
PRICE_BOT = "PriceNFTbot"
MINI_APP_URL = "https://gisthub-production.up.railway.app/"  # заміни на свій URL з Railway

MARKET_MARKUP = 1.70
BUYOUT_PERCENT = 0.95
STARS_PER_RUB = 1.5
ADMIN_ID = 7562324979

WELCOME_TEXT = '''Привет <tg-emoji emoji-id="5440431182602842059">👋</tg-emoji>
Добро пожаловать в GiftHub — надёжного бота для безопасных сделок с NFT подарками.
Немного о нас:
<tg-emoji emoji-id="5296369303661067030">🔒</tg-emoji> Гарант сделок между покупателем и продавцом
<tg-emoji emoji-id="5449800250032143374">🎁</tg-emoji> Автоскуп NFT подарков
<tg-emoji emoji-id="5249381781622247862">⚡</tg-emoji> Быстрый и удобный сервис
<tg-emoji emoji-id="5373174941095050893">💸</tg-emoji> Комиссия — 10%
Выберите нужный раздел ниже <tg-emoji emoji-id="5231102735817918643">👇</tg-emoji>'''

PHOTO_URL = "https://i.ibb.co/hFppHpVp/IMG-20260524-130341-793.jpg"

telethon_client: TelegramClient = None

# ==================== КУРС TON ====================

# Кеш курсів щоб не спамити API
_rates_cache = {}
_rates_cache_time = 0

async def get_ton_rates() -> dict:
    import time
    global _rates_cache, _rates_cache_time
    # Оновлюємо не частіше ніж раз на 5 хвилин
    if _rates_cache and time.time() - _rates_cache_time < 300:
        return _rates_cache
    try:
        # Спробуємо OKX — без обмежень
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://www.okx.com/api/v5/market/ticker?instId=TON-USDT",
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                d = await resp.json()
        ton_usd = float(d["data"][0]["last"])

        # Фіксовані приблизні курси USD до інших валют (оновлюються рідко)
        fx = {"uah": 41.5, "rub": 92.0, "kzt": 450.0, "eur": 0.92,
              "byn": 3.25, "uzs": 12700.0, "azn": 1.7, "amd": 390.0, "gel": 2.7}

        rates = {"usd": ton_usd}
        for cur, rate in fx.items():
            rates[cur] = round(ton_usd * rate, 2)

        _rates_cache = rates
        _rates_cache_time = time.time()
        return rates
    except Exception as e:
        print(f"[get_ton_rates] OKX помилка: {e}")
        # Fallback — CoinGecko з правильним заголовком
        try:
            async with aiohttp.ClientSession(headers={"accept": "application/json"}) as session:
                async with session.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "the-open-network", "vs_currencies": "usd,uah,rub,kzt,eur"},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json(content_type=None)
            rates = data.get("the-open-network", {})
            result = {k: rates.get(k, 0) for k in ["usd","uah","rub","kzt","eur","byn","uzs","azn","amd","gel"]}
            _rates_cache = result
            _rates_cache_time = time.time()
            return result
        except Exception as e2:
            print(f"[get_ton_rates] CoinGecko помилка: {e2}")
            return _rates_cache if _rates_cache else {}


def format_price(ton: float, rates: dict) -> str:
    lines = [f"💎 {ton} TON"]
    if rates.get("usd"): lines.append(f"💵 ${round(ton * rates['usd'], 2)}")
    if rates.get("eur"): lines.append(f"💶 €{round(ton * rates['eur'], 2)}")
    if rates.get("uah"): lines.append(f"🇺🇦 {round(ton * rates['uah'])} грн")
    if rates.get("rub"): lines.append(f"🇷🇺 {round(ton * rates['rub'])} руб")
    if rates.get("kzt"): lines.append(f"🇰🇿 {round(ton * rates['kzt'])} тенге")
    if rates.get("byn"): lines.append(f"🇧🇾 {round(ton * rates['byn'], 2)} бел.руб")
    if rates.get("uzs"): lines.append(f"🇺🇿 {round(ton * rates['uzs'])} сум")
    if rates.get("azn"): lines.append(f"🇦🇿 {round(ton * rates['azn'], 2)} ман")
    if rates.get("amd"): lines.append(f"🇦🇲 {round(ton * rates['amd'])} драм")
    if rates.get("gel"): lines.append(f"🇬🇪 {round(ton * rates['gel'], 2)} лари")
    return " | ".join(lines)


# ==================== ПАРСИНГ NFT ====================

async def get_price_from_pricebot(slug: str) -> dict:
    try:
        await telethon_client.send_message(PRICE_BOT, f"t.me/nft/{slug}")
        await asyncio.sleep(2)
        msgs = await telethon_client.get_messages(PRICE_BOT, limit=3)
        for msg in msgs:
            if msg.buttons:
                for row in msg.buttons:
                    for btn in row:
                        if any(x in btn.text.lower() for x in ['информация', 'подарк', 'gift info', 'information']):
                            await btn.click()
                            await asyncio.sleep(4)
                            break
        msgs = await telethon_client.get_messages(PRICE_BOT, limit=1)
        if not msgs: return {}
        text = msgs[0].text or ""
        clean = re.sub(r'\*\*', '', text)
        clean = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', clean)
        result = {}
        m = re.search(r'Floor:\s*([\d.]+)', clean)
        if m: result['floor_ton'] = float(m.group(1))
        m = re.search(r'AVG:\s*([\d.]+)', clean)
        if m: result['avg_ton'] = float(m.group(1))
        m = re.search(r'Последняя продажа:\s*([\d.]+)', clean)
        if m: result['last_ton'] = float(m.group(1))
        return result
    except Exception as e:
        print(f"[get_price] Помилка: {e}")
        return {}


async def get_attrs_from_telegram(slug: str) -> dict:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://t.me/nft/{slug}", headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                html = await resp.text()
        result = {}
        m = re.search(r'<meta property="og:title" content="([^"]+)"', html)
        if m: result['title'] = m.group(1).strip()
        m = re.search(r'<meta property="og:description" content="([^"]+)"', html)
        if m:
            attrs = {}
            for line in m.group(1).strip().splitlines():
                line = line.strip()
                if ':' in line:
                    k, _, v = line.partition(':')
                    attrs[k.strip()] = v.strip()
            if attrs: result['attrs'] = attrs
        return result
    except Exception as e:
        print(f"[get_attrs] Помилка: {e}")
        return {}


async def parse_nft(link: str) -> dict | None:
    slug_match = re.search(r't\.me/nft/([\w-]+)', link, re.IGNORECASE)
    if not slug_match: return None
    slug = slug_match.group(1)
    attrs_data, price_data, rates = await asyncio.gather(
        get_attrs_from_telegram(slug),
        get_price_from_pricebot(slug),
        get_ton_rates()
    )
    if not attrs_data.get('title'): return None
    return {
        'slug': slug, 'title': attrs_data.get('title', slug),
        'attrs': attrs_data.get('attrs', {}),
        'floor_ton': price_data.get('floor_ton'),
        'avg_ton': price_data.get('avg_ton'),
        'last_ton': price_data.get('last_ton'),
        'rates': rates,
    }


def format_nft_message(data: dict, link: str) -> str:
    lines = [f"🎁 *{data['title']}*\n"]
    if data.get('attrs'):
        icons = {'model': '📦', 'backdrop': '🎨', 'symbol': '🔷', 'pattern': '🌀'}
        for k, v in data['attrs'].items():
            lines.append(f"{icons.get(k.lower(), '▪️')} {k}: `{v}`")
    rates = data.get('rates', {})
    floor = data.get('floor_ton')
    avg = data.get('avg_ton')
    last = data.get('last_ton')
    if floor:
        floor_up = round(floor * MARKET_MARKUP, 2)
        lines.append("\n📊 *Рыночные данные:*")
        lines.append(f"  📉 Floor: {format_price(floor_up, rates)}")
        buyout = round(floor_up * BUYOUT_PERCENT, 2)
        lines.append(f"\n🛒 *Наше предложение выкупа:*\n  {format_price(buyout, rates)}")
    else:
        lines.append("\n💰 _Цена не найдена — уточните у менеджера_")
    lines.append(f"\n🔗 [Посмотреть NFT]({link})")
    return '\n'.join(lines)


# ==================== ВИЗНАЧЕННЯ СПОСОБУ ВИПЛАТИ ====================

def detect_payout(text: str) -> tuple[str, str]:
    text = text.strip()
    digits_only = re.sub(r'\D', '', text)

    if re.match(r'^\d+\s*[⭐*зв]', text, re.IGNORECASE) or text.lower() in ('звезды', 'зірки', 'stars', '⭐'):
        return ("⭐ *Telegram Stars*\nВыплата звёздами на ваш аккаунт.", 'STARS')

    if re.match(r'^(UQ|EQ)[A-Za-z0-9_-]{46}$', text):
        return ("💎 *Криптовалюта — TON*\nАдрес TON кошелька принят.", 'TON')
    if re.match(r'^0x[0-9a-fA-F]{40}$', text):
        return ("💎 *Криптовалюта — USDT/ETH (ERC-20)*\nАдрес принят.", 'USDT')
    if re.match(r'^T[1-9A-HJ-NP-Za-km-z]{33}$', text):
        return ("💎 *Криптовалюта — USDT (TRC-20)*\nАдрес принят.", 'USDT')
    if re.match(r'^(1|3)[1-9A-HJ-NP-Za-km-z]{25,34}$', text) or re.match(r'^bc1[a-z0-9]{39,59}$', text):
        return ("💎 *Криптовалюта — Bitcoin (BTC)*\nАдрес принят.", 'BTC')

    if re.match(r'^[\d\s\-]{16,23}$', text) and len(digits_only) == 16:
        first4 = digits_only[:4]
        # Україна
        if first4 in ('5375','4149','5168','4731','5355','4441','5209'):
            return ("🇺🇦 *Monobank (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in ('4405','5169','4276','4627','4246','5363'):
            return ("🇺🇦 *ПриватБанк (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in ('4058','6762'):
            return ("🇺🇦 *Ощадбанк (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in ('4552',):
            return ("🇺🇦 *ПУМБ (Украина)*\nНомер карты принят.", 'UAH')
        if first4 in ('4650','5229'):
            return ("🇺🇦 *УкрСибБанк (Украина)*\nНомер карты принят.", 'UAH')
        # Росія
        if first4 in ('5213','4377','5484','5189','4592','5296','2200','2201','2202','2203'):
            return ("🇷🇺 *Т-Банк / Tinkoff (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in ('4279','5469','5336'):
            return ("🇷🇺 *Сбербанк (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in ('4455','5157'):
            return ("🇷🇺 *ВТБ (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in ('5561','4154','5594'):
            return ("🇷🇺 *Альфа-Банк (Россия)*\nНомер карты принят.", 'RUB')
        if first4 in ('5100','5101','4960'):
            return ("🇷🇺 *Райффайзен (Россия)*\nНомер карты принят.", 'RUB')
        # Казахстан
        if first4 in ('4400','5170'):
            return ("🇰🇿 *Kaspi Bank (Казахстан)*\nНомер карты принят.", 'KZT')
        if first4 in ('5562','5478'):
            return ("🇰🇿 *Halyk Bank (Казахстан)*\nНомер карты принят.", 'KZT')
        if digits_only[0] in ('4','5'):
            return ("🏦 *Банковская карта*\nНомер принят. Менеджер уточнит банк.", 'USD')
        return ("🏦 *Банковская карта*\nНомер принят. Менеджер уточнит детали.", 'USD')

    phone_match = re.match(r'^(\+?[\d\s\-()]{10,15})$', text)
    if phone_match and 10 <= len(digits_only) <= 13:
        if digits_only.startswith('380') or digits_only.startswith('80'):
            return ("🇺🇦 *Monobank / ПриватБанк (Украина)*\nВыплата по номеру телефона.", 'UAH')
        if digits_only.startswith('7') or (digits_only.startswith('8') and len(digits_only) == 11):
            return ("🇷🇺 *Банк (Россия)*\nВыплата через СБП по номеру телефона.", 'RUB')
        if digits_only.startswith('375'):
            return ("🇧🇾 *Беларусбанк / МТБанк (Беларусь)*\nВыплата по номеру телефона.", 'BYN')
        if digits_only.startswith('77') or digits_only.startswith('76'):
            return ("🇰🇿 *Kaspi / Halyk (Казахстан)*\nВыплата по номеру телефона.", 'KZT')
        if digits_only.startswith('998'):
            return ("🇺🇿 *Uzum Bank / Kapitalbank (Узбекистан)*\nВыплата по номеру телефона.", 'UZS')
        if digits_only.startswith('994'):
            return ("🇦🇿 *ABB / Kapital Bank (Азербайджан)*\nВыплата по номеру телефона.", 'AZN')
        if digits_only.startswith('374'):
            return ("🇦🇲 *Ameriabank / ACBA (Армения)*\nВыплата по номеру телефона.", 'AMD')
        if digits_only.startswith('995'):
            return ("🇬🇪 *TBC / Bank of Georgia (Грузия)*\nВыплата по номеру телефона.", 'GEL')
        return ("📱 *Номер телефона принят*\nМенеджер уточнит способ выплаты.", 'USD')

    return ("❓ *Не удалось определить способ выплаты*\nПожалуйста, отправьте:\n— Номер карты (16 цифр)\n— Крипто-адрес (TON, USDT, BTC)\n— Номер телефона\n— «звезды» для выплаты звёздами", 'USD')


# ==================== ХЕНДЛЕРИ ====================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db.upsert_user(user_id=user.id, username=user.username, full_name=user.full_name, language_code=user.language_code)
    keyboard = [
        [InlineKeyboardButton("🔒 Безопасная сделка", callback_data="btn3")],
        [InlineKeyboardButton("🎁 Скуп NFT-подарков", callback_data="btn4")],
        [InlineKeyboardButton("👤 Личный кабинет", callback_data="profile")],
        [InlineKeyboardButton("💬 Отзывы", url="https://t.me/+24FS5JcCHgQxMjli"),
         InlineKeyboardButton("📖 Инструкция", url="https://telegra.ph/Instrukciya-05-24-28")],
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
            "Например: `t.me/nft/HappyBrownie-90097`\n\n"
            "⏳ Я оценю подарок и предложу цену выкупа!",
            parse_mode="Markdown"
        )

    elif query.data == "profile":
        user = query.from_user
        username = f"@{user.username}" if user.username else "не указан"

        import re as _re
        with db.get_conn() as conn:
            events = conn.execute(
                "SELECT amount_display FROM balance_events WHERE user_id = ? ORDER BY sent_at DESC",
                (user.id,)
            ).fetchall()

        ton_total = 0.0
        for e in events:
            m = _re.search(r'([\d\.]+)\s*TON', e['amount_display'], _re.IGNORECASE)
            if m:
                ton_total += float(m.group(1))
        ton_total = round(ton_total, 4)

        TON_RATE = 3.20
        usd = round(ton_total * TON_RATE, 2)
        eur = round(usd * 0.92, 2)
        uah = round(usd * 41.5, 0)
        rub = round(usd * 92.0, 0)
        kzt = round(usd * 450.0, 0)
        byn = round(usd * 3.25, 2)
        uzs = round(usd * 12700, 0)
        azn = round(usd * 1.7, 2)
        amd = round(usd * 390, 0)
        gel = round(usd * 2.7, 2)

        balance_lines = (
            f"💎 {ton_total} TON\n"
            f"💵 ${usd}\n"
            f"💶 €{eur}\n"
            f"🇺🇦 {int(uah)} грн\n"
            f"🇷🇺 {int(rub)} руб\n"
            f"🇰🇿 {int(kzt)} тенге\n"
            f"🇧🇾 {byn} бел.руб\n"
            f"🇺🇿 {int(uzs)} сум\n"
            f"🇦🇿 {azn} ман\n"
            f"🇦🇲 {int(amd)} драм\n"
            f"🇬🇪 {gel} лари"
        )

        keyboard = [[InlineKeyboardButton("💼 Открыть кошелёк", web_app={"url": MINI_APP_URL})]]

        text = (
            f"👤 *Личный кабинет пользователя {username}*\n\n"
            f"💰 *Баланс:*\n{balance_lines}\n\n"
            f"⚜️ Рейтинг: Не известен | 🤨\n\n"
            f"🆔 Ваш TelegramID: `{user.id}`"
        )

        try:
            photos = await context.bot.get_user_profile_photos(user.id, limit=1)
            if photos.total_count > 0:
                file_id = photos.photos[0][0].file_id
                await query.message.reply_photo(
                    photo=file_id,
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            await query.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

    elif query.data == "sell_nft":
        context.user_data['waiting_payout'] = True
        context.user_data['waiting_nft_link'] = False
        await query.message.reply_text(
            "💳 *Выберите способ выплаты*\n\n"
            "🏦 *Банковские карты:*\n"
            "  🇺🇦 Monobank, ПриватБанк, Ощадбанк, ПУМБ (Украина)\n"
            "  🇷🇺 Т-Банк, Сбербанк, ВТБ, Альфа-Банк (Россия)\n"
            "  🇧🇾 Беларусбанк, МТБанк (Беларусь)\n"
            "  🇰🇿 Kaspi, Halyk Bank (Казахстан)\n"
            "  🇺🇿 Uzum Bank, Kapitalbank (Узбекистан)\n"
            "  🇦🇿 ABB, Kapital Bank (Азербайджан)\n"
            "  🇦🇲 Ameriabank, ACBA (Армения)\n"
            "  🇬🇪 TBC Bank, Bank of Georgia (Грузия)\n\n"
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
    stars_count = round((buyout_ton * rates.get('rub', 0)) / STARS_PER_RUB)
    target = "на ваш аккаунт" if context.user_data.get('stars_target') == 'own' else "на другой аккаунт"
    context.user_data['req_currency'] = 'STARS'
    context.user_data['waiting_payout'] = False
    buyout_str = f"{stars_count} ⭐"
    req_id = db.save_requisite(user_id=query.from_user.id, raw_text=f"stars:{target}", detected_type=f"⭐ Telegram Stars ({target})", currency='STARS')
    context.user_data['req_db_id'] = req_id
    keyboard = [[InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_req"), InlineKeyboardButton("✏️ Изменить", callback_data="sell_nft")]]
    await query.message.reply_text(
        f"⭐ *Способ выплаты: Telegram Stars*\n\nВыплата: *{buyout_str}* {target}\nКурс: 1⭐ = {STARS_PER_RUB} руб\n\nПодтвердите выплату 👇",
        parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    db.touch_user(update.effective_user.id)

    if context.user_data.get('waiting_payout'):
        if re.search(r'звезд|зірк|stars|⭐', text, re.IGNORECASE):
            context.user_data['waiting_payout'] = False
            buyout_ton = context.user_data.get('buyout_ton', 0)
            rates = context.user_data.get('nft_rates', {})
            stars_count = round((buyout_ton * rates.get('rub', 0)) / STARS_PER_RUB)
            keyboard = [[InlineKeyboardButton("На свой аккаунт", callback_data="stars_own"), InlineKeyboardButton("На другой аккаунт", callback_data="stars_other")]]
            await update.message.reply_text(
                f"⭐ *Telegram Stars*\n\nСумма выплаты: *{stars_count} ⭐*\n(курс: 1⭐ = {STARS_PER_RUB} руб)\n\nКуда отправить звёзды?",
                parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        result, currency = detect_payout(text)
        context.user_data['waiting_payout'] = False
        context.user_data['req_currency'] = currency
        req_id = db.save_requisite(user_id=update.effective_user.id, raw_text=text, detected_type=result, currency=currency)
        context.user_data['req_db_id'] = req_id
        keyboard = [[InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_req"), InlineKeyboardButton("✏️ Изменить", callback_data="sell_nft")]]
        await update.message.reply_text(
            f"✅ *Способ выплаты принят:*\n`{text}`\n\n{result}\n\nМенеджер свяжется с вами для завершения сделки 🤝",
            parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    if not context.user_data.get('waiting_nft_link'):
        return

    nft_pattern = re.compile(r'(https?://)?t\.me/nft/[\w-]+', re.IGNORECASE)
    if not nft_pattern.search(text):
        await update.message.reply_text("❌ Не похоже на NFT-ссылку.\n\nНужен формат: `t.me/nft/НазваПодарка-Номер`", parse_mode="Markdown")
        return

    link = text if text.startswith('http') else f"https://{text}"
    wait_msg = await update.message.reply_text("⏳ Анализирую NFT, подождите ~5 секунд...")
    data = await parse_nft(link)
    await wait_msg.delete()

    if not data:
        await update.message.reply_text("😔 Не удалось получить данные.\nПроверьте ссылку и попробуйте снова.")
        return

    msg = format_nft_message(data, link)
    if data.get('floor_ton'):
        floor_up = round(data['floor_ton'] * MARKET_MARKUP, 2)
        context.user_data['buyout_ton'] = round(floor_up * BUYOUT_PERCENT, 2)
        context.user_data['nft_rates'] = data.get('rates', {})
    lookup_id = db.save_nft_lookup(user_id=update.effective_user.id, data=data, link=link, market_markup=MARKET_MARKUP, buyout_percent=BUYOUT_PERCENT)
    context.user_data['nft_lookup_db_id'] = lookup_id
    keyboard = [[InlineKeyboardButton("💰 Продать NFT", callback_data="sell_nft"), InlineKeyboardButton("🔄 Оценить другой", callback_data="btn4")]]
    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard), disable_web_page_preview=True)
    context.user_data['waiting_nft_link'] = False


# ==================== ПІДТВЕРДЖЕННЯ ====================

def get_buyout_in_currency(buyout_ton: float, rates: dict, currency: str) -> str:
    mapping = {
        'TON': lambda: f"{buyout_ton} TON",
        'USDT': lambda: f"{round(buyout_ton * rates.get('usd',0), 2)} USDT",
        'BTC': lambda: f"{round(buyout_ton * rates.get('usd',0) / 105000, 6)} BTC",
        'UAH': lambda: f"{round(buyout_ton * rates.get('uah',0))} грн",
        'RUB': lambda: f"{round(buyout_ton * rates.get('rub',0))} руб",
        'KZT': lambda: f"{round(buyout_ton * rates.get('kzt',0))} тенге",
        'BYN': lambda: f"{round(buyout_ton * rates.get('byn',0), 2)} BYN",
        'UZS': lambda: f"{round(buyout_ton * rates.get('uzs',0))} сум",
        'AZN': lambda: f"{round(buyout_ton * rates.get('azn',0), 2)} AZN",
        'AMD': lambda: f"{round(buyout_ton * rates.get('amd',0))} AMD",
        'GEL': lambda: f"{round(buyout_ton * rates.get('gel',0), 2)} GEL",
        'STARS': lambda: f"{round((buyout_ton * rates.get('rub',0)) / STARS_PER_RUB)} ⭐",
    }
    return mapping.get(currency, lambda: f"{round(buyout_ton * rates.get('usd',0), 2)} USD")()


async def confirm_requisites(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "confirm_req":
        currency = context.user_data.get('req_currency', 'USD')
        buyout_ton = context.user_data.get('buyout_ton')
        rates = context.user_data.get('nft_rates', {})
        user_id = query.from_user.id

        keyboard = [
            [InlineKeyboardButton("💼 Открыть кошелёк", web_app={"url": MINI_APP_URL})],
            [InlineKeyboardButton("✍️ Написать менеджеру", url="https://t.me/gifthub_manager")],
        ]

        if buyout_ton and rates:
            buyout_str = get_buyout_in_currency(buyout_ton, rates, currency)
            deal_id = db.create_deal(
                user_id=user_id,
                nft_lookup_id=context.user_data.get('nft_lookup_db_id'),
                requisite_id=context.user_data.get('req_db_id'),
                buyout_ton=buyout_ton, buyout_display=buyout_str, currency=currency,
            )
            context.user_data['current_deal_id'] = deal_id

        await query.message.reply_text(
            "✅ Способ выплаты подтверждён!\n\n"
            "📩 Напишите менеджеру @mshz_otc"
            " и отправьте ваш ID:\n\n"
            + str(user_id) +
            "\n\nПосле отправки NFT подарка менеджер пополнит ваш баланс.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ==================== АДМІН ====================

async def admin_topup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "Використання:\n`/topup USER_ID СУМА ВАЛЮТА`\n\nНаприклад:\n`/topup 123456789 5.5 TON`",
            parse_mode="Markdown"
        )
        return
    try:
        user_id = int(args[0])
        amount_str = args[1]
        currency = args[2].upper()
        display = f"{amount_str} {currency}"

        # Визначаємо суму в TON
        try:
            amount_num = float(amount_str)
        except:
            amount_num = 0.0

        ton_amount = amount_num if currency == 'TON' else None

        # Створюємо deal зі статусом paid — щоб мініапка показала баланс
        deal_id = db.create_deal(
            user_id=user_id,
            nft_lookup_id=None,
            requisite_id=None,
            buyout_ton=ton_amount,
            buyout_display=display,
            currency=currency,
        )
        db.mark_deal_paid(deal_id)
        db.log_balance_topup(user_id=user_id, deal_id=deal_id, amount_display=f"+{display}")

        await update.message.reply_text(
            f"✅ Баланс поповнено\nЮзер: {user_id}\nСума: {display}",
        )
        try:
            keyboard = [[InlineKeyboardButton("💼 Посмотреть баланс", web_app={"url": MINI_APP_URL})]]
            await context.bot.send_message(
                chat_id=user_id,
                text="💰 Ваш баланс пополнен!\n\n+" + display,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            await update.message.reply_text(f"⚠️ Не вдалось повідомити юзера: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Помилка: {e}")


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    stats = db.get_stats()
    await update.message.reply_text(
        f"📊 *Статистика GiftHub*\n\n"
        f"👥 Юзерів всього: {stats['users_total']}\n"
        f"🆕 Нових сьогодні: {stats['new_users_today']}\n"
        f"🔍 Оцінок NFT: {stats['lookups_total']}\n"
        f"🤝 Угод всього: {stats['deals_total']}\n"
        f"✅ Виплачено: {stats['deals_paid']}",
        parse_mode="Markdown"
    )


# ==================== ЗАПУСК ====================

async def auto_topup_on_id(event, bot):
    """
    Слухає всі приватні повідомлення юзербота.
    Якщо хтось пише Telegram ID — знаходить pending deal і поповнює баланс.
    Юзербот мовчить (не відповідає), бот пише юзеру в чат.
    """
    try:
        text = event.message.text.strip() if event.message.text else ""

        if not re.match(r'^\d{5,15}$', text):
            return

        user_id = int(text)

        with db.get_conn() as conn:
            deal = conn.execute(
                "SELECT * FROM deals WHERE user_id = ? AND status = 'pending' ORDER BY created_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()

        if not deal:
            return  # мовчимо якщо немає угоди

        deal_id = deal['id']
        buyout_display = deal['buyout_display']
        buyout_ton = deal['buyout_ton']

        # Якщо buyout_ton не збережений (наприклад UAH угода) —
        # беремо з nft_lookup по deal
        if not buyout_ton or buyout_ton == 0:
            with db.get_conn() as conn:
                lookup = conn.execute(
                    "SELECT buyout_ton FROM nft_lookups WHERE id = ?",
                    (deal['nft_lookup_id'],)
                ).fetchone()
            if lookup:
                buyout_ton = lookup['buyout_ton']

        db.mark_deal_paid(deal_id)
        amount_str = f"+{round(buyout_ton, 4)} TON" if buyout_ton else f"+{buyout_display}"
        db.log_balance_topup(
            user_id=user_id,
            deal_id=deal_id,
            amount_display=amount_str
        )

        # Запускаємо таймер — одразу пише юзеру "відправте NFT за 10 хв"
        ton_display = f"{round(buyout_ton, 4)} TON" if buyout_ton else buyout_display
        launch_gift_timer(bot=bot, user_id=user_id, deal_id=deal_id, buyout_display=ton_display)

        # Пишемо юзеру в бот — юзербот мовчить
        try:
            keyboard = [[InlineKeyboardButton("💼 Открыть кошелёк", web_app={"url": MINI_APP_URL})]]
            await bot.send_message(
                chat_id=user_id,
                text=(
                    f"💰 <b>Ваш баланс пополнен!</b>\n\n"
                    f"➕ <b>{amount_str}</b>\n\n"
                    f"Нажмите кнопку ниже, чтобы открыть кошелёк 👇"
                ),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            print(f"[auto_topup] Не вдалось повідомити юзера {user_id}: {e}")

    except Exception as e:
        print(f"[auto_topup] Помилка: {e}")


async def main():
    global telethon_client
    db.init_db()
    print("База даних ініціалізована ✅")
    telethon_client = TelegramClient('auth.session', API_ID, API_HASH)
    await telethon_client.start()
    print("Telethon підключено ✅")
    from gift_checker import set_telethon_client, set_bot
    set_telethon_client(telethon_client)
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    set_bot(app.bot)

    # Слухаємо всі вхідні приватні повідомлення
    from telethon import events
    @telethon_client.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_manager_message(event):
        sender = await event.get_sender()
        if getattr(sender, 'bot', False):
            return
        await auto_topup_on_id(event, app.bot)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", admin_stats))
    app.add_handler(CommandHandler("topup", admin_topup))
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
