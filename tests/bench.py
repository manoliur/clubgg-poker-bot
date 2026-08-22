#!/usr/bin/env python3
"""Замер скорости решений: сколько стоит один вызов strategy.decide().

Время ответа бота в игре складывается из adb+cv2 (сотни миллисекунд) и самого
решения (микросекунды). Решение обязано таким и остаться: новые правила — это
таблицы и кэши, а не перебор комбинаций. Скрипт даёт число, которое можно
сравнить до и после правки:

    python tests/bench.py                  # 10 000 решений, повтор 3 раза
    python tests/bench.py -n 20000 -r 5

Состояния случайные, но воспроизводимые (фиксированный seed), поэтому две
сборки меряются на одном и том же наборе раздач.
"""
import argparse
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy                                   # noqa: E402

RANKS = '23456789TJQKA'
SUITS = 'hdcs'
DECK = [r + s for r in RANKS for s in SUITS]
POSITIONS = ('UTG', 'MP', 'CO', 'BTN', 'SB', 'BB')
STREETS = {0: 'preflop', 3: 'flop', 4: 'turn', 5: 'river'}
# профили оппонента, под которые бот подстраивается (в игре они приходят из
# players.json): пассивный, агрессивный и «наблюдений мало»
PROFILES = (
    None,
    {'hands': 120, 'vpip': 0.22, 'pfr': 0.15, 'agg': 1.1, 'agg_bets': 30, 'agg_calls': 27},
    {'hands': 140, 'vpip': 0.55, 'pfr': 0.30, 'agg': 3.2, 'agg_bets': 60, 'agg_calls': 19},
    {'hands': 5, 'vpip': 1.0, 'pfr': 0.0, 'agg': 0.0},
)


def random_states(n, seed=20260822):
    """n случайных состояний стола — тех же, что бот собирает по кадру."""
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        deck = rnd.sample(DECK, 7)
        board_len = rnd.choice((0, 3, 3, 4, 5, 5))
        hole, board = deck[:2], deck[2:2 + board_len]
        players = rnd.choice((2, 2, 2, 3, 4, 6))
        pot = round(rnd.uniform(2.0, 60.0), 1)
        has_bet = rnd.random() < 0.55
        to_call = round(pot * rnd.uniform(0.15, 1.2), 1) if has_bet else None
        out.append({
            'hole': hole, 'board': board, 'street': STREETS[board_len],
            'has_bet': has_bet, 'to_call_bb': to_call, 'pot_bb': pot,
            'position': rnd.choice(POSITIONS), 'players': players,
            'players_seated': max(players, rnd.choice((2, 6))),
            'first_to_act': rnd.choice(('me', 'opp')),
            'opp_aggressor': rnd.random() < 0.4,
            'opp_checked': rnd.random() < 0.4,
            'opp_bet_streets': rnd.choice((0, 0, 1, 2, 3)),
            'bluff_ok': True,
        })
    return out


def run(states, profiles=PROFILES, chart=None, stack_bb=60.0):
    """Один прогон по всем состояниям. Возвращает секунды."""
    decide = strategy.decide
    n = len(profiles)
    t0 = time.perf_counter()
    for i, s in enumerate(states):
        decide(s, profile=profiles[i % n], stack_bb=stack_bb, chart=chart)
    return time.perf_counter() - t0


def measure(n=10000, repeat=3, chart=None, seed=20260822):
    """Лучшее из repeat прогонов (шум планировщика не должен попадать в число)."""
    states = random_states(n, seed)
    run(states[:200], chart=chart)                # прогрев кэшей диапазонов/досок
    return min(run(states, chart=chart) for _ in range(repeat))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Скорость strategy.decide()')
    ap.add_argument('-n', type=int, default=10000, help='решений за прогон')
    ap.add_argument('-r', '--repeat', type=int, default=3, help='прогонов')
    ap.add_argument('--flags', default='', help='флаги через запятую: key=on/off')
    args = ap.parse_args(argv)

    chart = strategy.DEFAULT_CHART.copy()
    for item in (f for f in args.flags.split(',') if f.strip()):
        key, _, value = item.partition('=')
        chart.settings[key.strip()] = value.strip().lower() not in ('0', 'off', 'false', '')
    best = measure(args.n, args.repeat, chart)
    print(f'{args.n} решений: {best * 1000:.1f} мс всего, '
          f'{best / args.n * 1e6:.1f} мкс на решение '
          f'({args.n / best:,.0f} решений/с)'.replace(',', ' '))
    return 0


if __name__ == '__main__':
    sys.exit(main())
