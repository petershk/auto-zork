import unittest
from unittest.mock import patch
from maze import Maze
import web_app

class ThiefRoamingTests(unittest.TestCase):
    def test_movement_uses_passages_on_game_turns_only(self):
        w=Maze()
        for _ in range(3): w.wait()
        self.assertEqual(w.thief_position,'TREASURE-ROOM')
        w.look(); w.score(); w.hint()
        self.assertEqual(w.thief_position,'TREASURE-ROOM')
        w.wait()
        self.assertEqual(w.thief_position,'CYCLOPS-ROOM')
        w._random.seed(123)
        visited={w.thief_position}
        for _ in range(200):
            old=w.thief_position
            w.wait()
            if w.thief_position!=old:
                self.assertIn(w.thief_position,w.rooms[old]['exits'].values())
            self.assertNotIn(w.rooms[w.thief_position]['area'],('House','Forest And Outside Of House','River Area'))
            visited.add(w.thief_position)
        self.assertGreater(len(visited),3)

    def test_encounters_are_local_and_visible(self):
        w=Maze(); w.position='MAZE-1'; w.thief_position='MAZE-2'; w.turns=3
        with patch.object(w._random,'choice',return_value='MAZE-1'):
            result=w.wait()
        self.assertIn('thief',result['nearby_features'])
        self.assertIn('thief',result['description'])
        self.assertIn('slips into',result['encounter_events'][0])
        self.assertIn('slips into',web_app._game_text(result))
        w.thief_position='TREASURE-ROOM'
        self.assertNotIn('thief',w.look()['nearby_features'])
        self.assertFalse(w.interact('give','thief','EGG')['success'])

    def test_egg_can_be_opened_during_roaming_encounter(self):
        w=Maze(); w.position='MAZE-1'; w.thief_position='MAZE-1'; w.turns=3
        w.item_locations['EGG']='inventory'
        result=w.interact('give','thief','EGG')
        self.assertTrue(result['success'],result.get('error'))
        self.assertTrue(w.item_open['EGG'])
        self.assertEqual(w.thief_position,'MAZE-1')

    def test_defeat_stops_roaming_and_reset_restores_thief(self):
        w=Maze(); w.position='MAZE-1'; w.thief_position='MAZE-1'
        self.assertFalse(w.interact('attack','thief')['success'])
        w.item_locations['SWORD']='inventory'
        result=w.interact('attack','thief')
        self.assertTrue(result['success'])
        self.assertNotIn('thief',result['nearby_features'])
        self.assertNotIn('suspicious thief',result['description'])
        for _ in range(12): w.wait()
        self.assertIsNone(w.thief_position)
        self.assertFalse(w.interact('give','thief','EGG')['success'])
        w.reset()
        self.assertEqual(w.thief_position,'TREASURE-ROOM')
        self.assertNotIn('thief-defeated',w.flags)

    def test_chalice_is_guarded_only_when_thief_is_present(self):
        w=Maze(); w.position='TREASURE-ROOM'
        self.assertFalse(w.take('CHALICE')['success'])
        w.thief_position='CYCLOPS-ROOM'
        self.assertTrue(w.take('CHALICE')['success'])

    def test_navigation_mode_does_not_spawn_thief(self):
        w=Maze(puzzles=False)
        for _ in range(12): w.wait()
        self.assertIsNone(w.thief_position)
        self.assertNotIn('thief',w.look()['nearby_features'])

if __name__=='__main__': unittest.main()
