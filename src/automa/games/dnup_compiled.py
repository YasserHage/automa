import random
import copy
import itertools
from automa.core.game import Viewer

NAME = "dnup"
PLAYERS = ('automa', 'human')

# The 26 cards in the 2-player deck. Each tuple is (active_value, inactive_value)
# when orientation (rot) is 0. Card 0 carries the Star symbol.
CARDS = [
    (1, 5), (2, 5), (5, 2), (2, 10), (2, 9), (6, 7), (2, 6), (1, 6),
    (8, 1), (8, 5), (3, 8), (1, 7), (7, 5), (1, 9), (4, 6), (8, 4),
    (2, 4), (1, 7), (3, 7), (3, 6), (3, 9), (3, 4), (3, 5), (4, 7),
    (2, 4), (1, 6)
]

def _active_val(c_id, rot):
    return CARDS[c_id][rot]

def _get_card_desc(c_id, rot):
    active = CARDS[c_id][rot]
    inactive = CARDS[c_id][1 - rot]
    s = f"{active}/{inactive}"
    if c_id == 0:
        s += "*"
    return s

def setup_round(state, deal):
    state['hands'] = {p: [] for p in PLAYERS}
    state['table'] = {p: {'1': [], '2': []} for p in PLAYERS}
    state['discard_pile'] = []
    
    state['hands'][PLAYERS[0]] = deal[0:13]
    state['hands'][PLAYERS[1]] = deal[13:26]
    
    start_p = None
    for p in PLAYERS:
        for c in state['hands'][p]:
            if c['id'] == 0:
                start_p = p
                break
        if start_p:
            break
            
    state['to_play'] = start_p
    state['current_area'] = '2'

def initial_state(seed=None):
    rng = random.Random(seed)
    deals = []
    for _ in range(3):
        deck = list(range(26))
        rng.shuffle(deck)
        deal = [{'id': cid, 'rot': rng.choice([0, 1])} for cid in deck]
        deals.append(deal)
        
    state = {
        'to_play': '',
        'turn': 0,
        'ply': 0,
        'scores': {p: 0 for p in PLAYERS},
        'future_rounds': deals[1:],
        'hands': {},
        'table': {},
        'discard_pile': [],
        'current_area': ''
    }
    setup_round(state, deals[0])
    return state

def legal_actions(state):
    actions = []
    p = state['to_play']
    hand = state['hands'][p]
    
    if not hand:
        return []
        
    from collections import defaultdict
    by_val = defaultdict(list)
    for c in hand:
        by_val[_active_val(c['id'], c['rot'])].append(c['id'])
        
    table_sets = {}
    for player in PLAYERS:
        for area in ['1', '2']:
            cards = state['table'][player][area]
            if cards:
                table_sets[len(cards)] = _active_val(cards[0]['id'], cards[0]['rot'])
                
    # A. PLAY A SET
    for val, c_ids in by_val.items():
        for size in range(1, len(c_ids) + 1):
            if size in table_sets and val <= table_sets[size]:
                continue
            for combo in itertools.combinations(c_ids, size):
                combo_sorted = sorted(list(combo))
                labels = [_get_card_desc(cid, next(c['rot'] for c in hand if c['id'] == cid)) for cid in combo_sorted]
                label = f"Play set of value {val} into Area {state['current_area']} using: " + ", ".join(labels) + "."
                actions.append({
                    'type': 'play',
                    'cards': combo_sorted,
                    'val': val,
                    'label': label
                })
                
    # B. ADD TO OPPONENT SET
    opp = PLAYERS[1] if p == PLAYERS[0] else PLAYERS[0]
    for area in ['1', '2']:
        t_cards = state['table'][opp][area]
        if not t_cards:
            continue
        t_val = _active_val(t_cards[0]['id'], t_cards[0]['rot'])
        new_size = len(t_cards) + 1
        
        if t_val in by_val:
            conflict_fails = False
            if new_size in table_sets and table_sets[new_size] >= t_val:
                conflict_fails = True
                
            if not conflict_fails:
                for c_id in by_val[t_val]:
                    rot = next(c['rot'] for c in hand if c['id'] == c_id)
                    actions.append({
                        'type': 'add',
                        'card_id': c_id,
                        'target_area': area,
                        'label': f"Add card {_get_card_desc(c_id, rot)} to opponent's Area {area} set."
                    })
                    
    # C. TAKE OPPONENT SET
    for area in ['1', '2']:
        if state['table'][opp][area]:
            actions.append({
                'type': 'take',
                'target_area': area,
                'label': f"Take the set from opponent's Area {area} into hand."
            })
            
    # D. ROTATE HAND
    actions.append({
        'type': 'rotate',
        'label': "Rotate all cards in hand."
    })
    
    def action_sort_key(a):
        if a['type'] == 'play':
            return (0, a['val'], tuple(a['cards']))
        elif a['type'] == 'add':
            return (1, a['target_area'], a['card_id'])
        elif a['type'] == 'take':
            return (2, a['target_area'])
        else:
            return (3,)
            
    actions.sort(key=action_sort_key)
    return actions

def apply(state, action):
    new_state = copy.deepcopy(state)
    new_state['ply'] += 1
    
    p = new_state['to_play']
    a_type = action['type']
    
    def return_set(player, area):
        cards = new_state['table'][player][area]
        for c in cards:
            c['rot'] = 1 - c['rot']
        new_state['hands'][player].extend(cards)
        new_state['table'][player][area] = []
        
    if a_type == 'play':
        played = []
        rem_hand = []
        c_ids = set(action['cards'])
        for c in new_state['hands'][p]:
            if c['id'] in c_ids:
                played.append(c)
            else:
                rem_hand.append(c)
        new_state['hands'][p] = rem_hand
        
        my_size = len(played)
        for opp_p in PLAYERS:
            for opp_a in ['1', '2']:
                if new_state['table'][opp_p][opp_a] and len(new_state['table'][opp_p][opp_a]) == my_size:
                    return_set(opp_p, opp_a)
                    break
                    
        new_state['table'][p][new_state['current_area']] = played
        
    elif a_type == 'add':
        opp = PLAYERS[1] if p == PLAYERS[0] else PLAYERS[0]
        t_area = action['target_area']
        c_id = action['card_id']
        
        card_to_add = None
        rem_hand = []
        for c in new_state['hands'][p]:
            if c['id'] == c_id:
                card_to_add = c
            else:
                rem_hand.append(c)
        new_state['hands'][p] = rem_hand
        
        new_state['table'][opp][t_area].append(card_to_add)
        new_size = len(new_state['table'][opp][t_area])
        
        for opp_p in PLAYERS:
            for opp_a in ['1', '2']:
                if opp_p == opp and opp_a == t_area:
                    continue
                if new_state['table'][opp_p][opp_a] and len(new_state['table'][opp_p][opp_a]) == new_size:
                    return_set(opp_p, opp_a)
                    break
                    
    elif a_type == 'take':
        opp = PLAYERS[1] if p == PLAYERS[0] else PLAYERS[0]
        t_area = action['target_area']
        cards = new_state['table'][opp][t_area]
        for c in cards:
            c['rot'] = 1 - c['rot']
        new_state['hands'][p].extend(cards)
        new_state['table'][opp][t_area] = []
        
    elif a_type == 'rotate':
        for c in new_state['hands'][p]:
            c['rot'] = 1 - c['rot']
            
    if len(new_state['hands'][p]) == 0:
        new_state['scores'][p] += 1
        if new_state['scores'][p] < 2:
            next_deal = new_state['future_rounds'].pop(0)
            setup_round(new_state, next_deal)
            new_state['turn'] += 1
            return new_state
        else:
            new_state['turn'] += 1
            return new_state

    curr_a = new_state['current_area']
    if curr_a == '1':
        new_state['current_area'] = '2'
    else:
        new_state['current_area'] = '1'
        new_state['to_play'] = PLAYERS[1] if p == PLAYERS[0] else PLAYERS[0]
        
    new_state['turn'] += 1
    
    next_p = new_state['to_play']
    next_a = new_state['current_area']
    if new_state['table'][next_p][next_a]:
        new_state['discard_pile'].extend(new_state['table'][next_p][next_a])
        new_state['table'][next_p][next_a] = []
        
    return new_state

def is_terminal(state):
    return max(state['scores'].values()) >= 2

def winner(state):
    if not is_terminal(state):
        raise ValueError("Match is not over")
    if state['scores'][PLAYERS[0]] >= 2:
        return PLAYERS[0]
    return PLAYERS[1]

def describe_state(state, viewer):
    lines = []
    lines.append(f"Turn: {state['turn']}, Ply: {state['ply']}")
    lines.append(f"Scores: Automa {state['scores']['automa']} - Human {state['scores']['human']}")
    
    if is_terminal(state):
        lines.append("Game Over.")
        return "\n".join(lines)
        
    lines.append(f"Current Turn: {state['to_play']} (Area {state['current_area']})")

    for p in PLAYERS:
        lines.append(f"\n--- {p.upper()} ---")
        
        is_visible = False
        if p == 'automa':
            is_visible = True
        elif viewer.name == p:
            is_visible = True
            
        if is_visible:
            if state['hands'][p]:
                h_str = ", ".join(_get_card_desc(c['id'], c['rot']) for c in state['hands'][p])
            else:
                h_str = "Empty"
            lines.append(f"Hand: {h_str}")
        else:
            lines.append(f"Hand: {len(state['hands'][p])} card(s)")
            
        for a in ['1', '2']:
            cards = state['table'][p][a]
            if cards:
                c_str = ", ".join(_get_card_desc(c['id'], c['rot']) for c in cards)
                val = _active_val(cards[0]['id'], cards[0]['rot'])
                lines.append(f"Area {a}: [{c_str}] (Size: {len(cards)}, Value: {val})")
            else:
                lines.append(f"Area {a}: Empty")
                
    lines.append(f"\nDiscard Pile: {len(state['discard_pile'])} card(s)")
    return "\n".join(lines)

def check_invariants(state):
    errors = []
    
    all_cards = []
    for p in PLAYERS:
        all_cards.extend(state['hands'][p])
        for a in ['1', '2']:
            all_cards.extend(state['table'][p][a])
    all_cards.extend(state['discard_pile'])
    
    if len(all_cards) != 26:
        errors.append(f"Expected 26 cards in play, found {len(all_cards)}")
        
    card_ids = sorted([c['id'] for c in all_cards])
    if card_ids != list(range(26)):
        errors.append(f"Missing or duplicated cards in current round. IDs present: {card_ids}")
        
    for i, deal in enumerate(state.get('future_rounds', [])):
        d_ids = sorted([c['id'] for c in deal])
        if d_ids != list(range(26)):
            errors.append(f"Future round {i} has missing/duplicate cards.")
            
    table_sets = {}
    for p in PLAYERS:
        for a in ['1', '2']:
            cards = state['table'][p][a]
            if cards:
                val = _active_val(cards[0]['id'], cards[0]['rot'])
                for c in cards:
                    if _active_val(c['id'], c['rot']) != val:
                        errors.append(f"Set in {p} Area {a} has mismatched values")
                        
                size = len(cards)
                if size in table_sets:
                    errors.append(f"Multiple sets of size {size} on the table")
                table_sets[size] = True
                
    return errors