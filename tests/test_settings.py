#!/usr/bin/env python3
"""Тесты настроек: пресеты стиля, живое перечитывание devices.json, размеры
ставок по улицам, мультипот, короткий стек, блеф с блокерами."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy as st                      # noqa: E402


def state(**kw):
    base = {'hole': ['Ah', 'Kd'], 'board': [], 'street': 'preflop', 'has_bet': False,
            'to_call_bb': None, 'pot_bb': 3.0, 'position': 'BTN', 'players': 6}
    base.update(kw)
    return base


def chart_with(**settings):
    """Копия встроенного чарта с изменёнными настройками."""
    chart = st.DEFAULT_CHART.copy()
    chart.settings.update(settings)
    return chart


class StyleTest(unittest.TestCase):
    def test_every_style_is_a_full_settings_dict(self):
        for style in st.STYLE_PRESETS:
            with self.subTest(style=style):
                s = st.style_settings(style)
                self.assertEqual(set(s), set(st.DEFAULT_SETTINGS))
                self.assertIn(style, st.STYLE_TITLES)

    def test_unknown_style_falls_back_to_standard(self):
        """Опечатка в панели не должна ронять бота."""
        self.assertEqual(st.style_settings('Тайтовый!!!'), st.style_settings('standard'))
        self.assertEqual(st.style_settings(None), st.style_settings('standard'))

    def test_tight_calls_less_and_bets_smaller_than_loose(self):
        tight, loose = st.style_settings('tighty'), st.style_settings('loose')
        self.assertLess(tight['medium_max_price'], loose['medium_max_price'])
        self.assertLess(tight['preflop_max_price'], loose['preflop_max_price'])
        self.assertGreater(tight['draw_min_equity'], loose['draw_min_equity'])
        self.assertLess(tight['bet_medium'], loose['bet_medium'])

    def test_aggressive_bets_bigger_but_calls_like_standard(self):
        agg, std = st.style_settings('aggressive'), st.style_settings('standard')
        self.assertGreater(agg['cbet_pot'], std['cbet_pot'])
        self.assertGreater(agg['bet_nuts'], std['bet_nuts'])
        self.assertGreater(agg['aggression'], std['aggression'])
        self.assertEqual(agg['medium_max_price'], std['medium_max_price'])
        self.assertEqual(agg['draw_min_equity'], std['draw_min_equity'])

    def test_tight_folds_where_loose_calls(self):
        """Стиль виден в решении, а не только в числах."""
        s = state(hole=['Ah', 'Jc'], board=['Ad', '9s', '2c'], street='river',
                  has_bet=True, to_call_bb=4.5, pot_bb=10.0, players=2)
        tight = st.decide(s, chart=chart_with(**st.style_settings('tighty')))
        loose = st.decide(s, chart=chart_with(**st.style_settings('loose')))
        self.assertEqual(tight['action'], 'fold', tight['reason'])
        self.assertEqual(loose['action'], 'call', loose['reason'])


class DeviceSettingsTest(unittest.TestCase):
    def test_old_record_without_style_still_works(self):
        """Существующий devices.json (name/chart/aggression/defense/stack)."""
        cfg = {'name': 'Телефон 1', 'chart': 'charts/6max_standard.json',
               'aggression': 1.0, 'defense': 1.0, 'stack': 69.6}
        self.assertEqual(st.device_settings(st.DEFAULT_SETTINGS, cfg),
                         st.DEFAULT_SETTINGS)

    def test_style_then_keys_then_sliders(self):
        cfg = {'style': 'tighty', 'cbet_pot': 0.9, 'aggression': 2.0, 'defense': 0.5}
        s = st.device_settings(st.DEFAULT_SETTINGS, cfg)
        self.assertEqual(s['cbet_pot'], 0.9)                     # ключ поверх стиля
        self.assertEqual(s['aggression'], round(0.9 * 2.0, 3))   # ползунок поверх стиля
        self.assertEqual(s['medium_max_price'], round(0.30 * 0.5, 3))

    def test_sliders_do_not_creep_on_repeated_reads(self):
        """Настройки перечитываются постоянно — множители не должны накапливаться."""
        cfg = {'style': 'standard', 'aggression': 1.5, 'defense': 1.5}
        once = st.device_settings(st.DEFAULT_SETTINGS, cfg)
        twice = st.device_settings(st.DEFAULT_SETTINGS, cfg)
        self.assertEqual(once, twice)

    def test_flags_are_booleans_not_numbers(self):
        s = st.device_settings(st.DEFAULT_SETTINGS, {'bet_sizing': 1, 'multiway_tight': 0})
        self.assertIs(s['bet_sizing'], True)
        self.assertIs(s['multiway_tight'], False)

    def test_panel_view_has_no_slider_multipliers(self):
        """Панель показывает пороги такими, какими сохранит их обратно."""
        cfg = {'style': 'standard', 'defense': 2.0}
        view = st.device_settings(st.DEFAULT_SETTINGS, cfg, sliders=False)
        self.assertEqual(view['medium_max_price'], st.DEFAULT_SETTINGS['medium_max_price'])


class BetSizingTest(unittest.TestCase):
    def bet(self, chart, **kw):
        s = state(has_bet=False, pot_bb=10.0, players=2, **kw)
        return st.decide(s, chart=chart)

    def test_off_keeps_the_old_sizes(self):
        chart = chart_with(bet_sizing=False)
        nuts = self.bet(chart, hole=['As', 'Ks'], board=['Qs', '9s', '2s'], street='flop')
        strong = self.bet(chart, hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop')
        self.assertAlmostEqual(nuts['pot_frac'], st.DEFAULT_SETTINGS['nuts_pot'])
        self.assertAlmostEqual(strong['pot_frac'], st.DEFAULT_SETTINGS['cbet_pot'])

    def test_on_scales_by_hand_strength(self):
        chart = chart_with(bet_sizing=True)
        nuts = self.bet(chart, hole=['As', 'Ks'], board=['Qs', '9s', '2s'], street='flop')
        strong = self.bet(chart, hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop')
        medium = self.bet(chart, hole=['Ah', 'Jc'], board=['Ad', '9s', '2c'], street='flop')
        draw = self.bet(chart, hole=['Ah', '5h'], board=['Kh', '9h', '2c'], street='flop')
        self.assertAlmostEqual(nuts['pot_frac'], 0.75)
        self.assertAlmostEqual(strong['pot_frac'], 0.6)
        self.assertAlmostEqual(medium['pot_frac'], 0.5)
        self.assertAlmostEqual(draw['pot_frac'], 0.45)
        self.assertGreater(nuts['pot_frac'], strong['pot_frac'])
        self.assertGreater(strong['pot_frac'], medium['pot_frac'])
        self.assertGreater(medium['pot_frac'], draw['pot_frac'])

    def test_street_factor_makes_the_river_bet_bigger(self):
        chart = chart_with(bet_sizing=True, street_factor_river=1.2)
        river = self.bet(chart, hole=['As', 'Ks'], board=['Qs', '9s', '2s', '3d', '4c'],
                         street='river')
        self.assertAlmostEqual(river['pot_frac'], round(0.75 * 1.2, 3))

    def test_size_never_exceeds_the_pot(self):
        """Пресета крупнее «100% банка» в клиенте нет."""
        chart = chart_with(bet_sizing=True, bet_nuts=1.0, street_factor_flop=2.0)
        nuts = self.bet(chart, hole=['As', 'Ks'], board=['Qs', '9s', '2s'], street='flop')
        self.assertEqual(nuts['pot_frac'], 1.0)

    def test_aggression_still_multiplies_sizes(self):
        chart = chart_with(bet_sizing=True, aggression=1.2)
        nuts = self.bet(chart, hole=['As', 'Ks'], board=['Qs', '9s', '2s'], street='flop')
        self.assertAlmostEqual(nuts['pot_frac'], round(0.75 * 1.2, 3))


class MultiwayTest(unittest.TestCase):
    FLOP = {'board': ['Ad', '9s', '2c'], 'street': 'flop', 'pot_bb': 10.0}

    def test_medium_checks_instead_of_a_thin_cbet(self):
        s = state(hole=['Ah', 'Jc'], has_bet=False, players=3, **self.FLOP)
        d = st.decide(s)
        self.assertEqual(d['action'], 'check', d['reason'])
        self.assertIn('мультипот 3 игроков', d['reason'])

    def test_same_hand_bets_heads_up(self):
        s = state(hole=['Ah', 'Jc'], has_bet=False, players=2, **self.FLOP)
        self.assertEqual(st.decide(s)['action'], 'raise')

    def test_value_bet_is_smaller_with_three_players(self):
        kw = dict(hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop',
                  has_bet=False, pot_bb=10.0)
        hu = st.decide(state(players=2, **kw))
        multi = st.decide(state(players=3, **kw))
        self.assertEqual(multi['action'], 'raise', multi['reason'])
        self.assertLess(multi['pot_frac'], hu['pot_frac'])
        self.assertIn('играем тайтовее', multi['reason'])

    def test_marginal_river_call_becomes_a_fold(self):
        kw = dict(hole=['Ah', 'Jc'], board=['Ad', '9s', '2c', '7h', '3d'], street='river',
                  has_bet=True, to_call_bb=5.5, pot_bb=10.0)
        self.assertEqual(st.decide(state(players=2, **kw))['action'], 'call')
        self.assertEqual(st.decide(state(players=3, **kw))['action'], 'fold')

    def test_flag_off_plays_as_before(self):
        s = state(hole=['Ah', 'Jc'], has_bet=False, players=3, **self.FLOP)
        d = st.decide(s, chart=chart_with(multiway_tight=False))
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertNotIn('мультипот', d['reason'])


class ShortStackTest(unittest.TestCase):
    def test_push_instead_of_a_min_raise(self):
        d = st.decide(state(hole=['Ah', 'Ks'], position='BTN'), stack_bb=18.0)
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertEqual(d['amount_bb'], 18.0)
        self.assertEqual(d['pot_frac'], 1.0)
        self.assertIn('короткий стек 18ББ — push/fold', d['reason'])

    def test_trash_is_not_pushed(self):
        d = st.decide(state(hole=['7h', '2c'], position='UTG'), stack_bb=18.0)
        self.assertEqual(d['action'], 'check', d['reason'])
        self.assertIn('вне пуш-диапазона', d['reason'])

    def test_button_pushes_wider_than_utg(self):
        hand = ['Kh', '9h']
        btn = st.decide(state(hole=hand, position='BTN'), stack_bb=18.0)
        utg = st.decide(state(hole=hand, position='UTG'), stack_bb=18.0)
        self.assertEqual(btn['action'], 'raise', btn['reason'])
        self.assertEqual(utg['action'], 'check', utg['reason'])

    def test_deep_stack_opens_normally(self):
        d = st.decide(state(hole=['Ah', 'Ks'], position='BTN'), stack_bb=100.0)
        self.assertEqual(d['action'], 'raise')
        self.assertEqual(d['amount_bb'], st.DEFAULT_SETTINGS['open_size_bb'])
        self.assertNotIn('короткий стек', d['reason'])

    def test_all_in_is_called_wider(self):
        """Цена, при которой глубокий стек пасует, а короткий коллит."""
        kw = dict(hole=['Ah', 'Ts'], position='BTN', players=2, has_bet=True,
                  to_call_bb=5.0, pot_bb=13.0)
        short = st.decide(state(**kw), stack_bb=20.0,
                          chart=chart_with(short_stack_bb=30.0))
        deep = st.decide(state(**kw), stack_bb=20.0,
                         chart=chart_with(short_stack_mode=False))
        self.assertEqual(short['action'], 'call', short['reason'])
        self.assertEqual(deep['action'], 'fold', deep['reason'])

    def test_postflop_bets_bigger_on_a_short_stack(self):
        kw = dict(hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop',
                  has_bet=False, pot_bb=10.0, players=2)
        short = st.decide(state(**kw), stack_bb=20.0)
        deep = st.decide(state(**kw), stack_bb=100.0)
        self.assertGreater(short['pot_frac'], deep['pot_frac'])
        self.assertIn('короткий стек 20ББ', short['reason'])

    def test_flag_off_keeps_min_raises(self):
        d = st.decide(state(hole=['Ah', 'Ks'], position='BTN'), stack_bb=18.0,
                      chart=chart_with(short_stack_mode=False))
        self.assertEqual(d['amount_bb'], st.DEFAULT_SETTINGS['open_size_bb'])


class BlockerBluffTest(unittest.TestCase):
    # ривер: три пики на доске, у нас туз пик — натс-флеша у оппонента нет
    AIR = dict(hole=['As', '4d'], board=['Ks', '9s', '2s', '7h', '3c'], street='river',
               has_bet=False, pot_bb=10.0, players=2)

    def test_off_by_default(self):
        d = st.decide(state(**self.AIR))
        self.assertEqual(d['action'], 'check', d['reason'])

    def test_on_bluffs_with_the_blocker(self):
        d = st.decide(state(**self.AIR), chart=chart_with(blocker_bluff=True))
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertIn('блокер', d['reason'])
        self.assertAlmostEqual(d['pot_frac'], st.DEFAULT_SETTINGS['blocker_bluff_pot'])

    def test_no_blocker_no_bluff(self):
        s = dict(self.AIR, hole=['6d', '4d'])
        d = st.decide(state(**s), chart=chart_with(blocker_bluff=True))
        self.assertEqual(d['action'], 'check', d['reason'])

    def test_bluff_frequency_is_limited(self):
        s = state(**self.AIR)
        allowed = st.decide(s, chart=chart_with(blocker_bluff=True))
        blocked = st.decide({**s, 'bluff_ok': False}, chart=chart_with(blocker_bluff=True))
        self.assertEqual(allowed['action'], 'raise')
        self.assertEqual(blocked['action'], 'check')
        self.assertIn('слишком часто', blocked['reason'])

    def test_spot_detection(self):
        chart = chart_with(blocker_bluff=True)
        self.assertTrue(st.blocker_bluff_spot(state(**self.AIR), chart))
        self.assertFalse(st.blocker_bluff_spot(state(**self.AIR)))          # флаг выкл
        self.assertFalse(st.blocker_bluff_spot(state(**dict(self.AIR, players=3)), chart))
        self.assertFalse(st.blocker_bluff_spot(                              # готовая рука
            state(**dict(self.AIR, hole=['Ks', 'Kd'])), chart))

    def test_nut_straight_blocker(self):
        # доска 9-T-J: старший возможный стрит — до короля, у нас он в руке
        self.assertIn('нет старшего стрита',
                      st.nut_blocker(['Kd', '4c'], ['9h', 'Ts', 'Jc', '2d', '3s']))
        self.assertEqual(st.nut_blocker(['4c', '3d'], ['9h', 'Ts', 'Jc', '2d', '5s']), '')


class PositionTest(unittest.TestCase):
    FLOP = dict(hole=['Ah', 'Jc'], board=['Ad', '9s', '2c'], street='flop',
                has_bet=False, pot_bb=10.0, players=2)

    def test_oop_checks_medium_when_enabled(self):
        chart = chart_with(position_aware=True)
        oop = st.decide(state(first_to_act='me', **self.FLOP), chart=chart)
        ip = st.decide(state(first_to_act='opp', **self.FLOP), chart=chart)
        self.assertEqual(oop['action'], 'check', oop['reason'])
        self.assertIn('без позиции', oop['reason'])
        self.assertEqual(ip['action'], 'raise', ip['reason'])

    def test_strong_hand_bets_even_oop(self):
        d = st.decide(state(first_to_act='me', **dict(self.FLOP, hole=['9h', '9c'])),
                      chart=chart_with(position_aware=True))
        self.assertEqual(d['action'], 'raise', d['reason'])

    def test_flag_off_bets_as_before(self):
        d = st.decide(state(first_to_act='me', **self.FLOP))
        self.assertEqual(d['action'], 'raise', d['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
