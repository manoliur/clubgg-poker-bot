#!/usr/bin/env python3
"""Добавить запись о раздаче в hand_history.jsonl и обновить players.json.
Использование: python log_hand.py '{"hand_id":...,"street":"river",...}'
или передать JSON через stdin."""
import json, sys, os, datetime

DIR = r'C:\Users\Vlad\clubgg_bot'
HISTORY = os.path.join(DIR, 'hand_history.jsonl')
PLAYERS = os.path.join(DIR, 'players.json')

def log_hand(record):
    record.setdefault('ts', datetime.datetime.now().isoformat(timespec='seconds'))
    with open(HISTORY, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return len(record)

def update_player(name, actions):
    """actions: список строк вида 'VPIP', 'PFR', '3BET', 'FOLD', 'CALL', 'RAISE', 'CHECK'"""
    with open(PLAYERS, encoding='utf-8') as f:
        db = json.load(f)
    p = db.setdefault(name, {'first_seen': datetime.date.today().isoformat(),
                             'hands': 0, 'vpip': 0.0, 'pfr': 0.0, 'three_bet': 0.0,
                             'agg': 0.0, 'fold_to_3bet': None, 'leaks': [], 'notes': ''})
    p['hands'] += 1
    vpip_hands = p['vpip'] * (p['hands'] - 1)
    pfr_hands = p['pfr'] * (p['hands'] - 1)
    if 'VPIP' in actions: vpip_hands += 1
    if 'PFR' in actions: pfr_hands += 1
    p['vpip'] = round(vpip_hands / p['hands'], 3)
    p['pfr'] = round(pfr_hands / p['hands'], 3)
    with open(PLAYERS, 'w', encoding='utf-8') as f:
        json.dump(db, f, ensure_ascii=False, indent=2)
    return p

if __name__ == '__main__':
    data = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    rec = json.loads(data)
    print('logged:', log_hand(rec))
    if 'players_actions' in rec:
        for name, actions in rec['players_actions'].items():
            p = update_player(name, actions)
            print(f'player {name}: {p}')
