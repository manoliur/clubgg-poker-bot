#!/usr/bin/env python3
"""Система понимания стола: кикер топ-пары, опасность доски, линии оппонента.

Каждый кусок включается своей галочкой в панели, и с выключенной галочкой бот
играет ровно как раньше — это здесь и проверяется наравне с самим поведением.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy as st                       # noqa: E402


def state(**kw):
    base = {'hole': ['Ah', 'Kd'], 'board': [], 'street': 'preflop', 'has_bet': False,
            'to_call_bb': None, 'pot_bb': 10.0, 'position': 'BTN', 'players': 2}
    base.update(kw)
    return base


def chart_with(**settings):
    chart = st.DEFAULT_CHART.copy()
    chart.settings.update(settings)
    return chart


# профили оппонента с набранной статистикой (metric_ready берёт min_hands_agg=80)
PASSIVE = {'hands': 120, 'agg': 1.2, 'agg_bets': 30, 'agg_calls': 25}
AGGRO = {'hands': 120, 'agg': 3.4, 'agg_bets': 51, 'agg_calls': 15}


class KickerGradeTest(unittest.TestCase):
    """Порог колла средней рукой двигает кикер: A/K — шире, мелочь — тайтовее."""

    # доска A-9-2 без масти и связок: решает только пара тузов и кикер
    BOARD = ['Ad', '9s', '2c']

    def call_price(self, hole, to_call, chart=None):
        s = state(hole=hole, board=self.BOARD, street='turn', has_bet=True,
                  to_call_bb=to_call, pot_bb=10.0, players=2)
        return st.decide(s, chart=chart)

    def test_strong_kicker_calls_where_weak_folds(self):
        """Цена 6.7ББ в банк 10 (40%) — между порогами 34% (слабый) и 48% (A/K)."""
        strong = self.call_price(['Ah', 'Kc'], 6.7)
        weak = self.call_price(['Ah', '7c'], 6.7)
        self.assertEqual(strong['action'], 'call', strong['reason'])
        self.assertEqual(weak['action'], 'fold', weak['reason'])
        self.assertIn('кикер A/K', strong['reason'])
        self.assertIn('кикер слабый', weak['reason'])

    def test_medium_kicker_plays_as_before(self):
        """Q/J/T — обычный кикер: порог тот же, что был до градаций."""
        before = self.call_price(['Ah', 'Jc'], 6.7, chart=chart_with(kicker_grades=False))
        after = self.call_price(['Ah', 'Jc'], 6.7)
        self.assertEqual(before['action'], after['action'])
        self.assertEqual(before['reason'], after['reason'])

    def test_flag_off_grades_nothing(self):
        off = chart_with(kicker_grades=False)
        strong = self.call_price(['Ah', 'Kc'], 6.7, chart=off)
        weak = self.call_price(['Ah', '7c'], 6.7, chart=off)
        self.assertEqual(strong['action'], weak['action'])
        self.assertNotIn('кикер', strong['reason'])

    def test_kicker_does_not_turn_a_fold_into_a_call_at_any_price(self):
        """Сильный кикер двигает порог, а не отменяет его: 70% банка — фолд."""
        d = self.call_price(['Ah', 'Kc'], 23.0)
        self.assertEqual(d['action'], 'fold', d['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
