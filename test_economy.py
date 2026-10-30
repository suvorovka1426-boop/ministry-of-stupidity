"""Offline unit tests: synthetic signed notifications only; never charges real money."""
import hashlib,hmac,os, tempfile, unittest,urllib.parse
from pathlib import Path
from unittest.mock import patch
import payments

class EconomyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path_patch=patch.object(payments,'DB_PATH',Path(self.temp.name)/'pay.sqlite3')
        self.path_patch.start();self.addCleanup(self.path_patch.stop)
        self.env=patch.dict(os.environ,{
            'YOOMONEY_WALLET':'410011111111111',
            'YOOMONEY_NOTIFICATION_SECRET':'unit-test-secret-not-real',
            'YOOMONEY_ENABLE_LIVE':'YES',
            'PUBLIC_BASE_URL':'https://game.example',
            'PAYMENTS_PERSISTENCE_CONFIRMED':'YES',
            'PAYMENTS_DB_PATH': str(Path(self.temp.name)/'pay.sqlite3'),
        })
        self.env.start();self.addCleanup(self.env.stop)
        token=payments.create_player()['token']
        self.player=payments.require_player({'X-Player-Token':token})

    def synthetic_webhook(self,order_id,op):
        fields={'notification_type':'p2p-incoming','operation_id':str(op),
            'amount':'9.90','withdraw_amount':'10.00','currency':'643','codepro':'false',
            'unaccepted':'false','label':order_id,'datetime':'2026-10-30T10:00:00Z'}
        raw=urllib.parse.urlencode(sorted(fields.items())).encode()
        signature=hmac.new(os.environ['YOOMONEY_NOTIFICATION_SECRET'].encode(),raw,hashlib.sha256).hexdigest()
        return raw+b'&sign='+signature.encode()

    def test_four_locked_countries_and_cosmetics_catalog(self):
        self.assertEqual({k:v for k,v in payments.ALLOWED_ITEMS.items() if k.startswith('country:')},
            {'country:JP':75,'country:IL':90,'country:IT':105,'country:KP':120})
        self.assertEqual(payments.COINS_PER_PURCHASE,120)
        self.assertEqual(payments.AMOUNT_KOPEKS,1000)

    def test_rejects_fake_webhook_no_coins(self):
        checkout=payments.create_checkout(self.player)
        self.assertEqual(checkout['amount_cents'],1000)
        with self.assertRaises(payments.PaymentError):
            payments.receive_webhook(self.synthetic_webhook(checkout['order_id'],42).replace(b'sign=',b'sign=bad'))
        self.assertEqual(payments.inventory(self.player)['coins_balance'],0)
        with self.assertRaises(payments.PaymentError): payments.redeem(self.player)

    def test_verified_payment_redeem_and_country_unlock(self):
        order=payments.create_checkout(self.player)
        payload=self.synthetic_webhook(order['order_id'],4200)
        self.assertTrue(payments.receive_webhook(payload)['ok'])
        self.assertTrue(payments.receive_webhook(payload)['duplicate'])
        self.assertEqual(payments.inventory(self.player)['quantity'],1)
        self.assertEqual(payments.inventory(self.player)['coins_balance'],0)
        result=payments.redeem(self.player)
        self.assertEqual(result['coins_balance'],120)
        self.assertEqual(result['quantity'],0)
        with self.assertRaises(payments.PaymentError):payments.redeem(self.player)
        result=payments.spend_coins(self.player,'country:JP',75)
        self.assertEqual(result['coins_balance'],45)
        self.assertIn('country:JP',payments.inventory(self.player)['entitlements'])
        with self.assertRaises(payments.PaymentError):payments.spend_coins(self.player,'country:JP',1)
        with self.assertRaises(payments.PaymentError):payments.spend_coins(self.player,'skin:cosmic',115)

    def test_invalid_catalog_amount_and_webhook_replay(self):
        order1=payments.create_checkout(self.player)
        order2=payments.create_checkout(self.player)
        payload1=self.synthetic_webhook(order1['order_id'],7812)
        self.assertTrue(payments.receive_webhook(payload1)['ok'])
        with self.assertRaises(payments.PaymentError):payments.receive_webhook(self.synthetic_webhook(order2['order_id'],7812))
        payments.redeem(self.player)
        for item,amount in [('country:XX',1),('skin:noir',36),('country:JP',0),('country:JP',True)]:
            with self.assertRaises(payments.PaymentError):payments.spend_coins(self.player,item,amount)

    def test_real_money_off_by_default(self):
        with patch.dict(os.environ, {'YOOMONEY_ENABLE_LIVE':'NO'}):
            self.assertFalse(payments.configured())
            with self.assertRaises(payments.PaymentError):payments.create_checkout(self.player)

if __name__=='__main__': unittest.main()
