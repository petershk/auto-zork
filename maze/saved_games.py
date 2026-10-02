"""Versioned JSON world saves and observation-only agent handoffs."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from maze import Maze
from context_memory import ContextMemory

SAVE_DIR = Path(__file__).resolve().with_name("saves")
FIELDS = ('position','moves_made','visited','item_locations','item_open','revision','turns',
          'discovered_items','flags','dig_count','bell_turn','hidden_items','endgame_announcement',
          'hints_used','thief_position','thief_events','troll_strength','troll_staggered',
          'troll_wake_chance','player_wounds','player_staggered','player_unconscious','heal_turn',
          'dead','combat_events','max_weight','ritual_turn','bell_cool_turn','dam_event','dam_turn')
# Fields added after saves were first written; older saves load with these defaults.
NEWER_FIELDS = {'ritual_turn': None, 'bell_cool_turn': None, 'dam_event': None, 'dam_turn': None}
SETS = {'visited','discovered_items','flags','hidden_items'}
# Combat tracks the troll's axe in item_locations although it is not a defined item.
COMBAT_ITEMS = {'AXE'}
COMBAT_PLACES = {'TROLL'}


def world_snapshot(world):
    world.look()  # Finish a pending action before taking an authoritative snapshot.
    result = {key: sorted(getattr(world,key)) if key in SETS else getattr(world,key) for key in FIELDS}
    return {'puzzles':world.puzzles,'fields':result,'random_state':world._random.getstate()}


def tuples(value):
    return tuple(tuples(v) for v in value) if isinstance(value,list) else value


def restore_world(data):
    if not isinstance(data,dict) or type(data.get('puzzles')) is not bool or not isinstance(data.get('fields'),dict):
        raise ValueError('Invalid saved world.')
    world=Maze(puzzles=data['puzzles'])
    fields={**NEWER_FIELDS,**data['fields']}
    if set(fields)!=set(FIELDS): raise ValueError('Save fields do not match this game version.')
    rooms=set(world.rooms); items=set(world.items)
    if fields['position'] not in rooms: raise ValueError('Invalid saved room.')
    for key in SETS:
        values=fields[key]
        if not isinstance(values,list) or not all(isinstance(v,str) for v in values): raise ValueError('Invalid saved sets.')
        allowed=rooms if key=='visited' else items if key in ('hidden_items','discovered_items') else None
        if allowed is not None and not set(values)<=allowed: raise ValueError('Unknown saved room or item.')
    locations=fields['item_locations']; opened=fields['item_open']
    if (not isinstance(locations,dict) or not items<=set(locations)<=items|COMBAT_ITEMS
            or not isinstance(opened,dict) or set(opened)!=items):
        raise ValueError('Save item definitions do not match this game.')
    roots=rooms|items|{'inventory','offstage'}|COMBAT_PLACES|set(world.item_locations.values())
    if not all(isinstance(v,str) and v in roots for v in locations.values()) or not all(type(v) is bool for v in opened.values()):
        raise ValueError('Invalid saved item locations or containers.')
    for item in items:
        seen={item}; location=locations[item]
        while location in items:
            if location in seen: raise ValueError('Saved containers form a cycle.')
            seen.add(location); location=locations[location]
    for key in ('moves_made','revision','turns','dig_count','hints_used','troll_wake_chance','player_wounds','player_unconscious','max_weight'):
        if type(fields[key]) is not int or not 0<=fields[key]<=10_000_000: raise ValueError('Invalid saved counters.')
    if type(fields['troll_strength']) is not int or not -2<=fields['troll_strength']<=2: raise ValueError('Invalid troll strength.')
    for key in ('troll_staggered','player_staggered','dead'):
        if type(fields[key]) is not bool: raise ValueError('Invalid saved combat state.')
    if fields['dam_event'] not in (None,'drain','fill'): raise ValueError('Invalid saved dam state.')
    for key in ('bell_turn','heal_turn','ritual_turn','bell_cool_turn','dam_turn'):
        if fields[key] is not None and (type(fields[key]) is not int or fields[key]<0): raise ValueError('Invalid saved timer.')
    if fields['thief_position'] is not None and fields['thief_position'] not in rooms: raise ValueError('Invalid thief room.')
    for key in ('thief_events','combat_events'):
        if not isinstance(fields[key],list) or not all(isinstance(v,str) for v in fields[key]): raise ValueError('Invalid saved events.')
    if fields['endgame_announcement'] is not None and not isinstance(fields['endgame_announcement'],str): raise ValueError('Invalid announcement.')
    try: world._random.setstate(tuples(data['random_state']))
    except (KeyError,TypeError,ValueError,OverflowError) as exc: raise ValueError('Invalid saved random state.') from exc
    for key,value in fields.items(): setattr(world,key,set(value) if key in SETS else value)
    world._turn_pending=False
    return world


def slot_path(name):
    if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,47}',name):
        raise ValueError('Use a save name of 1?48 letters, numbers, underscores or hyphens.')
    if SAVE_DIR.is_symlink() or (SAVE_DIR/name).is_symlink(): raise ValueError('Linked save folders are not supported.')
    return SAVE_DIR/name


def atomic_write(path,text):
    if path.is_symlink(): raise ValueError('Linked save files are not supported.')
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as handle:
            temporary=Path(handle.name); handle.write(text)
        os.replace(temporary,path)
    finally:
        if temporary is not None and temporary.exists(): temporary.unlink()


def save(name,world,memory=None):
    folder=slot_path(name); folder.mkdir(parents=True,exist_ok=True)
    observed=ContextMemory.restore(memory) if memory else ContextMemory('Explore the Great Underground Empire and discover treasures.')
    observed.observe('save_observation',{},world.look())
    record={'version':1,'saved_at':datetime.now(timezone.utc).isoformat(),'world':world_snapshot(world),'memory':observed.snapshot()}
    markdown='# Continue this adventure\n\nThese are observed game facts and the agent?s recent action notes. `state.json` is the authoritative save.\n\n'+observed.input()[0]['content']+'\n'
    atomic_write(folder/'continue.md',markdown)
    atomic_write(folder/'state.json',json.dumps(record,indent=2))
    return {'name':name,'saved_at':record['saved_at'],'room':world.rooms[world.position]['name'],'score':world._score()}


def load(name):
    path=slot_path(name)/'state.json'
    if path.is_symlink(): raise ValueError('Linked save files are not supported.')
    if path.stat().st_size>2_000_000: raise ValueError('Save file is too large.')
    try: record=json.loads(path.read_text(encoding='utf-8'))
    except (UnicodeError,json.JSONDecodeError) as exc: raise ValueError('Cannot read this save file.') from exc
    if not isinstance(record,dict) or record.get('version')!=1: raise ValueError('Unsupported save version.')
    world=restore_world(record.get('world'))
    memory=ContextMemory.restore(record.get('memory')).snapshot()
    return world,memory


def list_saves():
    if not SAVE_DIR.exists(): return []
    if SAVE_DIR.is_symlink(): raise ValueError('Linked save folders are not supported.')
    records=[]
    for folder in sorted(SAVE_DIR.iterdir()):
        if not folder.is_dir() or folder.is_symlink(): continue
        try:
            world,_=load(folder.name)
            records.append({'name':folder.name,'room':world.rooms[world.position]['name'],'score':world._score()})
        except (OSError,ValueError,TypeError,KeyError) as exc:
            # Show unreadable saves, with the reason, instead of silently hiding them.
            records.append({'name':folder.name,'error':str(exc) or type(exc).__name__})
    return records
