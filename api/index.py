"""Vercel Python entrypoint. API uses same handler as local Python server.
Do not store payment orders in function-local files: Supabase is mandatory.
"""
import os
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('PAYMENTS_BACKEND','supabase')
from server import Handler

class handler(Handler):
    pass
