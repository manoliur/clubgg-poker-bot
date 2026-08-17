#!/usr/bin/env python3
"""Показать сводку по игрокам и последние раздачи. Использование: python summary.py [N]"""
import json, os, sys

DIR = r'C:\Users\Vlad\clubgg_bot'
PLAYERS = os.path.join(DIR, 'players.json')
HISTORY = os.path.join(DIR, 'hand_history.jsonl')

def load_players():
    if os.path.exists(PLAYERS):
        with open(PLAYERS, encoding='utf-8') as f:
            return json.load(f)
    return {}

def last_hands(n=5):
    if not os.path.exists(HISTORY):
        return []
    with open(HISTORY, encoding='utf-8') as f:
        lines = f.readlines()
    return [json.loads(l) for l in lines[-n:]]

if __name__ == '__main__':
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    print('=== ИГРОКИ ===')
    for name, p in load_players().items():
        if name.startswith('_'): continue
        print(f"{name}: рук={p.get('hands',0)} VPIP={p.get('vpip',0)*100:.0f}% PFR={p.get('pfr',0)*100:.0f}% 3B={p.get('three_bet',0)*100:.0f}% Agg={p.get('agg',0):.1f} | {p.get('notes','')}")
    print(f'\n=== ПОСЛЕДНИЕ {n} РАЗДАЧ ===')
    for h in last_hands(n):
        print(f"[{h.get('ts','')[:16]}] {h.get('table','')} | {h.get('hole_cards','?')} | {h.get('street','')} | hero: {h.get('hero_action','?')} | результат: {h.get('result_bb','?')} ББ | {h.get('notes','')}")
