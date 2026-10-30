"""Persistent YooMoney order/credit accounting in Supabase.

The HTTP API and secret key are used ONLY by the Python backend. Atomic updates
are performed by tightly-scoped PostgreSQL RPC functions in payments_supabase.sql.
"""
import hashlib
import json
import os
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request

from leaderboard import admin_key


class CloudPaymentError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def configured():
    base = os.getenv('SUPABASE_URL', '').strip().rstrip('/')
    return bool(re.fullmatch(r'https://[A-Za-z0-9-]+\.supabase\.co', base) and admin_key())


def request(method, endpoint, data=None, prefer=None):
    if not configured():
        raise CloudPaymentError('Для платежей необходимо подключить постоянную базу Supabase', 503)
    url = os.getenv('SUPABASE_URL', '').rstrip('/') + '/rest/v1/' + endpoint
    key = admin_key()
    headers = {'apikey': key, 'Content-Type': 'application/json',
               'Accept': 'application/json', 'User-Agent': 'MinistryGame/Payments25'}
    if not key.startswith('sb_secret_'):
        headers['Authorization'] = 'Bearer ' + key
    if prefer:
        headers['Prefer'] = prefer
    body = json.dumps(data, separators=(',', ':')).encode('utf-8') if data is not None else None
    req = urllib.request.Request(url, method=method, headers=headers, data=body)
    try:
        with urllib.request.urlopen(req, timeout=12) as res:
            raw = res.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        # Do not forward database details or credentials to end users.
        print('Payment database RPC failed:', exc.code, endpoint.split('?')[0])
        raise CloudPaymentError('Платёжная база временно недоступна или SQL не установлен', 503) from None
    except (urllib.error.URLError, TimeoutError, ValueError):
        raise CloudPaymentError('Нет связи с платёжной базой', 503) from None


def register_player():
    token = secrets.token_urlsafe(32)
    player_id = secrets.token_hex(16)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    request('POST', 'ministry_pay_players', {'id': player_id, 'token_hash': token_hash}, 'return=minimal')
    return {'token': token}


def require_player(headers):
    token = headers.get('X-Player-Token', '')
    if not isinstance(token, str) or not 30 <= len(token) <= 128:
        raise CloudPaymentError('Не найден токен игрока', 401)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    query = 'ministry_pay_players?select=id&token_hash=eq.' + urllib.parse.quote(token_hash, safe='') + '&limit=1'
    rows = request('GET', query)
    if not isinstance(rows, list) or len(rows) != 1:
        raise CloudPaymentError('Игрок не найден. Обновите подключение казны.', 401)
    return rows[0]['id']


def inventory(player_id):
    result = request('POST', 'rpc/ministry_pay_inventory', {'p_player': player_id})
    if not isinstance(result, dict):
        raise CloudPaymentError('Некорректный ответ базы покупок', 503)
    return result


def new_order(player_id, order_id):
    result = request('POST', 'rpc/ministry_pay_create_order',
                     {'p_player': player_id, 'p_order': order_id})
    if result is not True:
        raise CloudPaymentError('Слишком много неоплаченных заказов. Попробуйте позже.', 429)


def mark_paid(order_id, operation_id):
    result = request('POST', 'rpc/ministry_pay_confirm',
                     {'p_order': order_id, 'p_operation': operation_id})
    if result not in ('paid', 'duplicate'):
        raise CloudPaymentError('Заказ отсутствует или уже оплачен другой операцией', 409)
    return {'ok': True, 'duplicate': result == 'duplicate'}


def redeem(player_id):
    result = request('POST', 'rpc/ministry_pay_redeem', {'p_player': player_id})
    if result is not True:
        raise CloudPaymentError('Нет подтверждённых оплаченных наборов', 409)
    return inventory(player_id)


def spend_coins(player_id, item_id, amount):
    result = request('POST', 'rpc/ministry_pay_spend',
                     {'p_player': player_id, 'p_item': item_id, 'p_amount': amount})
    if not isinstance(result, dict) or not result.get('ok'):
        raise CloudPaymentError('Недостаточно монет или предмет уже куплен', 409)
    return result
