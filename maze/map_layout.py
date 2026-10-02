"""Level-aware schematic coordinates; passage labels remain authoritative.

Rooms are first split into levels (up/down change level, everything else stays
on it), each level is placed from compass directions, relaxed so contradictory
loops settle into a readable compromise, and the levels are stacked in bands.
"""
from collections import deque

OFFSETS = {"north": (0, -1), "south": (0, 1), "east": (1, 0),
           "west": (-1, 0), "northeast": (1, -1), "northwest": (-1, -1),
           "southeast": (1, 1), "southwest": (-1, 1),
           "in": (1, 0), "out": (-1, 0)}
COMPASS = frozenset(d for d in OFFSETS if d not in ("in", "out"))
LEVEL_STEP = {"up": 1, "down": -1}

PITCH_X, PITCH_Y = 230, 130        # ideal distance between neighbouring rooms
MIN_X, MIN_Y = 170, 76             # closest two room boxes may sit
BAND_GAP, BAND_PAD = 70, 40


def assign_levels(rooms, start):
    """Level per room: up/down move one level, all other exits stay put."""
    levels = {}
    for root in [start, *rooms]:
        if root in levels:
            continue
        levels[root] = 0 if root == start or not levels else _free_level(levels)
        queue = deque([root])
        while queue:
            source = queue.popleft()
            for direction, target in rooms[source]["exits"].items():
                if target in levels or target not in rooms:
                    continue
                levels[target] = levels[source] + LEVEL_STEP.get(direction, 0)
                queue.append(target)
    return levels


def _free_level(levels):
    # Rooms unreachable from the start get their own level above everything.
    return max(levels.values()) + 1


def _initial_positions(members, rooms, levels, level):
    """Grid placement by spanning traversal, one origin per disconnected piece."""
    positions, occupied = {}, set()
    for root in members:
        if root in positions:
            continue
        origin = (max((p[0] for p in occupied), default=-3) + 3, 0)
        positions[root] = origin
        occupied.add(origin)
        queue = deque([root])
        while queue:
            source = queue.popleft()
            x, y = positions[source]
            for direction, target in rooms[source]["exits"].items():
                if target in positions or levels.get(target) != level:
                    continue
                dx, dy = OFFSETS.get(direction, (1, 0))
                desired = (x + dx, y + dy)
                point, radius = desired, 0
                while point in occupied:
                    radius += 1
                    candidates = [(desired[0] + a, desired[1] + b)
                                  for a in range(-radius, radius + 1)
                                  for b in range(-radius, radius + 1)
                                  if max(abs(a), abs(b)) == radius]
                    point = min((p for p in candidates if p not in occupied),
                                key=lambda p: ((p[0]-x)*dy-(p[1]-y)*dx)**2
                                + ((p[0]-x)*dx+(p[1]-y)*dy <= 0)*100,
                                default=desired)
                positions[target] = point
                occupied.add(point)
                queue.append(target)
    return positions


def _separate(order, pos):
    """Push overlapping room boxes apart along the axis they overlap least."""
    moved = False
    for i, a in enumerate(order):
        for b in order[i+1:]:
            dx, dy = pos[b][0] - pos[a][0], pos[b][1] - pos[a][1]
            ox, oy = MIN_X - abs(dx), MIN_Y - abs(dy)
            if ox <= 0 or oy <= 0:
                continue
            moved = True
            if ox / MIN_X < oy / MIN_Y:
                shift = ox / 2 * (1 if dx >= 0 else -1)
                pos[a][0] -= shift; pos[b][0] += shift
            else:
                shift = oy / 2 * (1 if dy >= 0 else -1)
                pos[a][1] -= shift; pos[b][1] += shift
    return moved


def _relax(order, rooms, grid, iterations=250):
    """Spring pass: pull linked rooms toward their compass offset, keep boxes apart."""
    pos = {k: [grid[k][0] * PITCH_X, grid[k][1] * PITCH_Y] for k in order}
    springs = []
    for source in order:
        for direction, target in rooms[source]["exits"].items():
            if target in pos and target != source and direction in OFFSETS:
                dx, dy = OFFSETS[direction]
                springs.append((source, target, dx * PITCH_X, dy * PITCH_Y,
                                1.0 if direction in COMPASS else 0.3))
    for step in range(iterations):
        force = {k: [0.0, 0.0] for k in order}
        for a, b, wx, wy, weight in springs:
            ex = (pos[b][0] - pos[a][0] - wx) * weight * 0.08
            ey = (pos[b][1] - pos[a][1] - wy) * weight * 0.08
            force[a][0] += ex; force[a][1] += ey
            force[b][0] -= ex; force[b][1] -= ey
        for k in order:
            pos[k][0] += force[k][0]; pos[k][1] += force[k][1]
        _separate(order, pos)
    for _ in range(200):
        if not _separate(order, pos):
            break
    return pos


def layout_with_levels(rooms, start):
    levels = assign_levels(rooms, start)
    bands, coordinates = [], {}
    cursor, width = 0, 0
    for level in sorted(set(levels.values()), reverse=True):
        members = [k for k in rooms if levels[k] == level]
        grid = _initial_positions(members, rooms, levels, level)
        pos = _relax(members, rooms, grid)
        left = min(x for x, y in pos.values())
        top = min(y for x, y in pos.values())
        body = max(y for x, y in pos.values()) - top + 48
        for key, (x, y) in pos.items():
            coordinates[key] = (round(40 + x - left), round(cursor + BAND_PAD + y - top))
        band_height = body + 2 * BAND_PAD
        bands.append({"level": level, "name": level_name(level), "y": cursor,
                      "height": band_height})
        width = max(width, max(coordinates[k][0] for k in members) + 176)
        cursor += band_height + BAND_GAP
    return coordinates, width, cursor - BAND_GAP, bands


def level_name(level):
    if level == 0:
        return "Ground level"
    if level > 0:
        return "Above ground" if level == 1 else f"Above ground +{level}"
    return "Underground" if level == -1 else f"Underground depth {-level}"


def layout_rooms(rooms, start):
    coordinates, width, height, _ = layout_with_levels(rooms, start)
    return coordinates, width, height
