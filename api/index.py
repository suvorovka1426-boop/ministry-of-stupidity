"""Vercel adapter for Ministry of Stupidity.
API requests use the original Python business logic.
Real-money payment endpoints are intentionally disabled until persistent payment
storage and merchant policy verification are implemented for serverless hosting.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from server import Handler

class handler(Handler):
    def do_GET(self):
        from urllib.parse import urlsplit
        path = urlsplit(self.path).path
        if path.startswith('/api/payments/'):
            return self.send_json({'enabled': False, 'configured': False,
                                   'error': 'Донаты временно недоступны до подключения постоянной базы покупок.'}, 503)
        return super().do_GET()

    def do_POST(self):
        from urllib.parse import urlsplit
        path = urlsplit(self.path).path
        if path.startswith('/api/payments/'):
            return self.send_json({'error': 'Донаты пока недоступны на бесплатном хостинге.'}, 503)
        return super().do_POST()
