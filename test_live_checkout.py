"""Non-live safety checks. No financial operations."""
import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import payments
class LiveSafety(unittest.TestCase):
    def test_off_without_absolute_storage(self):
        with patch.dict(os.environ,{'YOOMONEY_WALLET':'410011111111111', 'YOOMONEY_NOTIFICATION_SECRET':'temporary-test-secret', 'YOOMONEY_ENABLE_LIVE':'YES','PUBLIC_BASE_URL':'https://example.org','PAYMENTS_PERSISTENCE_CONFIRMED':'YES','PAYMENTS_DB_PATH':'payments.sqlite3'}):
            self.assertFalse(payments.configured())
    def test_disabled_if_http(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ,{'YOOMONEY_WALLET':'410011111111111','YOOMONEY_NOTIFICATION_SECRET':'temporary-test-secret','YOOMONEY_ENABLE_LIVE':'YES','PUBLIC_BASE_URL':'http://example.org','PAYMENTS_PERSISTENCE_CONFIRMED':'YES','PAYMENTS_DB_PATH':str(Path(d)/'payments.sqlite3')}):
                self.assertFalse(payments.configured())
    def test_valid_config_on_durable_path(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(os.environ,{'YOOMONEY_WALLET':'410011111111111','YOOMONEY_NOTIFICATION_SECRET':'temporary-test-secret','YOOMONEY_ENABLE_LIVE':'YES','PUBLIC_BASE_URL':'https://example.org','PAYMENTS_PERSISTENCE_CONFIRMED':'YES','PAYMENTS_DB_PATH':str(Path(d)/'payments.sqlite3')}):
                self.assertTrue(payments.configured())
if __name__=='__main__': unittest.main()
