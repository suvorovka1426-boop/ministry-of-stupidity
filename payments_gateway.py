"""Persistent payment adapter: YooMoney signed webhook -> Supabase atomic credit.

Use PAYMENT_BACKEND=supabase on serverless hosts. A commercial deployment must
explicitly enable payments after verifying provider eligibility and hosting terms.
Keys never enter browser JS or repo; the browser only gets checkout destination.
"""
import decimal
import hashlib
import hmac
import os
import re
import secrets
import urllib.parse

import cloud_payments as cloud
import payments as local

PaymentError = local.PaymentError
AMOUNT_KOPEKS = 1000
COINS_PER_PURCHASE = 120


def is_cloud():
    return os.getenv('PAYMENTS_BACKEND', '').lower() == 'supabase'


def configured():
    if not is_cloud():
        return local.configured()
    return bool(
        cloud.configured()
        and os.getenv('YOOMONEY_ENABLE_LIVE', '') == 'YES'
        and os.getenv('PAYMENT_COMMERCIAL_HOSTING_CONFIRMED', '') == 'YES'
        and os.getenv('PAYMENTS_PERSISTENCE_CONFIRMED', '') == 'YES'
        and re.fullmatch(r'\d{11,20}', os.getenv('YOOMONEY_WALLET', ''))
        and len(os.getenv('YOOMONEY_NOTIFICATION_SECRET', '')) >= 12
        and re.fullmatch(r'https://[a-zA-Z0-9][a-zA-Z0-9.-]+(?::443)?', os.getenv('PUBLIC_BASE_URL', '').strip())
    )


def translate(exc):
    if isinstance(exc, cloud.CloudPaymentError):
        raise PaymentError(str(exc), exc.status) from None
    raise exc


def create_player():
    if not is_cloud():
        return local.create_player()
    try: return cloud.register_player()
    except Exception as exc: translate(exc)


def require_player(headers):
    if not is_cloud():
        return local.require_player(headers)
    try: return cloud.require_player(headers)
    except Exception as exc: translate(exc)


def inventory(player_id):
    if not is_cloud():
        return local.inventory(player_id)
    try: return cloud.inventory(player_id)
    except Exception as exc: translate(exc)


def redeem(player_id):
    if not is_cloud():
        return local.redeem(player_id)
    # V26 paid coins are credited ATOMICALLY on webhook, no manual claim.
    return inventory(player_id)


def spend_coins(player_id, item, amount):
    if not is_cloud():
        return local.spend_coins(player_id, item, amount)
    if item == 'reroll':
        if type(amount) is not int or amount != 5:
            raise PaymentError('Неверная стоимость замены', 400)
    elif item not in local.ALLOWED_ITEMS or type(amount) is not int or amount != local.ALLOWED_ITEMS[item]:
        raise PaymentError('Стоимость предмета не совпадает с магазином', 400)
    try: return cloud.spend_coins(player_id, item, amount)
    except Exception as exc: translate(exc)


def create_checkout(player_id, _host=''):
    if not is_cloud():
        return local.create_checkout(player_id, _host)
    if not configured():
        raise PaymentError('Покупки не включены: проверь Supabase, HTTPS и разрешённый коммерческий хостинг', 503)
    order_id = 'ms' + secrets.token_hex(15)
    try: cloud.new_order(player_id, order_id)
    except Exception as exc: translate(exc)
    base = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
    return {'action':'https://yoomoney.ru/quickpay/confirm',
            'fields': {'receiver':os.environ['YOOMONEY_WALLET'],
                       'quickpay-form':'button', 'paymentType':'PC',
                       'sum':'10.00','label':order_id,
                       'successURL':base+'/?payment=success'},
            'amount_cents':AMOUNT_KOPEKS,'currency':'rub','order_id':order_id}


def _verify_notification(raw):
    if not 1 < len(raw) <= 16384:
        raise PaymentError('Некорректный размер уведомления', 413)
    try:
        raw_data=raw.decode('utf-8')
        values=urllib.parse.parse_qs(raw_data,keep_blank_values=True,strict_parsing=True,max_num_fields=50)
        if any(len(v)!=1 for v in values.values()): raise ValueError('Duplicate fields')
        fields={k:v[0] for k,v in values.items()}
    except (ValueError, UnicodeDecodeError):
        raise PaymentError('Некорректные параметры уведомления', 400)
    signature=fields.pop('sign','')
    if not re.fullmatch(r'[0-9A-Fa-f]{64}',signature):
        raise PaymentError('Отсутствует подпись ЮMoney', 403)
    canonical=urllib.parse.urlencode(sorted(fields.items()),encoding='utf-8',quote_via=urllib.parse.quote,safe='~').encode('utf8')
    expected=hmac.new(os.getenv('YOOMONEY_NOTIFICATION_SECRET','').encode(),canonical,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.lower(),expected):
        raise PaymentError('Неверная подпись ЮMoney', 403)
    if fields.get('test_notification')=='true':
        raise PaymentError('Тестовое уведомление не зачисляет монеты', 400)
    if fields.get('notification_type') not in ('p2p-incoming','card-incoming') or fields.get('currency')!='643' or fields.get('codepro')!='false' or fields.get('unaccepted')!='false':
        raise PaymentError('Платёж ещё не подтверждён',400)
    order=fields.get('label',''); operation=fields.get('operation_id','')
    if not re.fullmatch(r'ms[a-f0-9]{30}',order) or not re.fullmatch(r'\d{1,40}',operation):
        raise PaymentError('Неверный идентификатор покупки',400)
    try:
        sent=decimal.Decimal(fields['withdraw_amount']); received=decimal.Decimal(fields['amount'])
        if not sent.is_finite() or not received.is_finite() or sent!=decimal.Decimal('10.00') or not decimal.Decimal('9.50')<=received<=sent:
            raise ValueError('price')
    except (ValueError,decimal.InvalidOperation, KeyError):
        raise PaymentError('Сумма оплаты не равна 10 ₽',400)
    return order,operation


def receive_webhook(raw):
    if not is_cloud():
        return local.receive_webhook(raw)
    if not configured():
        raise PaymentError('Приём реальных платежей отключён',503)
    order,operation=_verify_notification(raw)
    try: return cloud.mark_paid(order, operation)
    except Exception as exc: translate(exc)
