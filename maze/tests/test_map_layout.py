import unittest
from maze import Maze
from map_layout import layout_rooms

class MapLayoutTests(unittest.TestCase):
    def test_compass_relationships(self):
        rooms = {'a': {'exits': {'north': 'b', 'east': 'c'}}, 'b': {'exits': {'south':'a'}}, 'c': {'exits': {'west':'a'}}}
        coordinates, width, height = layout_rooms(rooms, 'a')
        self.assertLess(coordinates['b'][1], coordinates['a'][1])
        self.assertEqual(coordinates['b'][0], coordinates['a'][0])
        self.assertGreater(coordinates['c'][0], coordinates['a'][0])
        self.assertEqual(coordinates['c'][1], coordinates['a'][1])

    def test_world_has_unique_bounded_deterministic_positions(self):
        world = Maze()
        positions, width, height = layout_rooms(world.rooms, world.start)
        self.assertEqual(len(positions), len(world.rooms))
        self.assertEqual(len(set(positions.values())), len(positions))
        self.assertEqual(positions, world.coordinates)
        self.assertTrue(all(0 <= x < width and 0 <= y < height for x,y in positions.values()))


class LevelTests(unittest.TestCase):
    def test_up_and_down_change_level_instead_of_going_diagonal(self):
        from map_layout import assign_levels, layout_with_levels
        rooms = {'a': {'exits': {'down': 'b'}}, 'b': {'exits': {'up': 'a', 'east': 'c'}}, 'c': {'exits': {'west': 'b'}}}
        self.assertEqual(assign_levels(rooms, 'a'), {'a': 0, 'b': -1, 'c': -1})
        coordinates, _, _, bands = layout_with_levels(rooms, 'a')
        self.assertEqual([b['level'] for b in bands], [0, -1])
        self.assertLess(coordinates['a'][1], coordinates['b'][1])
        self.assertEqual(coordinates['b'][1], coordinates['c'][1])

    def test_world_is_split_into_stacked_bands(self):
        world = Maze()
        bands = world.levels
        self.assertGreater(len(bands), 1)
        for upper, lower in zip(bands, bands[1:]):
            self.assertLess(upper['y'] + upper['height'], lower['y'])
        self.assertLess(world.coordinates['LIVING-ROOM'][1], world.coordinates['CELLAR'][1])

    def test_rooms_never_overlap(self):
        from map_layout import MIN_X, MIN_Y
        pts = list(Maze().coordinates.values())
        for i, a in enumerate(pts):
            for b in pts[i+1:]:
                self.assertTrue(abs(a[0]-b[0]) >= MIN_X - 1 or abs(a[1]-b[1]) >= MIN_Y - 1)
