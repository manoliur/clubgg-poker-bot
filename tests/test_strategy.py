#!/usr/bin/env python3
"""Тесты стратегии: разбор диапазонов, префлоп по позициям, постфлоп по силе руки."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy as st       # noqa: E402


def state(**kw):
    base = {'hole': ['Ah', 'Kd'], 'board': [], 'street': 'preflop', 'has_bet': False,
            'to_call_bb': None, 'pot_bb': 3.0, 'position': 'BTN', 'players': 6}
    base.update(kw)
    return base


class RangeTest(unittest.TestCase):
    def test_hand_code(self):
        self.assertEqual(st.hand_code(['Ah', 'Kd']), 'AKo')
        self.assertEqual(st.hand_code(['Kh', 'Ah']), 'AKs')
        self.assertEqual(st.hand_code(['7h', '7c']), '77')
        self.assertEqual(st.hand_code(['2d', '5d']), '52s')

    def test_pairs_plus(self):
        r = st.parse_range('TT+')
        self.assertEqual(r, {'TT', 'JJ', 'QQ', 'KK', 'AA'})

    def test_ace_suited_plus(self):
        self.assertEqual(st.parse_range('ATs+'), {'ATs', 'AJs', 'AQs', 'AKs'})

    def test_connector_plus_raises_both_cards(self):
        self.assertEqual(st.parse_range('76s+'),
                         {'76s', '87s', '98s', 'T9s', 'JTs', 'QJs', 'KQs', 'AKs'})

    def test_non_connector_plus_fixes_top_card(self):
        self.assertEqual(st.parse_range('K9s+'), {'K9s', 'KTs', 'KJs', 'KQs'})

    def test_exact_hands(self):
        self.assertEqual(st.parse_range('AA, AKo, JTs'), {'AA', 'AKo', 'JTs'})

    def test_in_range(self):
        self.assertTrue(st.in_range(['Ah', 'Ad'], '22+'))
        self.assertFalse(st.in_range(['9h', '4d'], '22+, ATs+'))

    def test_bad_token(self):
        with self.assertRaises(ValueError):
            st.parse_range('ZZ+')

    def test_chen(self):
        self.assertGreater(st.chen_score(['Ah', 'Ad']), st.chen_score(['Kh', 'Kd']))
        self.assertGreater(st.chen_score(['Ah', 'Kh']), st.chen_score(['Ah', 'Kd']))
        self.assertGreater(st.chen_score(['Ah', 'Kd']), st.chen_score(['7h', '2d']))


class PreflopTest(unittest.TestCase):
    def test_premium_opens_from_utg(self):
        d = st.decide(state(hole=['Ah', 'Ad'], position='UTG'))
        self.assertEqual(d['action'], 'raise')

    def test_trash_folds_when_facing_bet(self):
        d = st.decide(state(hole=['7h', '2d'], position='UTG', has_bet=True,
                            to_call_bb=2.5, pot_bb=4.0))
        self.assertEqual(d['action'], 'fold')

    def test_trash_checks_when_free(self):
        d = st.decide(state(hole=['7h', '2d'], position='BB', has_bet=False))
        self.assertEqual(d['action'], 'check')

    def test_utg_is_tighter_than_button(self):
        hand = ['9h', '8h']
        self.assertEqual(st.decide(state(hole=hand, position='UTG'))['action'], 'check')
        self.assertEqual(st.decide(state(hole=hand, position='BTN'))['action'], 'raise')

    def test_three_bet_with_strong_hand(self):
        d = st.decide(state(hole=['Qh', 'Qd'], position='BTN', has_bet=True,
                            to_call_bb=2.5, pot_bb=4.0))
        self.assertEqual(d['action'], 'raise')
        self.assertGreater(d['amount_bb'], 2.5)

    def test_call_range_calls(self):
        d = st.decide(state(hole=['Jh', 'Th'], position='BTN', has_bet=True,
                            to_call_bb=2.5, pot_bb=4.0))
        self.assertEqual(d['action'], 'call')

    def test_expensive_call_folded_without_premium(self):
        d = st.decide(state(hole=['Jh', 'Th'], position='BTN', has_bet=True,
                            to_call_bb=30.0, pot_bb=40.0))
        self.assertEqual(d['action'], 'fold')

    def test_heads_up_range_is_wider(self):
        hand = ['K5o'[0] + 'h', '5d']      # K5o
        self.assertEqual(st.decide(state(hole=hand, position='SB', players=2))['action'],
                         'raise')
        self.assertEqual(st.decide(state(hole=hand, position='UTG', players=6))['action'],
                         'check')

    def test_unknown_position_uses_middle_range(self):
        d = st.decide(state(hole=['Ah', 'Qh'], position=None, players=6))
        self.assertEqual(d['action'], 'raise')


class PostflopTest(unittest.TestCase):
    def test_set_raises_facing_bet(self):
        d = st.decide(state(hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop',
                            has_bet=True, to_call_bb=3.0, pot_bb=6.0, players=2))
        self.assertEqual(d['action'], 'raise')

    def test_air_folds_facing_bet(self):
        d = st.decide(state(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], street='flop',
                            has_bet=True, to_call_bb=3.0, pot_bb=6.0, players=2))
        self.assertEqual(d['action'], 'fold')

    def test_flush_draw_calls_with_right_odds(self):
        # 9 аутов ~36% на флопе против цены 25% банка
        d = st.decide(state(hole=['Ah', '5h'], board=['Kh', '9h', '2c'], street='flop',
                            has_bet=True, to_call_bb=2.0, pot_bb=6.0, players=2))
        self.assertEqual(d['action'], 'call')

    def test_flush_draw_folds_to_huge_bet(self):
        d = st.decide(state(hole=['Ah', '5h'], board=['Kh', '9h', '2c'], street='river',
                            has_bet=True, to_call_bb=20.0, pot_bb=6.0, players=2))
        self.assertEqual(d['action'], 'fold')

    def test_value_bet_when_checked_to(self):
        d = st.decide(state(hole=['Ah', 'Kc'], board=['Ad', 'Kd', '2c'], street='flop',
                            has_bet=False, pot_bb=6.0, players=2))
        self.assertEqual(d['action'], 'raise')
        self.assertAlmostEqual(d['amount_bb'], 3.6, places=1)

    def test_medium_hand_controls_pot_on_river(self):
        d = st.decide(state(hole=['Ah', '7c'], board=['Ad', 'Kd', '2c', '5s', '9h'],
                            street='river', has_bet=False, pot_bb=10.0, players=2))
        self.assertEqual(d['action'], 'check')

    def test_medium_hand_folds_to_big_river_bet(self):
        d = st.decide(state(hole=['Ah', '7c'], board=['Ad', 'Kd', '2c', '5s', '9h'],
                            street='river', has_bet=True, to_call_bb=15.0, pot_bb=10.0,
                            players=2))
        self.assertEqual(d['action'], 'fold')

    def test_no_numbers_still_decides(self):
        """Без эталонов цифр банка/ставки нет — решение всё равно принимается."""
        d = st.decide(state(hole=['Ah', 'Ac'], board=['Ad', 'Kd', '2c'], street='flop',
                            has_bet=True, to_call_bb=None, pot_bb=None, players=2))
        self.assertEqual(d['action'], 'raise')

    def test_never_checks_when_facing_bet(self):
        d = st.decide(state(hole=['3h', '2c'], board=['Ad', 'Kd', 'Qc'], street='flop',
                            has_bet=True, to_call_bb=5.0, pot_bb=5.0, players=6))
        self.assertIn(d['action'], ('fold', 'call', 'raise'))

    def test_never_calls_when_no_bet(self):
        for hole in (['Ah', 'Kd'], ['7h', '2c'], ['Qh', 'Qc']):
            d = st.decide(state(hole=hole, board=['Ad', '9d', '2c'], street='flop',
                                has_bet=False, pot_bb=5.0, players=6))
            self.assertIn(d['action'], ('check', 'raise'), (hole, d))


class RobustnessTest(unittest.TestCase):
    def test_unrecognized_cards_are_safe(self):
        d = st.decide(state(hole=[None, None], has_bet=True, to_call_bb=3.0))
        self.assertEqual(d['action'], 'fold')
        d = st.decide(state(hole=['Ah'], has_bet=False))
        self.assertEqual(d['action'], 'check')

    def test_incomplete_board_is_safe(self):
        """Доска из 1-2 карт невозможна: карту не прочитали — не считаем силу руки."""
        for board in (['Ad'], ['Ad', 'Ks']):
            d = st.decide(state(hole=['Ah', 'Kd'], board=board, street='unknown',
                                has_bet=True, to_call_bb=3.0, pot_bb=6.0))
            self.assertEqual(d['action'], 'fold', board)
            self.assertIn('не полностью', d['reason'])
            d = st.decide(state(hole=['Ah', 'Kd'], board=board, street='unknown',
                                has_bet=False, pot_bb=6.0))
            self.assertEqual(d['action'], 'check', board)

    def test_full_boards_are_played_normally(self):
        for board in (['9d', '5s', '2c'], ['9d', '5s', '2c', '7h'],
                      ['9d', '5s', '2c', '7h', 'Ts']):
            d = st.decide(state(hole=['9h', '9c'], board=board,
                                street={3: 'flop', 4: 'turn', 5: 'river'}[len(board)],
                                has_bet=True, to_call_bb=3.0, pot_bb=9.0))
            self.assertNotIn('не полностью', d['reason'])

    def test_garbage_card_string(self):
        d = st.decide(state(hole=['Xx', 'Kd'], has_bet=True, to_call_bb=2.0))
        self.assertEqual(d['action'], 'fold')

    def test_loose_opponent_kills_bluff(self):
        base = state(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], street='flop',
                     has_bet=False, pot_bb=6.0, players=2)
        self.assertEqual(st.decide(base)['action'], 'raise')
        loose = st.decide(base, profile={'vpip': 0.55, 'agg': 0.5})
        self.assertEqual(loose['action'], 'check')


if __name__ == '__main__':
    unittest.main(verbosity=2)
