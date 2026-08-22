#!/usr/bin/env python3
"""Система понимания стола: кикер топ-пары, опасность доски, линии оппонента.

Каждый кусок включается своей галочкой в панели, и с выключенной галочкой бот
играет ровно как раньше — это здесь и проверяется наравне с самим поведением.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import hand_evaluator as he                 # noqa: E402
import strategy as st                       # noqa: E402
from main import Bot                        # noqa: E402


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


class BoardDangerTest(unittest.TestCase):
    """Опасность доски на известных досках: сухая — 0, мокрая — почти единица."""

    def test_dry_rainbow_board_is_safe(self):
        for board in (['Kh', '7d', '2c'], ['Ah', '7d', '2c', '4s', '9h'],
                      ['Kh', '9h', '2c', '5d', 'Jc']):
            with self.subTest(board=board):
                self.assertEqual(he.danger_level(board), 'safe')

    def test_three_of_a_suit_is_a_flush_risk(self):
        self.assertEqual(he.danger_level(['As', 'Ks', 'Qs']), 'danger')
        self.assertGreaterEqual(he.danger_score(['2s', '8s', 'Qs', '7d']), 0.40)

    def test_four_of_a_suit_is_worse_than_three(self):
        self.assertGreater(he.danger_score(['2s', '8s', 'Qs', '4s']),
                           he.danger_score(['2s', '8s', 'Qs', '4d']))

    def test_connected_board_beats_a_disconnected_one(self):
        self.assertGreater(he.danger_score(['Js', 'Th', '9c']),
                           he.danger_score(['Js', '6h', '2c']))
        # 4 к стриту — оппоненту хватит одной карты
        self.assertEqual(he.danger_level(['Qd', 'Jc', 'Th', '9s']), 'danger')

    def test_paired_board_is_more_dangerous_than_unpaired(self):
        self.assertGreater(he.danger_score(['Kh', 'Kd', '4s']),
                           he.danger_score(['Kh', '8d', '4s']))
        self.assertEqual(he.danger_level(['Kh', 'Kd', '4s']), 'medium')

    def test_two_pair_and_trips_are_worse_than_one_pair(self):
        one = he.danger_score(['Kh', 'Kd', '4s', '9c'])
        self.assertGreater(he.danger_score(['Kh', 'Kd', '4s', '4c']), one)
        self.assertGreater(he.danger_score(['Kh', 'Kd', 'Kc', '4c']), one)
        self.assertEqual(he.danger_level(['Kh', 'Kd', 'Kc', '4c']), 'danger')

    def test_the_wettest_board_maxes_out(self):
        self.assertEqual(he.danger_score(['Ah', 'Kh', 'Qh', 'Jh', 'Th']), 1.0)
        self.assertEqual(he.danger_level(['9h', '8h', '7h']), 'danger')

    def test_score_stays_in_range_and_is_cached(self):
        he._DANGER_CACHE.clear()
        board = ['9h', '8h', '7h', '2c']
        first = he.board_danger(board)
        self.assertTrue(0.0 <= first[0] <= 1.0)
        self.assertIs(he.board_danger(board), first)     # тот же объект — кэш
        self.assertEqual(len(he._DANGER_CACHE), 1)

    def test_no_board_is_safe(self):
        self.assertEqual(he.danger_score([]), 0.0)
        self.assertEqual(he.danger_level([None]), 'safe')


class RiverValueBetTest(unittest.TestCase):
    """Живой кейс: оппонент тянул флеш, не добрал и чекнул — а бот молча чекал в ответ.

    Доска 5-5-K-2-9: у нас KQ, то есть «две пары K/5», где пятёрки общие. Класс
    силы — medium, и раньше такая рука всегда играла «контроль банка».
    """

    DRY = ['5s', '5d', 'Kc', '2h', '9c']            # флеш не пришёл: масти разные
    WET = ['5s', '5d', 'Kc', '2s', '9s']            # три пики: флеш возможен

    def river(self, board=None, hole=('Kh', 'Qd'), profile=None, chart=None, **kw):
        base = {'street': 'river', 'has_bet': False, 'pot_bb': 20.0, 'players': 2}
        base.update(kw)
        s = state(hole=list(hole), board=list(board or self.DRY), **base)
        return st.decide(s, profile=profile, chart=chart)

    def test_two_pair_bets_thin_value_on_a_dry_board(self):
        d = self.river()
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertAlmostEqual(d['pot_frac'], st.DEFAULT_SETTINGS['river_value_pot'])
        self.assertAlmostEqual(d['amount_bb'], 11.0)
        self.assertIn('тонкий вэлью', d['reason'])
        self.assertIn('две пары доминируют', d['reason'])

    def test_size_is_half_the_pot(self):
        """50-60% банка: столько платит вторая пара, больше — только флеш."""
        d = self.river()
        self.assertTrue(0.50 <= d['pot_frac'] <= 0.60, d['pot_frac'])

    def test_dangerous_board_checks_instead(self):
        d = self.river(board=self.WET)
        self.assertEqual(d['action'], 'check', d['reason'])
        self.assertIn('доска опасная', d['reason'])
        self.assertIn('3 в масть', d['reason'])

    def test_aggressive_opponent_gets_a_check(self):
        """Против агрессора тонкая ставка собирает не колл, а рейз."""
        d = self.river(profile=AGGRO)
        self.assertEqual(d['action'], 'check', d['reason'])
        self.assertIn('оппонент агрессивный', d['reason'])

    def test_passive_opponent_is_value_bet(self):
        d = self.river(profile=PASSIVE)
        self.assertEqual(d['action'], 'raise', d['reason'])

    def test_thin_bet_needs_a_heads_up_pot(self):
        d = self.river(players=3)
        self.assertEqual(d['action'], 'check', d['reason'])

    def test_top_pair_needs_a_kicker(self):
        """Топ-пара с кикером от десятки ставит, с мелким — контроль банка."""
        board = ['Ad', '9s', '2c', '7h', '3d']
        good = self.river(board=board, hole=('Ah', 'Jc'))
        bad = self.river(board=board, hole=('Ah', '4c'))
        self.assertEqual(good['action'], 'raise', good['reason'])
        self.assertIn('топ-пара держит', good['reason'])
        self.assertEqual(bad['action'], 'check', bad['reason'])
        self.assertIn('контроль банка', bad['reason'])

    def test_flag_off_keeps_the_old_check(self):
        off = chart_with(river_value_bet=False, kicker_grades=False)
        d = self.river(chart=off)
        self.assertEqual(d['action'], 'check', d['reason'])
        self.assertIn('контроль банка', d['reason'])

    def test_turn_is_only_for_a_strong_kicker(self):
        """На терне тонко ставим лишь топ-парой с A/K: впереди ещё карта."""
        turn = ['Ad', '9s', '2c', '7h']
        strong = self.river(board=turn, hole=('Ah', 'Kc'), street='turn')
        medium = self.river(board=turn, hole=('Ah', 'Jc'), street='turn')
        self.assertEqual(strong['action'], 'raise', strong['reason'])
        self.assertEqual(medium['action'], 'check', medium['reason'])

    def test_strong_two_pair_still_bets_the_usual_size(self):
        """Обычные две пары — не «тонкое велью», а прежняя ставка на велью."""
        d = self.river(board=['Ad', '9s', '2c', '7h', '3d'], hole=('Ah', '9c'))
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertIn('ставка на велью', d['reason'])
        self.assertAlmostEqual(d['pot_frac'], st.DEFAULT_SETTINGS['cbet_pot'])


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


class OpponentLinesTest(unittest.TestCase):
    """Линия оппонента в раздаче: кто поднимал, кто чекал, кто ставит третью улицу."""

    BOARD = ['Ad', '9s', '2c']

    def medium(self, to_call=6.0, chart=None, **lines):
        """Топ-пара против ставки: цена 6ББ в банк 10 — 37.5%, порог 40%."""
        s = state(hole=['Ah', 'Jc'], board=self.BOARD, street='turn', has_bet=True,
                  to_call_bb=to_call, pot_bb=10.0, players=2, **lines)
        return st.decide(s, chart=chart)

    def test_aggressor_makes_the_medium_hand_fold(self):
        quiet = self.medium()
        vs_aggressor = self.medium(opp_aggressor=True)
        self.assertEqual(quiet['action'], 'call', quiet['reason'])
        self.assertEqual(vs_aggressor['action'], 'fold', vs_aggressor['reason'])
        self.assertIn('агрессор префлопа', vs_aggressor['reason'])

    def test_flag_off_ignores_the_line(self):
        off = chart_with(opponent_lines=False)
        d = self.medium(opp_aggressor=True, chart=off)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertNotIn('агрессор', d['reason'])

    def test_two_bet_streets_turn_a_strong_raise_into_a_call(self):
        """Сет против ставки — рейз; но если оппонент бьёт третью улицу — колл."""
        kw = dict(hole=['9h', '9c'], board=['9d', '5s', '2c', '7h'], street='turn',
                  has_bet=True, to_call_bb=6.0, pot_bb=10.0, players=2)
        usual = st.decide(state(**kw))
        pressed = st.decide(state(opp_bet_streets=2, **kw))
        self.assertEqual(usual['action'], 'raise', usual['reason'])
        self.assertEqual(pressed['action'], 'call', pressed['reason'])
        self.assertIn('ставит 2 улицы подряд', pressed['reason'])

    def test_nuts_still_raise_under_pressure(self):
        """«Осторожнее» — про сильную руку, а не про непобиваемую."""
        d = st.decide(state(hole=['As', 'Ks'], board=['Qs', '9s', '2s', '7h'],
                            street='turn', has_bet=True, to_call_bb=6.0, pot_bb=10.0,
                            players=2, opp_bet_streets=3))
        self.assertEqual(d['action'], 'raise', d['reason'])

    def test_aggressor_gets_no_thin_value(self):
        kw = dict(hole=['Kh', 'Qd'], board=['5s', '5d', 'Kc', '2h', '9c'],
                  street='river', has_bet=False, pot_bb=20.0, players=2)
        quiet = st.decide(state(**kw))
        vs_aggressor = st.decide(state(opp_aggressor=True, **kw))
        self.assertEqual(quiet['action'], 'raise', quiet['reason'])
        self.assertEqual(vs_aggressor['action'], 'check', vs_aggressor['reason'])
        self.assertIn('чек вместо тонкой ставки', vs_aggressor['reason'])

    def test_check_check_frees_the_thin_value_again(self):
        """Поднимал префлоп, но чекнул прошлую улицу — слабость показана, ставим."""
        d = st.decide(state(hole=['Kh', 'Qd'], board=['5s', '5d', 'Kc', '2h', '9c'],
                            street='river', has_bet=False, pot_bb=20.0, players=2,
                            opp_aggressor=True, opp_checked=True))
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertIn('чек-чек', d['reason'])

    def test_lines_are_read_from_the_state(self):
        s = state(opp_aggressor=True, opp_checked=True, opp_bet_streets=2)
        self.assertEqual(st.opponent_lines(s),
                         {'aggressor': True, 'checked': True, 'bet_streets': 2})
        off = dict(st.DEFAULT_SETTINGS, opponent_lines=False)
        self.assertEqual(st.opponent_lines(s, off), st.EMPTY_LINES)
        self.assertEqual(st.opponent_lines(state()), st.EMPTY_LINES)


class StubScreen:
    def grab(self):
        return None

    def tap(self, x, y):
        pass


class LineTrackingTest(unittest.TestCase):
    """Бот сам считает линию оппонента по кадрам своего хода (main.Bot)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_lines_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bot = Bot(StubScreen(), players_path=os.path.join(self.tmp, 'players.json'),
                       log_path=os.path.join(self.tmp, 'bot.log'),
                       history_path=os.path.join(self.tmp, 'hands.jsonl'))

    def turn(self, street, has_bet=False, to_call=None, acted=None):
        """Кадр нашего хода на улице + наш ход на ней (как это делает step)."""
        self.bot.track_lines({'street': street, 'has_bet': has_bet, 'to_call_bb': to_call})
        if acted:
            self.bot._line_acted[street] = acted

    def lines(self, street):
        return self.bot.opponent_lines({'street': street})

    def test_preflop_raise_marks_the_aggressor(self):
        self.turn('preflop', has_bet=True, to_call=3.0, acted='call')
        self.assertTrue(self.lines('flop')['opp_aggressor'])

    def test_a_limp_is_not_a_raise(self):
        """Колл в размер блайнда — это не поднятие (порог тот же, что у PFR)."""
        self.turn('preflop', has_bet=True, to_call=1.0, acted='call')
        self.assertFalse(self.lines('flop')['opp_aggressor'])

    def test_our_own_raise_is_not_theirs(self):
        self.bot.raised_preflop = True
        self.turn('preflop', has_bet=True, to_call=6.0, acted='call')
        self.assertFalse(self.lines('flop')['opp_aggressor'])

    def test_unreadable_amount_does_not_invent_a_raise(self):
        """Суммы в кадре нет — тайтоветь на выдумке нельзя."""
        self.turn('preflop', has_bet=True, to_call=None, acted='call')
        self.assertFalse(self.lines('flop')['opp_aggressor'])

    def test_check_check_on_the_previous_street(self):
        self.turn('flop', has_bet=False, acted='check')
        self.assertTrue(self.lines('turn')['opp_checked'])

    def test_our_own_bet_is_not_a_check_check(self):
        self.turn('flop', has_bet=False, acted='raise')
        self.assertFalse(self.lines('turn')['opp_checked'])

    def test_a_bet_on_that_street_is_not_a_check_check(self):
        self.turn('flop', has_bet=False, acted='check')
        self.turn('flop', has_bet=True, to_call=5.0, acted='call')   # он поставил после чека
        self.assertFalse(self.lines('turn')['opp_checked'])

    def test_bet_streets_count_postflop_only(self):
        self.turn('preflop', has_bet=True, to_call=3.0, acted='call')
        self.turn('flop', has_bet=True, to_call=5.0, acted='call')
        self.assertEqual(self.lines('flop')['opp_bet_streets'], 1)
        self.turn('turn', has_bet=True, to_call=9.0, acted='call')
        self.assertEqual(self.lines('turn')['opp_bet_streets'], 2)

    def test_a_new_hand_forgets_the_line(self):
        self.turn('preflop', has_bet=True, to_call=3.0, acted='call')
        self.turn('flop', has_bet=True, to_call=5.0, acted='call')
        self.bot.close_hand()
        self.assertEqual(self.lines('turn'),
                         {'opp_aggressor': False, 'opp_checked': False,
                          'opp_bet_streets': 0})

    def test_flag_off_sends_nothing_to_the_strategy(self):
        self.bot.chart.settings['opponent_lines'] = False
        self.turn('preflop', has_bet=True, to_call=3.0, acted='call')
        self.assertEqual(self.lines('flop'), {})

    def test_lines_reach_the_decision(self):
        """Сквозь Bot.decide: линия доезжает до стратегии и меняет решение."""
        self.turn('preflop', has_bet=True, to_call=3.0, acted='call')
        s = {'hole': ['Ah', 'Jc'], 'board': ['Ad', '9s', '2c'], 'street': 'turn',
             'has_bet': True, 'to_call_bb': 6.0, 'pot_bb': 10.0, 'players': 2,
             'position': 'BB'}
        self.bot.stack_bb = 100.0
        d = self.bot.decide(s)
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('агрессор префлопа', d['reason'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
