"""Leaderboard storage. Offline SQLite or shared Supabase (server-side credentials only).
Scores are client-submitted and rate-limited, not cryptographically gameplay-verified.
"""
import os, sqlite3, json, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timezone
from contextlib import contextmanager

DEFAULT_PATH = Path(__file__).resolve().parent / 'ministry_scores.sqlite3'


def admin_key():
    """Preferred 2026 Supabase secret key; legacy service_role remains supported."""
    return os.getenv('SUPABASE_SECRET_KEY', '').strip() or os.getenv('SUPABASE_SERVICE_ROLE_KEY', '').strip()


def mode():
    return 'global' if os.getenv('SUPABASE_URL', '').startswith('https://') and admin_key() else 'local'


def db_path():
    return Path(os.getenv('LEADERBOARD_DB_PATH') or str(DEFAULT_PATH)).expanduser()


@contextmanager
def db():
    path=db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn=sqlite3.connect(path,timeout=10)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA busy_timeout=10000')
    conn.execute("""CREATE TABLE IF NOT EXISTS scores (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      player TEXT NOT NULL,
      player_key TEXT NOT NULL,
      country TEXT NOT NULL,
      days INTEGER NOT NULL,
      score INTEGER NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(player_key, country)
    )""")
    conn.execute('CREATE INDEX IF NOT EXISTS idx_scores_rank ON scores(score DESC, days DESC, created_at ASC)')
    try:
        yield conn
        conn.commit()
    finally:conn.close()


def supabase_request(method,endpoint,payload=None):
    base=os.environ['SUPABASE_URL'].rstrip('/')
    key=admin_key()
    if not key:
        raise RuntimeError('Ключ Supabase не задан')
    data=json.dumps(payload,ensure_ascii=False).encode('utf-8') if payload is not None else None
    # New sb_secret_* keys are not JWTs. Supabase requires the `apikey` header;
    # legacy service_role JWTs additionally accept Authorization: Bearer.
    headers={'apikey':key,'Content-Type':'application/json',
             'Accept':'application/json','User-Agent':'MinistryGame/Leaderboard3'}
    if not key.startswith('sb_secret_'):
        headers['Authorization']='Bearer '+key
    request=urllib.request.Request(base+'/rest/v1/'+endpoint,method=method,data=data,headers=headers)
    with urllib.request.urlopen(request,timeout=12) as response:
        raw=response.read()
    return json.loads(raw) if raw else None


def top(country=None, limit=20):
    limit=max(1,min(50,int(limit)))
    if mode()=='global':
        q='ministry_scores?select=player,country,days,score,created_at'
        if country:q+='&country=eq.'+urllib.parse.quote(country,safe='')
        # SQL enforces one best result per normalized nickname and country.
        q+='&order=score.desc,days.desc,created_at.asc&limit='+str(limit)
        rows=supabase_request('GET',q) or []
        if not isinstance(rows,list):raise ValueError('Некорректный ответ Supabase')
        filtered=[{k:row.get(k) for k in ('player','country','days','score','created_at')} for row in rows]
        return {'source':'global','entries':filtered,'limit':limit}
    with db() as conn:
        if country:
            rows=conn.execute('SELECT player,country,days,score,created_at FROM scores WHERE country=? ORDER BY score DESC,days DESC,created_at ASC LIMIT ?', (country,limit)).fetchall()
        else:
            rows=conn.execute('SELECT player,country,days,score,created_at FROM scores ORDER BY score DESC,days DESC,created_at ASC LIMIT ?', (limit,)).fetchall()
    return {'source':'local','entries':[dict(row) for row in rows],'limit':limit}


def record(player,country,days,score):
    if mode()=='global':
        # Atomic PostgreSQL upsert, retaining the highest result even for
        # competing simultaneous requests. Install supabase.sql before using.
        result=supabase_request('POST','rpc/ministry_submit_score',{
            'p_player':player,'p_country':country,'p_days':days,'p_score':score})
        if type(result) is not bool:
            raise ValueError('Supabase RPC did not return a boolean; run supabase.sql')
        return {'ok':True,'saved':result,'source':'global'}
    now=datetime.now(timezone.utc).isoformat(timespec='seconds')
    with db() as conn:
        old=conn.execute('SELECT score,days FROM scores WHERE player_key=? AND country=?',(player.casefold(),country)).fetchone()
        better=old is None or (score,days)>(old['score'],old['days'])
        if better:
            conn.execute("""INSERT INTO scores(player,player_key,country,days,score,created_at) VALUES(?,?,?,?,?,?)
              ON CONFLICT(player_key,country) DO UPDATE SET player=excluded.player,days=excluded.days,score=excluded.score,created_at=excluded.created_at
              WHERE excluded.score>scores.score OR (excluded.score=scores.score AND excluded.days>scores.days)""",
              (player,player.casefold(),country,days,score,now))
    return {'ok':True,'saved':better,'source':'local'}
