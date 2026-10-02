"""Zork I melee tables and troll combat, adapted to the Python world.

Reference: historicalsource/zork1 1actions.zil, MELEE and TROLL-FCN.
The original Zork license is in ZORK_LICENSE.txt.
"""
MISS, OUT, KILL, LIGHT, HEAVY, STAGGER, DISARM = range(7)
DEF1 = [MISS]*4 + [STAGGER]*2 + [OUT]*2 + [KILL]*5
DEF2A = [MISS]*5 + [STAGGER]*2 + [LIGHT]*2 + [OUT]
DEF2B = [MISS]*3 + [STAGGER]*2 + [LIGHT]*3 + [OUT] + [KILL]*3
DEF3A = [MISS]*5 + [STAGGER]*2 + [LIGHT]*2 + [HEAVY]*2
DEF3B = [MISS]*3 + [STAGGER]*2 + [LIGHT]*3 + [HEAVY]*3
DEF3C = [MISS] + [STAGGER]*2 + [LIGHT]*4 + [HEAVY]*3


def blow_table(attacker, defender):
    if defender == 1:
        start = (min(attacker, 3)-1)*2
        return DEF1[start:start+9]
    if defender == 2:
        attacker = min(attacker, 4)
        if attacker == 1: return DEF2A[:9]
        start=(attacker-2)*2
        return DEF2B[start:start+9]
    difference=max(-2,min(2,attacker-defender))
    table,offset={-2:(DEF3A,0),-1:(DEF3A,2),0:(DEF3B,0),1:(DEF3B,2),2:(DEF3C,0)}[difference]
    return table[offset:offset+9]


class CombatWorld:
    def combat_reset(self):
        self.troll_strength=2
        self.troll_staggered=False
        self.troll_wake_chance=0
        self.player_wounds=0
        self.player_staggered=False
        self.player_unconscious=0
        self.heal_turn=None
        self.dead=False
        self.combat_events=[]
        self._turn_pending=False
        self.max_weight=100

    def combat_status(self):
        base=2 + max(0,self._score())//70
        strength=max(0,base-self.player_wounds) if not self.dead else 0
        health='dead' if self.dead else 'unconscious' if self.player_unconscious else 'healthy' if not self.player_wounds else 'wounded'
        return {'health':health,'strength':strength,'max_strength':base,'wounds':self.player_wounds,'staggered':self.player_staggered,'game_over':self.dead}

    def roll_blow(self, attacker, defender):
        return self._random.choice(blow_table(max(1,attacker),max(1,defender)))

    def _dead_result(self):
        if "ADVENTURE-COMPLETE" in self.flags:
            return {'success':False,'error':'Your adventure is complete. Reset to play again.',**self.look()}
        if self.dead:
            return {'success':False,'error':'You have died. Restore a saved game or reset to begin again.',**self.look()}
        if self.player_unconscious:
            self._tick()
            return {'success':False,'error':'You are unconscious and cannot act.',**self.look()}
        return None

    def attack_troll(self, item=''):
        if self.troll_strength == 0:
            raise ValueError('The troll is already gone.')
        weapon=self._find_item(item) if item else next((i for i in ('SWORD','KNIFE','AXE','RUSTY-KNIFE') if self._has(i)),None)
        if weapon not in ('SWORD','KNIFE','AXE','RUSTY-KNIFE') or not self._has(weapon):
            raise ValueError('You need an accessible weapon to attack the troll.')
        if self.player_staggered:
            self.player_staggered=False
            return 'Still staggering from the last blow, you cannot land an effective attack.'
        if self.troll_strength < 0 or self.item_locations.get('AXE') != 'TROLL':
            result=KILL
        else:
            defense=max(1,self.troll_strength-(1 if weapon=='SWORD' else 0))
            result=self.roll_blow(self.combat_status()['strength'],defense)
            if result==STAGGER and self._random.randint(1,100)<=25:
                result=DISARM
        if result==MISS:
            return 'Your weapon misses the troll.'
        if result==STAGGER:
            self.troll_staggered=True
            return 'Your blow sends the troll staggering backward.'
        if result==DISARM:
            self.item_locations['AXE']='TROLL-ROOM'
            return 'You knock the bloody axe out of the troll?s hands.'
        if result==OUT:
            self.troll_strength=-abs(self.troll_strength)
            self.troll_wake_chance=0
            self.item_locations['AXE']='TROLL-ROOM'
            self.flags.add('TROLL-FLAG')
            return 'The troll collapses unconscious. The passages are open.'
        if result in (LIGHT,HEAVY):
            self.troll_strength=max(0,self.troll_strength-(1 if result==LIGHT else 2))
            if self.troll_strength:
                return 'You wound the troll.' if result==LIGHT else 'You seriously wound the troll.'
        self.troll_strength=0
        self.flags.add('TROLL-FLAG')
        self.item_locations['AXE']='TROLL-ROOM'
        return 'The troll dies, vanishing in a cloud of dark fog. His axe remains, and the passages are open.'

    def finish_combat_turn(self):
        if not self.puzzles or self.dead:
            return
        if self.player_wounds and self.heal_turn is not None and self.turns>=self.heal_turn:
            self.player_wounds-=1
            self.max_weight=min(100,self.max_weight+10)
            self.heal_turn=self.turns+30 if self.player_wounds else None
            self.combat_events.append('Your wounds begin to heal.' if self.player_wounds else 'You have recovered from your wounds.')
        if self.position!='TROLL-ROOM' or not self.troll_strength:
            self.player_staggered=False
            return
        if self.troll_strength<0:
            if self.troll_wake_chance and self._random.randint(1,100)<=self.troll_wake_chance:
                self.troll_strength=abs(self.troll_strength)
                self.flags.discard('TROLL-FLAG')
                self.combat_events.append('The troll wakes and resumes his fighting stance.')
            else:
                self.troll_wake_chance=min(100,self.troll_wake_chance+25)
            return
        if self.troll_staggered:
            self.troll_staggered=False
            self.combat_events.append('The troll regains his balance instead of attacking.')
            return
        if self.item_locations.get('AXE')!='TROLL':
            if self.item_locations.get('AXE')=='TROLL-ROOM':
                self.item_locations['AXE']='TROLL'
                self.combat_events.append('The troll retrieves his axe.')
            else:
                self.combat_events.append('The disarmed troll cowers before you.')
            return
        result=self.roll_blow(self.troll_strength,self.combat_status()['strength'])
        if self.player_unconscious:
            self.player_unconscious-=1
            if result!=STAGGER: result=KILL
        weapon=next((i for i in ('SWORD','KNIFE','AXE','RUSTY-KNIFE') if self.item_locations.get(i)=='inventory'),None)
        if result==STAGGER and weapon and self._random.randint(1,100)<=25:
            result=DISARM
        if result==MISS:
            self.combat_events.append('The troll swings his axe and misses you.')
        elif result==STAGGER:
            self.player_staggered=True
            self.combat_events.append('The troll?s blow leaves you staggering.')
        elif result==DISARM:
            self.item_locations[weapon]='TROLL-ROOM'
            self.combat_events.append('The troll knocks your weapon to the floor.')
        elif result==OUT:
            self.player_unconscious=self._random.randint(2,4)
            self.combat_events.append('The troll knocks you unconscious.')
        else:
            if result==KILL:
                self.dead=True
            else:
                damage=1 if result==LIGHT else 2
                self.player_wounds+=damage
                self.max_weight=max(50,self.max_weight-damage*10)
                self.heal_turn=self.turns+30
                self.dead=self.combat_status()['strength']<=0
                self.combat_events.append('The troll wounds you.' if damage==1 else 'The troll seriously wounds you.')
            if self.dead:
                self.combat_events.append('The troll?s final blow kills you. Restore a save or begin again.')
