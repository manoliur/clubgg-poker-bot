#!/usr/bin/env python3
"""Бенчмарк решений: время ответа бота не должно расти от новых правил.

Игровой цикл занят adb и cv2 (сотни миллисекунд на кадр), а само решение стоит
микросекунды — и обязано таким остаться. Абсолютные секунды на разных машинах
разные, поэтому тесты меряют не их, а ОТНОШЕНИЯ, которые от машины не зависят:

* решение против разбора карт — вся стратегия должна стоить немногим больше,
  чем неизбежный перебор пятёрок в hand_evaluator (появится перебор диапазонов
  или монте-карло — отношение улетит сразу);
* один и тот же спот, спрошенный дважды, не должен разбираться дважды.

Число в секундах печатает `python tests/bench.py` — им и меряется «до/после».
"""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hand_evaluator as he                 # noqa: E402
import strategy as st                       # noqa: E402
from tests.bench import random_states, run  # noqa: E402

N = 10000               # решений в прогоне (как в задании)
REPEAT = 3              # лучшее из трёх: планировщик шумит
CARDS_BUDGET = 1.5      # решение дороже разбора карт не больше чем в полтора раза


def best(fn, states, repeat=REPEAT):
    """Лучшее время из repeat прогонов, каждый — с холодным кэшем разбора рук."""
    out = None
    for _ in range(repeat):
        he._CLASS_CACHE.clear()
        t = time.perf_counter()
        fn(states)
        took = time.perf_counter() - t
        out = took if out is None else min(out, took)
    return out


def read_cards(states):
    for s in states:
        he.hand_class(s['hole'], s['board'])


class BenchTest(unittest.TestCase):
    """10 000 решений на случайных состояниях — столько бот принимает за смену."""

    @classmethod
    def setUpClass(cls):
        cls.states = random_states(N)
        cls.postflop = [s for s in cls.states if s['board']]
        run(cls.states[:200])               # прогрев: кэши диапазонов и досок

    def test_ten_thousand_decisions(self):
        """Ни одно случайное состояние не роняет стратегию и не даёт пустого ответа."""
        actions = set()
        for i, s in enumerate(self.states):
            d = st.decide(s, stack_bb=60.0)
            self.assertIn(d['action'], ('fold', 'check', 'call', 'raise'), (i, s, d))
            self.assertTrue(d['reason'], (i, s, d))
            actions.add(d['action'])
        self.assertEqual(actions, {'fold', 'check', 'call', 'raise'})

    def test_decision_costs_no_more_than_reading_the_cards(self):
        """Вся стратегия поверх разбора карт — считанные проценты, а не разы."""
        cards = best(read_cards, self.postflop)
        decide = best(run, self.postflop)
        ratio = decide / cards
        self.assertLess(ratio, CARDS_BUDGET,
                        f'решение стоит {ratio:.2f} разбора карт '
                        f'({decide / len(self.postflop) * 1e6:.0f} мкс на решение) — '
                        f'в стратегии появился перебор')

    def test_the_same_spot_is_not_re_evaluated(self):
        """За ход стратегию спрашивают дважды (раскрытие столбца, no_raise) — разбор один."""
        s = {'hole': ['Ah', 'Kc'], 'board': ['Ad', '9s', '2c'], 'street': 'flop',
             'has_bet': False, 'to_call_bb': None, 'pot_bb': 10.0, 'players': 2,
             'position': 'BTN'}
        he._CLASS_CACHE.clear()
        st.decide(s)
        size = len(he._CLASS_CACHE)
        for _ in range(50):
            st.decide(s)
            st.decide({**s, 'no_raise': True})
        self.assertEqual(len(he._CLASS_CACHE), size)

    def test_board_danger_is_counted_once_per_street(self):
        """Опасность доски зависит только от доски — раз на улицу, дальше кэш."""
        board = ['Ad', '9s', '2c', '7h', '3d']
        he._DANGER_CACHE.clear()
        first = he.board_danger(board)
        for _ in range(100):
            self.assertIs(he.board_danger(board), first)
        self.assertEqual(len(he._DANGER_CACHE), 1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
