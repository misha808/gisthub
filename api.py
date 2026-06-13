from flask import Flask, request, jsonify, send_from_directory
import database as db
import re
import os

app = Flask(__name__)

@app.route('/')
def index():
    return send_from_directory('.', 'miniapp.html')

@app.route('/api/ton_rate')
def ton_rate():
    import urllib.request, json as _json

    apis = [
        'https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd&include_24hr_change=true',
        'https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT',
    ]

    # CoinGecko
    try:
        print('[ton_rate] Запрос к CoinGecko...')
        with urllib.request.urlopen(apis[0], timeout=5) as resp:
            body = resp.read().decode()
        data = _json.loads(body).get('the-open-network', {})
        usd = data.get('usd', 0)
        if usd:
            print(f'[ton_rate] CoinGecko OK: ${usd}')
            return jsonify({'usd': usd, 'change': data.get('usd_24h_change', 0)})
    except Exception as e:
        print(f'[ton_rate] CoinGecko ОШИБКА: {e}')

    # Binance fallback
    try:
        print('[ton_rate] Запрос к Binance...')
        with urllib.request.urlopen(apis[1], timeout=5) as resp:
            body = resp.read().decode()
        usd = float(_json.loads(body).get('price', 0))
        if usd:
            print(f'[ton_rate] Binance OK: ${usd}')
            return jsonify({'usd': usd, 'change': 0})
    except Exception as e:
        print(f'[ton_rate] Binance ОШИБКА: {e}')

    print('[ton_rate] Все API недоступны')
    return jsonify({'usd': 0, 'error': 'all apis failed'}), 500


@app.route('/api/balance')
def balance():
    user_id = request.args.get('user_id', type=int)
    if not user_id:
        return jsonify({'error': 'no user_id'}), 400

    with db.get_conn() as conn:
        deals = conn.execute(
            "SELECT buyout_ton, currency FROM deals WHERE user_id = ? AND status = 'paid'",
            (user_id,)
        ).fetchall()

        history = conn.execute(
            "SELECT amount_display, sent_at, label FROM balance_events WHERE user_id = ? ORDER BY sent_at DESC LIMIT 20",
            (user_id,)
        ).fetchall()

    # Рахуємо TON з усіх balance_events (+ поповнення, - списання)
    ton_total = 0.0
    for h in history:
        m = re.search(r'([+-]?[\d\.]+)\s*TON', h['amount_display'], re.IGNORECASE)
        if m:
            ton_total += float(m.group(1))

    # Останні реквізити юзера
    with db.get_conn() as conn:
        req = conn.execute(
            "SELECT raw_text, detected_type, currency FROM requisites WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()

    requisite = None
    if req:
        requisite = {
            'raw_text': req['raw_text'],
            'detected_type': req['detected_type'],
            'currency': req['currency'],
        }

    # Pending deal (NFT ще не отримано)
    with db.get_conn() as conn:
        pending = conn.execute(
            "SELECT id FROM deals WHERE user_id = ? AND status IN ('pending','gift_received') ORDER BY created_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()

    return jsonify({
        'ton': round(ton_total, 4),
        'stars': 0,
        'frozen': db.is_balance_frozen(user_id),
        'history': [
            {'amount_display': h['amount_display'], 'sent_at': h['sent_at'], 'label': h['label'] if h['label'] else 'Від @GiftHubUserBot'}
            for h in history
        ],
        'requisite': requisite,
        'has_pending': pending is not None,
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
