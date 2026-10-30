"""Small offline smoke tests for the release candidate (no money or external API)."""
import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import payments
from server import Handler

class ReleaseTests(unittest.TestCase):
    def test_real_payments_off_by_default(self):
        with patch.dict(os.environ, {'YOOMONEY_WALLET':'410011111111111', 'YOOMONEY_NOTIFICATION_SECRET':'secret-long-enough', 'YOOMONEY_ENABLE_LIVE':'YES', 'PUBLIC_BASE_URL':'https://example.org', 'PAYMENTS_PERSISTENCE_CONFIRMED':'NO'}):
            self.assertFalse(payments.configured())
    def test_payment_persistence_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.dict(os.environ, {'YOOMONEY_WALLET':'410011111111111', 'YOOMONEY_NOTIFICATION_SECRET':'secret-long-enough', 'YOOMONEY_ENABLE_LIVE':'YES', 'PUBLIC_BASE_URL':'https://example.org', 'PAYMENTS_PERSISTENCE_CONFIRMED':'YES', 'PAYMENTS_DB_PATH':str(Path(temp)/'payments.sqlite3')}):
                self.assertTrue(payments.configured())
    def test_player_and_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(payments,'DB_PATH',Path(temp)/'payments.sqlite3'):
                token=payments.create_player()['token']
                player=payments.require_player({'X-Player-Token':token})
                self.assertEqual(payments.inventory(player)['quantity'],0)
                with self.assertRaises(payments.PaymentError):
                    payments.redeem(player)
    def test_health_route_exists(self):
        self.assertIn("path.path == '/health'", Path(__file__).with_name('server.py').read_text(encoding='utf-8'))

if __name__=='__main__': unittest.main()
