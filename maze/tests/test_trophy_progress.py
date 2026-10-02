import unittest
from unittest.mock import patch
import maze
import web_app

class TrophyProgressTests(unittest.TestCase):
    def test_nested_deposit_and_removal_match_score(self):
        world=maze.Maze()
        empty=world.trophy_case_progress()
        self.assertEqual(empty['count'],0)
        treasure=next(i for i,item in world.items.items() if item['deposit_points']>0)
        world.item_locations['SANDWICH-BAG']='TROPHY-CASE'
        world.item_locations[treasure]='SANDWICH-BAG'
        stored=world.trophy_case_progress()
        self.assertEqual(stored['count'],1)
        self.assertEqual(stored['treasures'][0]['id'],treasure)
        self.assertEqual(stored['points'],world._score_parts()['treasures_in_case'])
        world.item_locations['SANDWICH-BAG']='inventory'
        removed=world.trophy_case_progress()
        self.assertEqual(removed['count'],0)
        self.assertEqual(removed['points'],0)
        self.assertEqual(removed['total'],empty['total'])

    def test_viewer_progress_is_passive_and_exposes_only_stored_names(self):
        world=maze.Maze()
        with patch('maze._maze',world):
            before=world.turns
            state=web_app.app.test_client().get('/api/state').json
            self.assertEqual(state['trophy_case']['treasures'],[])
            self.assertGreater(state['trophy_case']['total'],0)
            self.assertEqual(world.turns,before)
            self.assertNotIn('trophy_case',world.look())

if __name__=='__main__': unittest.main()
