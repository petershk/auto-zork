"""Zork I V-SCORE boundaries, independently checked against original source."""
import unittest
from unittest.mock import patch
import maze
import web_app

class RankingTests(unittest.TestCase):
    def test_every_score_and_threshold_matches_original_rank(self):
        world=maze.Maze()
        ranges=[(0,25,"Beginner"),(26,50,"Amateur Adventurer"),(51,100,"Novice Adventurer"),(101,200,"Junior Adventurer"),(201,300,"Adventurer"),(301,330,"Master"),(331,349,"Wizard"),(350,350,"Master Adventurer")]
        for minimum,maximum,rank in ranges:
            for score in range(minimum,maximum+1):
                with self.subTest(score=score),patch.object(world,'_score',return_value=score):
                    self.assertEqual(world.score()['rank'],rank)
                    self.assertEqual(world.look()['rank'],rank)

    def test_http_rank_changes_with_score_and_reset(self):
        world=maze.Maze()
        with patch('maze._maze',world):
            client=web_app.app.test_client()
            for score,rank in ((350,'Master Adventurer'),(349,'Wizard'),(330,'Master'),(0,'Beginner')):
                with patch.object(world,'_score',return_value=score):
                    self.assertEqual(client.get('/api/state').json['rank'],rank)
            world.reset()
            self.assertEqual(client.get('/api/state').json['rank'],'Beginner')
            page=client.get('/').get_data(as_text=True)
            self.assertIn('id="player-rank"',page)

if __name__=='__main__': unittest.main()
