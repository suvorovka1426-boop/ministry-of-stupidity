"""YooMoney direct wallet transfers, verified signed notifications, and SQLite inventory.
REAL transfers: no simulation, no pre-webhook award. Do not use without provider eligibility.
"""
import hashlib, hmac, os, re, secrets, sqlite3, time, urllib.parse
from contextlib import contextmanager
from pathlib import Path

DB_PATH=Path(os.getenv('PAYMENTS_DB_PATH', str(Path(__file__).resolve().parent/'ministry_payments.sqlite3'))).expanduser()
ITEM_ID='coins_120'
ITEM_NAME='Набор 120 игровых монет'
AMOUNT_KOPEKS=1000  # 10.00 RUB actually sent by user
CURRENCY='rub'
COINS_PER_PURCHASE=120
ALLOWED_ITEMS={'country:JP':75,'country:IL':90,'country:IT':105,'country:KP':120,
               'skin:noir':35,'skin:robot':60,'skin:royalty':90,'skin:cosmic':115,
               'theme:rose':35,'theme:mint':55,'theme:royal':85}


class PaymentError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message); self.status=status

@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn=sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS players(id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS purchases(id TEXT PRIMARY KEY, player_id TEXT NOT NULL REFERENCES players(id), session_id TEXT UNIQUE,
      amount_cents INTEGER NOT NULL, currency TEXT NOT NULL, item_id TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending', created_at INTEGER NOT NULL, fulfilled_at INTEGER);
    CREATE TABLE IF NOT EXISTS redemptions(id INTEGER PRIMARY KEY AUTOINCREMENT, player_id TEXT NOT NULL REFERENCES players(id),
      item_id TEXT NOT NULL, redeemed_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS wallets(player_id TEXT PRIMARY KEY REFERENCES players(id), balance INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS entitlements(player_id TEXT NOT NULL REFERENCES players(id), item_id TEXT NOT NULL,
      unlocked_at INTEGER NOT NULL, PRIMARY KEY(player_id,item_id));
    CREATE INDEX IF NOT EXISTS idx_purchases_player ON purchases(player_id,created_at);
    CREATE INDEX IF NOT EXISTS idx_redemptions_player ON redemptions(player_id,item_id);
    """)
    try:
        with conn: yield conn
    finally: conn.close()

def configured():
    """Real transfers require explicit opt-in AND a durable writable DB path."""
    public=os.getenv('PUBLIC_BASE_URL','').strip().rstrip('/')
    storage=os.getenv('PAYMENTS_DB_PATH','').strip()
    if not (re.fullmatch(r'\d{11,20}',os.getenv('YOOMONEY_WALLET',''))
            and len(os.getenv('YOOMONEY_NOTIFICATION_SECRET',''))>=12
            and os.getenv('YOOMONEY_ENABLE_LIVE')=='YES'
            and os.getenv('PAYMENTS_PERSISTENCE_CONFIRMED')=='YES'
            and re.fullmatch(r'https://[a-zA-Z0-9][a-zA-Z0-9.-]+(?::443)?',public)
            and storage and Path(storage).is_absolute()):
        return False
    # Refuse to activate payments on the app's ephemeral working directory.
    try:
        if Path(storage).resolve().parent == Path(__file__).resolve().parent:
            return False
        Path(storage).parent.mkdir(parents=True,exist_ok=True)
        if not os.access(Path(storage).parent,os.W_OK):
            return False
    except OSError:
        return False
    return True

def ensure_configured():
    if not configured(): raise PaymentError('ЮMoney выключен: проверьте кошелёк, секрет, HTTPS, YOOMONEY_ENABLE_LIVE и PAYMENTS_PERSISTENCE_CONFIRMED',503)

def create_player():
    token=secrets.token_urlsafe(32)
    with db() as c: c.execute('INSERT INTO players VALUES (?,?,?)',(secrets.token_hex(16),hashlib.sha256(token.encode()).hexdigest(),int(time.time())))
    return {'token':token}

def require_player(headers):
    token=headers.get('X-Player-Token','')
    if not 30<=len(token)<=128: raise PaymentError('Не найден токен игрока',401)
    with db() as c: row=c.execute('SELECT id FROM players WHERE token_hash=?',(hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
    if not row: raise PaymentError('Игрок не найден',401)
    return row['id']

def inventory(player_id):
    with db() as c:
        paid=c.execute('SELECT COUNT(*) FROM purchases WHERE player_id=? AND item_id=? AND status=?',(player_id,ITEM_ID,'paid')).fetchone()[0]
        used=c.execute('SELECT COUNT(*) FROM redemptions WHERE player_id=? AND item_id=?',(player_id,ITEM_ID)).fetchone()[0]
        rows=c.execute('SELECT id,status,amount_cents,currency,created_at FROM purchases WHERE player_id=? ORDER BY created_at DESC LIMIT 10',(player_id,)).fetchall()
        wallet=c.execute('SELECT balance FROM wallets WHERE player_id=?',(player_id,)).fetchone()
        entitlements=[r['item_id'] for r in c.execute('SELECT item_id FROM entitlements WHERE player_id=?',(player_id,)).fetchall()]
    return {'item':ITEM_ID,'item_name':ITEM_NAME,'quantity':max(0,paid-used),'purchased':paid,'used':used,'history':[dict(r) for r in rows],'coins_balance':int(wallet['balance']) if wallet else 0,'entitlements':entitlements}

def redeem(player_id):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        paid=c.execute("SELECT COUNT(*) FROM purchases WHERE player_id=? AND item_id=? AND status='paid'",(player_id,ITEM_ID)).fetchone()[0]
        used=c.execute('SELECT COUNT(*) FROM redemptions WHERE player_id=? AND item_id=?',(player_id,ITEM_ID)).fetchone()[0]
        if paid<=used: raise PaymentError('Нет подтверждённых оплаченных бонусов',409)
        c.execute('INSERT INTO redemptions(player_id,item_id,redeemed_at) VALUES(?,?,?)',(player_id,ITEM_ID,int(time.time())))
        c.execute('INSERT INTO wallets(player_id,balance) VALUES(?,?) ON CONFLICT(player_id) DO UPDATE SET balance=balance+?',(player_id,COINS_PER_PURCHASE,COINS_PER_PURCHASE))
    return inventory(player_id)

def spend_coins(player_id, item_id, amount):
    # Only paid coins exist on the server. Locally earned coins are spent in the browser.
    # Purchased entitlements are persisted server-side so a reload cannot erase paid goods.
    price=5 if item_id=='reroll' else ALLOWED_ITEMS.get(item_id)
    if price is None or type(amount) is not int or not (1<=amount<=price):
        raise PaymentError('Неверный предмет или сумма',400)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        if item_id!='reroll':
            already=c.execute('SELECT 1 FROM entitlements WHERE player_id=? AND item_id=?',(player_id,item_id)).fetchone()
            if already: raise PaymentError('Предмет уже разблокирован',409)
        row=c.execute('SELECT balance FROM wallets WHERE player_id=?',(player_id,)).fetchone()
        if not row or row['balance']<amount: raise PaymentError('Недостаточно подтверждённых платных монет',409)
        c.execute('UPDATE wallets SET balance=balance-? WHERE player_id=?',(amount,player_id))
        if item_id!='reroll':
            c.execute('INSERT INTO entitlements VALUES(?,?,?)',(player_id,item_id,int(time.time())))
        new_balance=c.execute('SELECT balance FROM wallets WHERE player_id=?',(player_id,)).fetchone()['balance']
    return {'ok':True,'coins_balance':new_balance,'unlocked':item_id}

def create_checkout(player_id, _host=None):
    ensure_configured()
    order_id='ms'+secrets.token_hex(15)
    with db() as c:
        c.execute('INSERT INTO purchases(id,player_id,amount_cents,currency,item_id,status,created_at) VALUES(?,?,?,?,?,?,?)',
                  (order_id,player_id,AMOUNT_KOPEKS,CURRENCY,ITEM_ID,'pending',int(time.time())))
    # Never trust redirect alone; it is not payment confirmation.
    origin=os.getenv('PUBLIC_BASE_URL','').rstrip('/')
    if origin and not origin.startswith('https://') and not re.fullmatch(r'http://(?:localhost|127\.0\.0\.1):\d+',origin):
        raise PaymentError('PUBLIC_BASE_URL должен использовать HTTPS (кроме localhost)',503)
    form={'receiver':os.environ['YOOMONEY_WALLET'],'quickpay-form':'button', 'paymentType':'PC',
          'sum':f'{AMOUNT_KOPEKS/100:.2f}','label':order_id}
    if origin: form['successURL']=origin+'/?payment=success'
    return {'action':'https://yoomoney.ru/quickpay/confirm','fields':form,
            'amount_cents':AMOUNT_KOPEKS,'currency':CURRENCY,'order_id':order_id}

def receive_webhook(raw, _sig=''):
    ensure_configured()
    if len(raw)>16384: raise PaymentError('Слишком большое уведомление',413)
    try:
        params=urllib.parse.parse_qs(raw.decode('utf-8'),keep_blank_values=True,strict_parsing=True)
        if any(len(v)!=1 for v in params.values()): raise ValueError('Duplicate fields')
        fields={k:v[0] for k,v in params.items()}
    except (ValueError,UnicodeDecodeError): raise PaymentError('Неверный формат уведомления',400)
    sign=fields.pop('sign','')
    if not re.fullmatch(r'[0-9a-fA-F]{64}',sign): raise PaymentError('Нет действительной подписи',403)
    # Current YooMoney signature: HMAC-SHA256 over alphabetical URL-encoded params excluding sign.
    payload=urllib.parse.urlencode(sorted(fields.items()),encoding='utf-8',quote_via=urllib.parse.quote,safe='~').encode('utf-8')
    expected=hmac.new(os.environ['YOOMONEY_NOTIFICATION_SECRET'].encode('utf-8'),payload,hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sign.lower(),expected): raise PaymentError('Подпись ЮMoney неверна',403)
    if fields.get('test_notification','false')=='true': raise PaymentError('Тестовое уведомление не является оплатой',400)
    if fields.get('notification_type') not in ('p2p-incoming','card-incoming') or fields.get('currency')!='643' or fields.get('codepro')!='false' or fields.get('unaccepted')!='false':
        raise PaymentError('Неподходящий тип перевода',400)
    order_id=fields.get('label','')
    operation_id=fields.get('operation_id','')
    if not re.fullmatch(r'ms[0-9a-f]{30}',order_id) or not re.fullmatch(r'\d{1,40}',operation_id):
        raise PaymentError('Неизвестный заказ или операция',400)
    # Sum in the transfer (withdraw_amount) must match the order, received amount
    # can be less because of provider fees (1% wallet / 3% bank card).
    from decimal import Decimal,InvalidOperation
    try:
        sent=Decimal(fields['withdraw_amount']); credited=Decimal(fields['amount'])
    except (InvalidOperation,KeyError): raise PaymentError('Некорректная сумма',400)
    if not sent.is_finite() or not credited.is_finite() or sent!=Decimal('10.00') or credited<Decimal('9.50') or credited>sent:
        raise PaymentError('Сумма перевода не совпадает с заказом',400)
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT * FROM purchases WHERE id=?',(order_id,)).fetchone()
        if not row: raise PaymentError('Заказ не найден',404)
        if row['amount_cents']!=AMOUNT_KOPEKS or row['currency']!=CURRENCY: raise PaymentError('Несоответствие заказа',400)
        # operation id must never be used for two different purchases.
        existing=c.execute('SELECT id FROM purchases WHERE session_id=?',(operation_id,)).fetchone()
        if existing and existing['id']!=order_id: raise PaymentError('Операция уже использована',409)
        if row['status']=='paid':
            if row['session_id']!=operation_id: raise PaymentError('Заказ уже оплачен другой операцией',409)
            return {'ok':True,'duplicate':True}
        if row['status']!='pending': raise PaymentError('Заказ закрыт',409)
        c.execute("UPDATE purchases SET status='paid',session_id=?,fulfilled_at=? WHERE id=? AND status='pending'",
                  (operation_id,int(time.time()),order_id))
    return {'ok':True}
