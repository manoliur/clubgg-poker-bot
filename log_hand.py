#!/usr/bin/env python3
"""Ручная дозапись раздачи в hand_history.jsonl и профилей в players.json.

В игровом цикле это делает сам бот (main.Bot.observe -> opponents.Profiles), без
subprocess. Скрипт остался как отладочный вход — дописать раздачу руками:

    python log_hand.py '{"hand_id":1,"street":"river","players_actions":{"Оппонент 1":["VPIP","PFR"]}}'
    echo '{...}' | python log_hand.py

players_actions: имя -> список пометок 'VPIP', 'PFR', '3BET', 'BET', 'CALL'.
"""
import datetime
import json
import sys

import config
import opponents

HISTORY = config.HAND_HISTORY
PLAYERS = config.PLAYERS_FILE


def log_hand(record, path=None):
    record.setdefault('ts', datetime.datetime.now().isoformat(timespec='seconds'))
    with open(path or HISTORY, 'a', encoding='utf-8') as f:
        f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return len(record)


def update_player(name, actions, path=None):
    """actions: пометки раздачи ('VPIP', 'PFR', '3BET', 'BET', 'CALL', 'CHECK')."""
    actions = [str(a).upper() for a in actions]
    profiles = opponents.Profiles(path or PLAYERS)
    p = profiles.update(name, {
        'vpip': 'VPIP' in actions or 'PFR' in actions or '3BET' in actions,
        'pfr': 'PFR' in actions,
        'three_bet': '3BET' in actions,
        'three_bet_spot': '3BET' in actions,
        'bets': actions.count('BET') + actions.count('RAISE'),
        'passive': actions.count('CALL') + actions.count('CHECK'),
    })
    profiles.save()
    return p


if __name__ == '__main__':
    data = sys.argv[1] if len(sys.argv) > 1 else sys.stdin.read()
    rec = json.loads(data)
    print('logged:', log_hand(rec))
    for player, marks in (rec.get('players_actions') or {}).items():
        print(f'player {player}: {update_player(player, marks)}')
