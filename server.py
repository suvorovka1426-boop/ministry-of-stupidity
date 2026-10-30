"""Министерство глупостей: minimal server-side proxy for 3 gameplay APIs."""
import json
import mimetypes
import time
import re
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import payments_gateway as payments
import leaderboard

ROOT = Path(__file__).resolve().parent
COUNTRIES = {'RU': ('Russia', 55.75, 37.62), 'US': ('United States', 38.90, -77.04), 'JP': ('Japan', 35.68, 139.69), 'IT': ('Italy', 41.90, 12.50), 'CN': ('China', 39.90, 116.40), 'IL': ('Israel', 31.77, 35.21), 'KP': ('North Korea', 39.04, 125.76)}

CACHE = {}
# World Bank country identifiers. KP data may be unavailable; no fabricated values.
WB_CODES = {'RU':'RUS','US':'USA','JP':'JPN','IT':'ITA','CN':'CHN','IL':'ISR','KP':'PRK'}
SCORE_LIMIT = {}

def wb_economy(code):
    indicators = {'gdp_per_capita':'NY.GDP.PCAP.CD', 'inflation':'FP.CPI.TOTL.ZG'}
    result = {'gdp_per_capita': None, 'inflation': None, 'years': {}}
    for label, indicator in indicators.items():
        try:
            url = 'https://api.worldbank.org/v2/country/'+WB_CODES[code]+'/indicator/'+indicator+'?format=json&per_page=12'
            payload = cached_json('wb:'+code+':'+indicator, url, 86400)
            entries = payload[1] if isinstance(payload, list) and len(payload)>1 and isinstance(payload[1], list) else []
            found = next((v for v in entries if v.get('value') is not None), None)
            if found:
                result[label] = round(float(found['value']), 2)
                result['years'][label] = found.get('date')
        except Exception:
            pass
    return result

def country_population(code):
    """Try REST Countries endpoints, then a clearly labelled World Bank fallback."""
    alpha3 = WB_CODES[code]
    urls = [
        'https://restcountries.com/v3.1/alpha/' + code + '?fields=name,population,capital',
        'https://restcountries.com/v3.1/alpha/' + alpha3 + '?fields=name,population,capital',
        'https://restcountries.com/v3.1/alpha/' + alpha3,
        'https://restcountries.com/v3.1/alpha/' + code,
        'https://restcountries.com/v3.1/all?fields=cca2,name,population,capital',
    ]
    for index, url in enumerate(urls):
        try:
            data = cached_json('country:'+code+':'+str(index), url, 86400)
            if isinstance(data, list):
                data = next((item for item in data if isinstance(item, dict) and item.get('cca2') == code), None) if 'all?fields=' in url else (data[0] if data else None)
            if not isinstance(data, dict):
                continue
            population = data.get('population')
            if type(population) is int and population > 0:
                return {'name': data.get('name', {}).get('common', COUNTRIES[code][0]),
                        'population': population, 'capital': data.get('capital', []),
                        'source': 'REST Countries'}
        except (ValueError, KeyError, TypeError, urllib.error.URLError, TimeoutError, OSError):
            continue
    try:
        url = ('https://api.worldbank.org/v2/country/' + alpha3 +
               '/indicator/SP.POP.TOTL?format=json&per_page=12')
        data = cached_json('wb:population:'+code, url, 86400)
        entries = data[1] if isinstance(data, list) and len(data) > 1 and isinstance(data[1], list) else []
        found = next((row for row in entries if row.get('value') is not None), None)
        if found:
            return {'name': COUNTRIES[code][0], 'population': int(found['value']),
                    'capital': [], 'source': 'World Bank', 'year': found.get('date')}
    except (ValueError, KeyError, TypeError, urllib.error.URLError, TimeoutError, OSError):
        pass
    return None


def cached_json(key, url, ttl=1800):
    entry = CACHE.get(key)
    if entry and time.time() - entry[0] < ttl:
        return entry[1]
    value = request_json(url)
    CACHE[key] = (time.time(), value)
    return value

def request_json(url, headers=None, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={'User-Agent': 'MinistryGame/1.0', **(headers or {})})
    with urllib.request.urlopen(req, timeout=12) as response:
        return json.load(response)

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        if path.path == '/health':
            return self.send_json({'ok': True, 'service': 'ministry-of-stupidity'})
        if path.path == '/api/status':
            # Configuration only: never return credentials or secret values.
            return self.send_json({
                'server': 'online',
                'world': 'ready',
                'ai': 'configured' if (os.getenv('SHUPP_API_KEY') or os.getenv('OPENAI_API_KEY')) else 'needs_key',
                'leaderboard': 'global' if leaderboard.mode() == 'global' else 'local',
                'payments': 'live_ready' if payments.configured() else 'needs_setup',
                'payments_mode': 'real_transfers',
                'note': 'Статус настройки, а не подтверждение доступности внешних сервисов'
            })
        if path.path in ('/api/payments/inventory', '/api/payments/status'):
            try:
                player_id = payments.require_player(self.headers)
                if path.path == '/api/payments/status':
                    return self.send_json({'paid':False,'message':'Только подписанное уведомление ЮMoney подтверждает перевод','inventory':payments.inventory(player_id)})
                return self.send_json(payments.inventory(player_id))
            except payments.PaymentError as exc:
                return self.send_json({'error': str(exc)}, exc.status)
            except Exception:
                return self.send_json({'error': 'Не удалось получить покупки'}, 503)
        if path.path == '/api/leaderboard/check':
            # Performs a real database read; unlike /api/status this validates
            # that the configured table and server key are usable.
            if leaderboard.mode() != 'global':
                return self.send_json({'connected':False,'source':'local',
                                       'message':'Supabase ещё не настроен; используется локальный рейтинг SQLite.'})
            try:
                leaderboard.top(limit=1)
                return self.send_json({'connected':True,'source':'global',
                                       'message':'Supabase подключён. Общий рейтинг доступен.'})
            except Exception as exc:
                print('Supabase check failed:', type(exc).__name__)
                return self.send_json({'connected':False,'source':'global',
                                       'message':'Нет связи с Supabase. Проверь URL, секретный ключ и SQL-таблицу.'}, 503)
        if path.path == '/api/leaderboard':
            params = urllib.parse.parse_qs(path.query)
            country = params.get('country', ['ALL'])[0]
            if country != 'ALL' and country not in COUNTRIES:
                return self.send_json({'error': 'Неизвестная страна'}, 400)
            try:
                result = leaderboard.top(None if country == 'ALL' else country, 20)
                result['country'] = country
                result['configured'] = True
                return self.send_json(result)
            except Exception as exc:
                print('Leaderboard read:', type(exc).__name__)
                return self.send_json({'error':'Не удалось загрузить рейтинг. Проверьте настройки базы данных.'},503)
        if path.path == '/api/world':
            code = urllib.parse.parse_qs(path.query).get('country', ['RU'])[0]
            if code not in COUNTRIES:
                return self.send_json({'error': 'Неизвестная страна'}, 400)
            name, lat, lon = COUNTRIES[code]
            # Fetch sources separately so one outage does not hide the others.
            result = {'country': None, 'weather': None, 'rates': None, 'fxLive': False, 'economy': None, 'errors': []}
            result['country'] = country_population(code)
            if not result['country']:
                result['errors'].append('население (REST Countries / World Bank)')
            elif result['country']['source'] != 'REST Countries':
                result['errors'].append('REST Countries (население из World Bank)')
            try:
                q = urllib.parse.urlencode({'latitude': lat, 'longitude': lon, 'current': 'temperature_2m,precipitation', 'timezone': 'auto'})
                w = cached_json('weather:'+code, 'https://api.open-meteo.com/v1/forecast?' + q, 900)['current']
                result['weather'] = {'temperature': w['temperature_2m'], 'precipitation': w['precipitation']}
            except Exception:
                result['errors'].append('Open-Meteo')
            try:
                fx = cached_json('fx', 'https://open.er-api.com/v6/latest/USD', 21600)
                if fx.get('result') == 'success' and isinstance(fx.get('rates'), dict):
                    result['rates'] = {k: fx['rates'][k] for k in ('USD','RUB','JPY','CNY','ILS','EUR') if k in fx['rates']}
                    result['fxLive'] = True
            except Exception:
                result['errors'].append('Валютный API')
            result['economy'] = wb_economy(code)
            if result['economy']['gdp_per_capita'] is None and result['economy']['inflation'] is None:
                result['errors'].append('World Bank')
            if not result['country'] and not result['weather'] and result['economy']['inflation'] is None:
                return self.send_json({'error': 'Нет связи с REST Countries и Open-Meteo. Проверьте интернет и повторите.'}, 503)
            return self.send_json(result)
        if path.path in ('/', '/index.html'):
            body = (ROOT / 'index.html').read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        # Serve bundled artwork and other static files from the project folder.
        # Previously all /assets/* requests returned 404, leaving an empty blue scene.
        if path.path.startswith('/assets/'):
            relative = path.path.lstrip('/')
            target = (ROOT / relative).resolve()
            assets_root = (ROOT / 'assets').resolve()
            if not target.is_relative_to(assets_root) or not target.is_file():
                return self.send_error(404)
            body = target.read_bytes()
            content_type = mimetypes.guess_type(target.name)[0] or 'application/octet-stream'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'public, max-age=3600')
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404)

    def do_POST(self):
        if self.path in ('/api/player', '/api/payments/checkout', '/api/payments/redeem', '/api/payments/spend', '/api/payments/webhook'):
            try:
                if self.path == '/api/player':
                    return self.send_json(payments.create_player(), 201)
                if self.path == '/api/payments/webhook':
                    length = int(self.headers.get('Content-Length', '0'))
                    if length < 2 or length > 262144:
                        return self.send_json({'error': 'Неверный размер webhook'}, 413)
                    raw = self.rfile.read(length)
                    return self.send_json(payments.receive_webhook(raw))
                length = int(self.headers.get('Content-Length', '0'))
                if length > 2048 or length < 0:
                    return self.send_json({'error': 'Слишком большой запрос'}, 413)
                player_id = payments.require_player(self.headers)
                if self.path == '/api/payments/checkout':
                    return self.send_json(payments.create_checkout(player_id, self.headers.get('Host', '')))
                if self.path == '/api/payments/spend':
                    try: body=json.loads(self.rfile.read(length) or b'{}')
                    except (ValueError, UnicodeDecodeError): return self.send_json({'error':'Неверный JSON'},400)
                    if not isinstance(body,dict): return self.send_json({'error':'Неверный формат'},400)
                    return self.send_json(payments.spend_coins(player_id,body.get('item'),body.get('amount')))
                return self.send_json(payments.redeem(player_id))
            except payments.PaymentError as exc:
                return self.send_json({'error': str(exc)}, exc.status)
            except Exception as exc:
                print('Payment server error:', type(exc).__name__)
                return self.send_json({'error': 'Платёжный сервер временно недоступен'}, 503)
        if self.path == '/api/leaderboard':
            length = int(self.headers.get('Content-Length', 0))
            if length < 1 or length > 512:
                return self.send_json({'error':'Неверный размер запроса'}, 400)
            try:
                data = json.loads(self.rfile.read(length))
                player = data.get('player', '')
                country = data.get('country', '')
                days = data.get('days', 0)
                score = data.get('score', 0)
                if not isinstance(player, str) or not (1 <= len(player.strip()) <= 24) or not re.fullmatch(r'[\w\s\-А-Яа-яЁё]{1,24}', player.strip(), re.UNICODE):
                    raise ValueError('Имя: 1–24 символа, только буквы, цифры, пробелы и дефисы')
                if country not in COUNTRIES or type(days) is not int or not (1 <= days <= 20) or type(score) is not int or not (0 <= score <= (days - 1) * 100 + 400 + (500 if days == 20 else 0)):
                    raise ValueError('Неверные игровые показатели')
                ip = self.client_address[0]
                now = time.time()
                if now - SCORE_LIMIT.get(ip, 0) < 20:
                    return self.send_json({'error':'Подождите 20 секунд перед отправкой'}, 429)
                SCORE_LIMIT[ip] = now
                if len(SCORE_LIMIT)>5000:
                    SCORE_LIMIT.clear()
                    SCORE_LIMIT[ip] = now
                result = leaderboard.record(player.strip(), country, days, score)
                return self.send_json(result)
            except ValueError as exc:
                return self.send_json({'error':str(exc)}, 400)
            except Exception:
                return self.send_json({'error':'Рейтинг временно недоступен. Проверьте базу данных.'}, 503)
        if self.path != '/api/ai-law': return self.send_error(404)
        length = int(self.headers.get('Content-Length', 0))
        if length > 2048: return self.send_json({'error': 'Слишком большой запрос'}, 413)
        try: body = json.loads(self.rfile.read(length))
        except Exception: return self.send_json({'error': 'Неверный JSON'}, 400)
        key = os.getenv('SHUPP_API_KEY') or os.getenv('OPENAI_API_KEY')
        if not key: return self.send_json({'error': 'Укажите SHUPP_API_KEY на сервере (или OPENAI_API_KEY для официального OpenAI).'}, 503)
        code = body.get('country', 'RU')
        if code not in COUNTRIES: return self.send_json({'error': 'Неизвестная страна'}, 400)
        stats = body.get('stats', [50, 50, 50, 25])
        if not isinstance(stats, list) or len(stats) != 4 or any(not isinstance(x, (int,float)) or x<0 or x>100 for x in stats): return self.send_json({'error': 'Неверные показатели'}, 400)
        # Use numeric fields only for gameplay context; never trust arbitrary client strings in an AI prompt.
        weather = body.get('weather') if isinstance(body.get('weather'), dict) else {}
        economy = body.get('economy') if isinstance(body.get('economy'), dict) else {}
        def clean_number(value, low, high):
            try:
                v = float(value)
                if not (low <= v <= high): return None
                return round(v, 2)
            except (ValueError, TypeError): return None
        temp = clean_number(weather.get('temperature'), -80, 70)
        rain = clean_number(weather.get('precipitation'), 0, 1000)
        inflation = clean_number(economy.get('inflation'), -100, 500)
        population = clean_number(body.get('population'), 1000, 2000000000)
        day = body.get('day') if type(body.get('day')) is int and 1 <= body.get('day') <= 20 else 1
        prompt = ('Придумай смешной абсурдный закон для сатирической игры на русском языке. Страна: '+COUNTRIES[code][0]+'. Игровой день: '+str(day)+'. Показатели бюджет/счастье/порядок/абсурд: '+str(stats)+'. Реальные данные (если указаны): температура '+str(temp)+' °C, осадки '+str(rain)+' мм, инфляция '+str(inflation)+'%, население '+str(population)+'. Привяжи закон к одному из доступных реальных факторов, например борьба с жарой, дороговизной или масштабом мегаполиса; избегай сухого пересказа цифр. Верни JSON с title (до 65 символов), description (до 220), author (до 50), effects (массив 4 целых чисел от -20 до 20). Без политики ненависти и насилия.')
        try:
            base_url = os.getenv('SHUPP_BASE_URL', 'https://api.shupp.ru/v1').rstrip('/') if os.getenv('SHUPP_API_KEY') else 'https://api.openai.com/v1'
            model = os.getenv('SHUPP_MODEL', 'shugg') if os.getenv('SHUPP_API_KEY') else os.getenv('OPENAI_MODEL', 'gpt-4o-mini')
            result = request_json(base_url + '/chat/completions', {'Authorization': 'Bearer '+key, 'Content-Type': 'application/json'}, {'model': model, 'messages': [{'role': 'system', 'content': 'Ты сценарист юмористической игры. Отвечай только JSON.'}, {'role': 'user', 'content': prompt}], 'max_tokens': 450})
            content = result['choices'][0]['message']['content'].strip()
            if content.startswith('```'):
                content = content.split('\n', 1)[-1].rsplit('```', 1)[0].strip()
            law = json.loads(content)
            if not all(isinstance(law.get(k), str) and 0<len(law[k])<=limit for k,limit in [('title',100),('description',350),('author',100)]): raise ValueError('Bad law')
            if not isinstance(law.get('effects'), list) or len(law['effects'])!=4 or any(type(x)!=int or abs(x)>20 for x in law['effects']): raise ValueError('Bad effects')
            return self.send_json(law)
        except Exception as exc:
            print('AI provider error:', type(exc).__name__, str(exc)[:180])
            return self.send_json({'error': 'ИИ временно недоступен. Проверьте модель, баланс и настройки ключа в Shupp.'}, 503)

if __name__ == '__main__':
    port = int(os.getenv('PORT', '8765'))
    print(f'Министерство глупостей: http://127.0.0.1:{port}')
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
