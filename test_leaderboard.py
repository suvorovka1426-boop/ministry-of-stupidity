"""SQLite leaderboard unit tests, no external services."""
import os, tempfile, unittest
from unittest.mock import patch
import leaderboard

class LeaderboardTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'LEADERBOARD_DB_PATH': self.temp.name+'/scores.sqlite3','SUPABASE_URL':'','SUPABASE_SERVICE_ROLE_KEY':''})
        self.env.start()
    def tearDown(self):
        self.env.stop();self.temp.cleanup()
    def test_empty_local(self):
        result=leaderboard.top()
        self.assertEqual(result['source'],'local')
        self.assertEqual(result['entries'],[])
    def test_best_run_case_insensitive(self):
        self.assertTrue(leaderboard.record('Бро','RU',4,400)['saved'])
        self.assertFalse(leaderboard.record('бро','RU',3,150)['saved'])
        self.assertTrue(leaderboard.record('Бро','RU',10,1100)['saved'])
        rows=leaderboard.top('RU')['entries']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['score'],1100)
    def test_country_and_order(self):
        leaderboard.record('Саша','RU',12,700)
        leaderboard.record('Игорь','US',19,1200)
        leaderboard.record('Бро','RU',15,950)
        self.assertEqual([r['player'] for r in leaderboard.top()['entries']],['Игорь','Бро','Саша'])
        self.assertEqual([r['player'] for r in leaderboard.top('RU')['entries']],['Бро','Саша'])
    def test_persists_across_connection(self):
        leaderboard.record('Игрок','CN',8,750)
        self.assertEqual(leaderboard.top('CN')['entries'][0]['player'],'Игрок')
        self.assertEqual(leaderboard.top('JP')['entries'],[])
    def test_tied_score_longer_run_wins(self):
        leaderboard.record('One','JP',7,500)
        leaderboard.record('One','JP',9,500)
        self.assertEqual(leaderboard.top('JP')['entries'][0]['days'],9)

if __name__=='__main__': unittest.main()
