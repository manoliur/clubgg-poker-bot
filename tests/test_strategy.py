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

    def test_seated_players_decide_tactic_not_in_hand(self):
        """Тактика зависит от числа СИДЯЩИХ, а не от числа в раздаче.

        Живой баг: на столе 4-max один оппонент сфолдил -> players=2, и бот
        включал HU-диапазон (K5o рейз как в хедз-апе). С 4 сидящими даже при
        2 в раздаче это 6-max стол: K5o на SB — вне диапазона открытия.
        """
        hand = ['K5o'[0] + 'h', '5d']      # K5o
        # сидят 4 (players_seated), в раздаче 2 (players) — стол 4-max, не HU
        d = st.decide(state(hole=hand, position='SB', players=2, players_seated=4))
        self.assertEqual(d['action'], 'check',
                         '4-max стол: HU-тактика не применяется')
        # контроль: реальный HU (сидят 2) — K5o рейзится
        d = st.decide(state(hole=hand, position='SB', players=2, players_seated=2))
        self.assertEqual(d['action'], 'raise', 'настоящий HU: широкая тактика')
        # контроль: 6-max стол — K5o на SB чек/фолд
        d = st.decide(state(hole=hand, position='SB', players=2, players_seated=6))
        self.assertEqual(d['action'], 'check', '6-max стол: HU-тактика не применяется')

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


class AllInDefenceTest(unittest.TestCase):
    """Живая раздача 19.08 09:52 #27: бот заколлил алл-ин 23.7ББ с 76s.

    Стол 3-max (один вне раздачи), поз=BTN, чарт gto_6max — в нём 76s входит в
    диапазон 3-бета (блеф-3-бет против открытия). На 4-бет-алл-ин оппонента
    стратегия снова выдала «3-бет на велью», живого пресета рейза не было, и
    главный цикл молча подменил рейз коллом 23.7ББ (34% стека).
    """

    CHART = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'charts', 'gto_6max.json')
    STACK = 69.6

    def setUp(self):
        self.chart = st.load_chart(self.CHART)

    def hand(self, **kw):
        base = {'hole': ['7d', '6d'], 'position': 'BTN', 'players': 2,
                'players_seated': 3, 'has_bet': True, 'to_call_bb': 23.7,
                'pot_bb': 51.7}
        base.update(kw)
        return st.decide(state(**base), stack_bb=self.STACK, chart=self.chart)

    def test_three_bet_still_fires_against_a_normal_open(self):
        """09:52:19 — открытие 0.5ББ: блеф-3-бет с 76s по чарту законен."""
        d = self.hand(to_call_bb=0.5, pot_bb=1.5)
        self.assertEqual(d['action'], 'raise', d['reason'])

    def test_three_bet_range_not_applied_against_4bet(self):
        """AQ против 4-бета — фолд, а не «3-бет на велью» (5-бет).

        Живой кейс 20.08: пользователь с AA рейзит, бот с AQ «на велью»
        отвечал 3-бетом и доходил до алл-ина. Против 3-бета+ диапазон
        3-бета не применяется: 4-бет только QQ+/AK, колл 3-бета только
        премиум, против 4-бета премиум без монстра тоже пасует.
        """
        # AQs против 4-бета 20ББ — фолд (был 5-бет «3-бет на велью»)
        d = self.hand(hole=['As', 'Qs'], to_call_bb=20.0, pot_bb=32.0)
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('4-бета', d['reason'])
        # AQo против обычного 3-бета — фолд
        d = self.hand(hole=['As', 'Qh'], to_call_bb=8.0, pot_bb=12.0)
        self.assertEqual(d['action'], 'fold', d['reason'])
        # AQs против обычного 3-бета — колл премиумом
        d = self.hand(hole=['As', 'Qs'], to_call_bb=8.0, pot_bb=12.0)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertIn('премиум', d['reason'])
        # монстр 4-бетит
        d = self.hand(hole=['Ks', 'Kd'], to_call_bb=20.0, pot_bb=32.0)
        self.assertEqual(d['action'], 'raise', d['reason'])
        # 3-бет против обычного открытия остаётся законным
        d = self.hand(hole=['As', 'Qh'], to_call_bb=2.5, pot_bb=4.5)
        self.assertEqual(d['action'], 'raise', d['reason'])

    def test_suited_connector_folds_to_all_in(self):
        """09:52:27 — 4-бет/алл-ин 23.7ББ: это уже колл/фолд, а не 3-бет."""
        d = self.hand()
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('крупной ставки', d['reason'])
        self.assertIn('стека', d['reason'])

    def test_no_raise_flag_blocks_the_three_bet(self):
        """Рейза в клиенте нет — чарт 3-бета не применяется даже к мелкой ставке."""
        d = self.hand(to_call_bb=0.5, pot_bb=1.5, no_raise=True)
        self.assertNotEqual(d['action'], 'raise')
        self.assertIn('против алл-ина', d['reason'])

    def test_premium_calls_the_all_in(self):
        """AKo/JJ против алл-ина коллируют, а не пасуют.

        В GTO-чарте диапазон колла на BTN — «77, 88, ATs»: всё сильное лежит в
        3-бете и 4-бете. Без переноса этих рук в колл премиум пасовал бы.
        """
        for hole in (['Ah', 'Kd'], ['Jh', 'Jd'], ['Ah', 'Qh']):
            with self.subTest(hole=hole):
                d = self.hand(hole=hole, no_raise=True)
                self.assertEqual(d['action'], 'call', d['reason'])
                self.assertIn('пот-оддсам', d['reason'])

    def test_trash_folds_to_all_in(self):
        d = self.hand(hole=['7h', '2c'], no_raise=True)
        self.assertEqual(d['action'], 'fold')
        self.assertIn('против алл-ина', d['reason'])

    def test_no_raise_postflop_calls_with_value_and_folds_the_bluff(self):
        """Постфлоп: сет коллит вместо рейза, полу-блеф отменяется (блефовать нечем)."""
        made = st.decide(state(hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop',
                               has_bet=True, to_call_bb=3.0, pot_bb=6.0, players=2,
                               no_raise=True))
        self.assertEqual(made['action'], 'call', made['reason'])
        self.assertIn('рейз недоступен', made['reason'])
        # флеш-дро против дорогой ставки: с рейзом это полу-блеф, без него — фолд
        draw = state(hole=['Ah', '5h'], board=['Kh', '9h', '2c'], street='flop',
                     has_bet=True, to_call_bb=2.5, pot_bb=3.0, players=2)
        self.assertEqual(st.decide(draw)['action'], 'raise')
        self.assertEqual(st.decide({**draw, 'no_raise': True})['action'], 'fold')

    def test_no_raise_without_bet_is_check(self):
        for hole in (['Ah', 'Ad'], ['7h', '2c']):
            with self.subTest(hole=hole):
                pre = st.decide(state(hole=hole, has_bet=False, no_raise=True))
                self.assertEqual(pre['action'], 'check', pre['reason'])
                post = st.decide(state(hole=hole, board=['Ad', 'Kd', '2c'], street='flop',
                                       has_bet=False, pot_bb=6.0, players=2,
                                       no_raise=True))
                self.assertEqual(post['action'], 'check', post['reason'])


class BigOpenTest(unittest.TestCase):
    """Крупное открытие — это открытие, а не 3-бет (пока мы сами не поднимали).

    Защита «против 3-бета+ играем только премиумом» отличала ререйз от открытия
    по размеру: доплата больше 1.6 открытия (4ББ) считалась 3-бетом. На живых
    столах открывают и в 4.5-5ББ, и бот пасовал TT, 99, AJo, KQs — весь диапазон
    колла — против одного-единственного рейза. Теперь порог зависит от того,
    поднимали ли МЫ: свой рейз в раздаче видит главный цикл (Bot.raised_preflop).
    """

    def hand(self, hole, to_call, **kw):
        base = {'hole': hole, 'position': 'BTN', 'players': 6, 'players_seated': 6,
                'has_bet': True, 'to_call_bb': to_call, 'pot_bb': to_call + 1.5}
        base.update(kw)
        return st.decide(state(**base), stack_bb=100.0)

    def test_a_plain_big_open_keeps_the_calling_range(self):
        for hole in (['Th', 'Td'], ['9h', '9d'], ['Ah', 'Js'], ['Kh', 'Qh']):
            for to_call in (4.5, 5.0, 5.5):
                with self.subTest(hole=hole, to_call=to_call):
                    # банк: открытие плюс блайнды и лимперы, цена колла разумная
                    d = self.hand(hole, to_call, pot_bb=to_call + 4.0)
                    self.assertNotIn('против 3-бета+', d['reason'])
                    self.assertIn(d['action'], ('call', 'raise'), d['reason'])

    def test_after_our_raise_the_same_bet_is_a_reraise(self):
        for hole in (['Th', 'Td'], ['9h', '9d'], ['Ah', 'Js'], ['Kh', 'Qh']):
            with self.subTest(hole=hole):
                d = self.hand(hole, 4.5, hero_raised=True)
                self.assertEqual(d['action'], 'fold', d['reason'])
                self.assertIn('против 3-бета+', d['reason'])

    def test_a_real_three_bet_is_caught_even_without_our_raise(self):
        """Холодный 3-бет (доплата от 3 открытий) защиту всё равно включает."""
        d = self.hand(['Th', 'Td'], 9.0)
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('против 3-бета+', d['reason'])

    def test_premium_still_folds_to_a_four_bet(self):
        """Тот самый живой кейс: AQs против 4-бета поверх нашего 3-бета — фолд."""
        d = self.hand(['As', 'Qs'], 20.0, pot_bb=32.0, hero_raised=True)
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('4-бета', d['reason'])

    def test_a_squeeze_over_a_raise_is_a_reraise(self):
        """Открытие 2.5ББ + коллер + сквиз до 7ББ.

        По размеру это «крупное открытие» (меньше трёх открытий), и раньше TT
        отвечала ему коллом. Но под ставкой лежит 5ББ — столько лимпами не
        набирается, значит до неё уже поднимали, и это ререйз.
        """
        d = self.hand(['Th', 'Td'], 7.0, pot_bb=13.5, players=5)
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('против 3-бета+', d['reason'])

    def test_an_iso_raise_over_limps_is_still_an_open(self):
        """Два лимпа и рейз до 5.5ББ — это изо-рейз: диапазон колла остаётся."""
        d = self.hand(['Th', 'Td'], 5.5, pot_bb=9.5, players=5)
        self.assertNotIn('против 3-бета+', d['reason'])
        self.assertIn(d['action'], ('call', 'raise'), d['reason'])


class PreflopMoneyTest(unittest.TestCase):
    """Что бот вычитает из банка: сколько вложено до нашего хода и кем."""

    def test_investors_count_the_limps_under_the_bet(self):
        self.assertEqual(st.preflop_investors(4.0, 2.5, 'BTN'), 1)   # голое открытие
        self.assertEqual(st.preflop_investors(9.5, 6.0, 'BTN'), 3)   # два лимпа и рейз
        # свой блайнд лежит в том же банке и вложением соседей не считается
        self.assertEqual(st.preflop_investors(9.5, 5.0, 'BB'), 3)

    def test_without_the_pot_there_is_nothing_to_count(self):
        self.assertIsNone(st.preflop_investors(None, 5.0, 'BTN'))
        self.assertIsNone(st.preflop_investors(9.5, None, 'BTN'))

    def test_a_squeeze_needs_more_money_than_limps_could_be(self):
        # два лимпа под ставкой (2.5ББ) — столько могли налимпить и вчетвером
        self.assertFalse(st.is_squeeze(9.5, 5.5, 'BTN', live_players=5))
        # 5ББ под ставкой при троих оппонентах лимпами уже не объяснить
        self.assertTrue(st.is_squeeze(13.5, 7.0, 'BTN', live_players=5))

    def test_a_squeeze_is_impossible_when_there_is_nobody_to_squeeze(self):
        """Хедз-ап и кадры без чисел: класть деньги под чужую ставку некому."""
        self.assertFalse(st.is_squeeze(13.5, 7.0, 'BTN', live_players=2))
        self.assertFalse(st.is_squeeze(13.5, 7.0, 'BTN', live_players=None))
        self.assertFalse(st.is_squeeze(None, 7.0, 'BTN', live_players=5))


class ShortStackReraiseTest(unittest.TestCase):
    """Короткий стек пушит по своим диапазонам — но только против ОТКРЫТИЯ.

    push/fold не смотрел, что перед ним ставка поверх: AQ уходила в алл-ин
    против 4-бета, у которого диапазон QQ+/AK.
    """

    STACK = 25.0

    def hand(self, hole, to_call, pot_bb=20.0, **kw):
        base = {'hole': hole, 'position': 'BTN', 'players': 2, 'players_seated': 2,
                'has_bet': True, 'to_call_bb': to_call, 'pot_bb': pot_bb,
                'hero_raised': True}
        base.update(kw)
        return st.decide(state(**base), stack_bb=self.STACK)

    def test_a_reraise_folds_out_everything_but_the_monsters(self):
        for hole in (['Ah', 'Qd'], ['Th', 'Td'], ['Ah', 'Js'], ['Kh', 'Qh']):
            with self.subTest(hole=hole):
                d = self.hand(hole, 10.0)
                self.assertEqual(d['action'], 'fold', d['reason'])
                self.assertIn('против 3-бета+', d['reason'])

    def test_monsters_and_premium_still_push(self):
        for hole in (['Ah', 'Kd'], ['Qh', 'Qd'], ['Jh', 'Jd'], ['Ah', 'Qh']):
            with self.subTest(hole=hole):
                d = self.hand(hole, 10.0)
                self.assertEqual(d['action'], 'raise', d['reason'])
                self.assertEqual(d['amount_bb'], self.STACK, d['reason'])

    def test_against_an_open_the_push_range_is_unchanged(self):
        """Перед нами не ререйз — короткий стек пушит как раньше."""
        d = self.hand(['Th', 'Td'], 2.5, pot_bb=4.0, hero_raised=False)
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertEqual(d['amount_bb'], self.STACK, d['reason'])

    def test_without_a_bet_the_push_range_is_unchanged(self):
        d = self.hand(['Ah', 'Td'], None, pot_bb=1.5, has_bet=False, hero_raised=False)
        self.assertEqual(d['action'], 'raise', d['reason'])
        self.assertIn('вместо рейза', d['reason'])


class ProfileThresholdTest(unittest.TestCase):
    """У каждой метрики профиля свой порог: VPIP копится быстрее агрессии."""

    @staticmethod
    def prof(hands, **kw):
        p = {'hands': hands, 'vpip': 0.5, 'pfr': 0.2, 'three_bet': 0.1, 'agg': 3.0,
             'three_bet_spots': 8, 'agg_bets': 12, 'agg_calls': 4}
        p.update(kw)
        return p

    def test_a_young_profile_is_trusted_metric_by_metric(self):
        young = self.prof(25)
        self.assertTrue(st.metric_ready(young, 'vpip'), 'VPIP считается каждую раздачу')
        for metric in ('pfr', 'three_bet', 'agg'):
            with self.subTest(metric=metric):
                self.assertFalse(st.metric_ready(young, metric))

    def test_a_grown_profile_is_trusted_whole(self):
        grown = self.prof(85)
        for metric in st.PROFILE_MIN_HANDS:
            with self.subTest(metric=metric):
                self.assertTrue(st.metric_ready(grown, metric))

    def test_an_empty_denominator_is_not_a_zero(self):
        """«3-бет 0%» при нуле спотов — это не пассивный оппонент, а нет наблюдений."""
        self.assertFalse(st.metric_ready(self.prof(85, three_bet_spots=0), 'three_bet'))
        self.assertFalse(st.metric_ready(self.prof(85, agg_bets=0, agg_calls=0), 'agg'))

    def test_the_thresholds_come_from_the_settings(self):
        loose = dict(st.DEFAULT_SETTINGS, min_hands_agg=20)
        self.assertFalse(st.metric_ready(self.prof(25), 'agg'))
        self.assertTrue(st.metric_ready(self.prof(25), 'agg', loose))

    def test_aggression_waits_for_its_own_threshold(self):
        """Тот же профиль: VPIP по нему уже работает, а агрессия ещё нет."""
        fold = st._d('fold', 'средняя рука дороговата')
        young = st.adjust_for_opponent(fold, self.prof(30), 'medium')
        self.assertEqual(young['action'], 'fold', 'на 30 руках агрессии ещё не верим')
        grown = st.adjust_for_opponent(fold, self.prof(85), 'medium')
        self.assertEqual(grown['action'], 'call', grown['reason'])

    def test_a_profile_without_counters_is_not_trusted(self):
        self.assertFalse(st.metric_ready(None, 'vpip'))
        self.assertFalse(st.metric_ready({'hands': 500}, 'выдуманная метрика'))


class PotOddsTest(unittest.TestCase):
    """Живая раздача 19.08 15:49 #21: 6s6c на 2s 8s Qs 4s.

    Бот собрал флеш с шестёркой, hand_class выдал «натс», и на алл-ин пошёл
    колл. Против алл-ина такую руку бьёт любая старшая пика — решать должны
    шансы банка, а не название комбинации.
    """

    BOARD = ['2s', '8s', 'Qs', '4s']

    def hand(self, hole=('6s', '6c'), **kw):
        base = {'hole': list(hole), 'board': self.BOARD, 'street': 'turn',
                'has_bet': True, 'players': 2, 'no_raise': True}
        base.update(kw)
        return st.decide(state(**base))

    def test_pot_odds_formula(self):
        self.assertAlmostEqual(st.pot_odds(3.0, 30.0), 3.0 / 33.0)
        self.assertAlmostEqual(st.pot_odds(10.0, 10.0), 0.5)
        self.assertIsNone(st.pot_odds(None, 10.0))

    def test_weak_flush_folds_to_all_in_at_a_bad_price(self):
        d = self.hand(to_call_bb=20.0, pot_bb=20.0)          # цена 50%
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('пот-оддсы 50%', d['reason'])
        self.assertIn('эквити ~30%', d['reason'])

    def test_weak_flush_calls_a_cheap_all_in(self):
        d = self.hand(to_call_bb=3.0, pot_bb=30.0)           # цена 9% < эквити 30%
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertIn('пот-оддсы 9%', d['reason'])

    def test_weak_flush_does_not_bet_for_value(self):
        d = self.hand(has_bet=False, no_raise=False, pot_bb=20.0)
        self.assertEqual(d['action'], 'check', d['reason'])

    def test_medium_flush_calls_where_weak_folds(self):
        price = {'to_call_bb': 10.0, 'pot_bb': 30.0}         # цена 25%
        self.assertEqual(self.hand(('Ts', '6c'), **price)['action'], 'call')
        self.assertEqual(self.hand(('6s', '6c'), **price)['action'], 'fold')

    def test_nut_flush_calls_the_all_in(self):
        d = self.hand(('As', '6c'), to_call_bb=20.0, pot_bb=20.0)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertIn('эквити ~85%', d['reason'])

    def test_nut_flush_still_raises_for_value(self):
        d = self.hand(('As', '6c'), to_call_bb=5.0, pot_bb=20.0, no_raise=False)
        self.assertEqual(d['action'], 'raise', d['reason'])
        # а младший флеш банк не растит даже против маленькой ставки
        self.assertEqual(self.hand(to_call_bb=5.0, pot_bb=20.0, no_raise=False)['action'],
                         'call')

    def test_low_straight_is_not_a_value_raise(self):
        d = st.decide(state(hole=['6c', '5d'], board=['2s', '3h', '4c', 'Kd'],
                            street='turn', has_bet=False, pot_bb=20.0, players=2))
        self.assertNotEqual(d['action'], 'raise', d['reason'])


class DrawPriceTest(unittest.TestCase):
    """Дро платит за ОДНУ карту, если после колла ставки продолжатся.

    Правило 4x («аутов x4») верно только когда денег больше не будет: на флопе
    против обычной ставки мы покупаем один тёрн, а за ривер придётся платить
    заново. Недобор компенсируют неявные пот-оддсы — добор на следующей улице.
    """

    FLUSH_DRAW = {'hole': ['As', '5s'], 'board': ['Ks', '9s', '2c'], 'street': 'flop',
                  'has_bet': True, 'players': 6}       # 6-max: полу-блеф не мешает

    def test_flop_draw_counts_one_card_against_a_normal_bet(self):
        # цена 33% банка: по правилу 4x «36%» звало коллить, по факту у нас 18%
        d = st.decide(state(**dict(self.FLUSH_DRAW, to_call_bb=10.0, pot_bb=20.0)),
                      stack_bb=100.0)
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('18%', d['reason'])

    def test_all_in_draw_counts_both_cards(self):
        """Та же цена, но колл — половина стека: карты будут обе, считаем 4x."""
        d = st.decide(state(**dict(self.FLUSH_DRAW, to_call_bb=10.0, pot_bb=20.0)),
                      stack_bb=20.0)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertIn('36%', d['reason'])

    def test_implied_odds_justify_a_cheap_call(self):
        """Дешёвая ставка: чистая цена 25% выше эквити, но добор её окупает."""
        d = st.decide(state(**dict(self.FLUSH_DRAW, to_call_bb=2.0, pot_bb=6.0)),
                      stack_bb=100.0)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertIn('имплайд', d['reason'])

    def test_no_implied_odds_against_an_all_in(self):
        """Против алл-ина добирать не с кого — считаем чистую цену."""
        cheap = state(**dict(self.FLUSH_DRAW, to_call_bb=2.0, pot_bb=6.0, no_raise=True))
        d = st.decide(cheap, stack_bb=100.0)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertNotIn('имплайд', d['reason'])

    def test_implied_price_capped_by_the_stack(self):
        self.assertAlmostEqual(st.implied_price(2.0, 6.0, 100.0, 1.0), 2.0 / 14.0)
        # в стеке осталось всего 3ББ — больше 3 мы не доберём
        self.assertAlmostEqual(st.implied_price(2.0, 6.0, 5.0, 1.0), 2.0 / 11.0)
        self.assertIsNone(st.implied_price(None, 6.0, 100.0, 1.0))

    def test_board_draw_is_not_paid_for(self):
        """4 карты масти на доске без нашей масти: коллить «по аутам» нечего.

        Цена 13% — по фальшивым 9 аутам флеша это был колл, хотя пика не даёт
        нам ничего (она даёт флеш оппоненту).
        """
        d = st.decide(state(hole=['6c', '6d'], board=['2s', '8s', 'Qs', '4s'],
                            street='turn', has_bet=True, to_call_bb=3.0, pot_bb=20.0,
                            players=2))
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertNotIn('дро', d['reason'])


class BigPreflopBetTest(unittest.TestCase):
    """Крупная ставка на префлопе: решает не только премиум, но и цена."""

    STACK = 69.6

    def hand(self, hole, to_call_bb, pot_bb):
        return st.decide(state(hole=hole, position='BTN', players=6, players_seated=6,
                               has_bet=True, to_call_bb=to_call_bb, pot_bb=pot_bb),
                         stack_bb=self.STACK)

    def test_good_price_calls_without_a_premium(self):
        """Алл-ин 20ББ в банк 100ББ — цена 17%, столько эквити есть у TT."""
        d = self.hand(['Th', 'Td'], 20.0, 100.0)
        self.assertEqual(d['action'], 'call', d['reason'])
        self.assertIn('пот-оддсы', d['reason'])

    def test_bad_price_still_folds(self):
        d = self.hand(['Th', 'Td'], 20.0, 20.0)          # цена 50%
        self.assertEqual(d['action'], 'fold', d['reason'])
        self.assertIn('пот-оддсы 50%', d['reason'])

    def test_price_does_not_open_the_door_to_trash(self):
        """Цена хорошая, но рука вне диапазона колла — всё равно фолд."""
        d = self.hand(['7h', '2c'], 20.0, 100.0)
        self.assertEqual(d['action'], 'fold', d['reason'])


class BetSizeTest(unittest.TestCase):
    def test_nuts_bet_bigger_than_a_strong_hand(self):
        nuts = st.decide(state(hole=['As', 'Ks'], board=['Qs', '9s', '2s'], street='flop',
                               has_bet=False, pot_bb=10.0, players=2))
        strong = st.decide(state(hole=['9h', '9c'], board=['9d', '5s', '2c'], street='flop',
                                 has_bet=False, pot_bb=10.0, players=2))
        self.assertEqual(nuts['action'], 'raise')
        self.assertGreater(nuts['pot_frac'], strong['pot_frac'])
        self.assertAlmostEqual(nuts['pot_frac'], 0.75)

    def test_sizes_are_pot_fractions_not_min_bets(self):
        """У каждой ставки есть доля банка — по ней бот выбирает пресет."""
        for kw in ({'hole': ['9h', '9c'], 'board': ['9d', '5s', '2c']},        # сет
                   {'hole': ['Ah', 'Jc'], 'board': ['Ad', '9s', '2c']},        # топ-пара
                   {'hole': ['Ah', '5h'], 'board': ['Kh', '9h', '2c']}):       # дро
            d = st.decide(state(street='flop', has_bet=False, pot_bb=10.0, players=2, **kw))
            with self.subTest(**kw):
                self.assertEqual(d['action'], 'raise', d['reason'])
                self.assertIsNotNone(d['pot_frac'])
                self.assertAlmostEqual(d['amount_bb'], round(10.0 * d['pot_frac'], 1))

    def test_no_bet_is_bigger_than_the_pot(self):
        """Пресета крупнее «100% банка» в клиенте нет — доля банка не выходит за 1.0.

        Блеф с блокером считался мимо bet_frac и при агрессии x2 просил 144%
        банка: тап уходил в тот же пресет 100%, а в лог и историю раздач
        попадал выдуманный размер.
        """
        chart = st.DEFAULT_CHART.copy()
        chart.settings = st.device_settings(
            st.DEFAULT_SETTINGS, {'style': 'aggressive', 'aggression': 2.0,
                                  'blocker_bluff': True, 'bet_sizing': True})
        spots = [
            {'hole': ['Ah', '2d'], 'board': ['Kh', '7h', '3h', '9c', '4s'],
             'street': 'river', 'bluff_ok': True},                    # блеф с блокером
            {'hole': ['As', 'Ks'], 'board': ['Qs', '9s', '2s'], 'street': 'flop'},
            {'hole': ['9h', '9c'], 'board': ['9d', '5s', '2c'], 'street': 'turn'},
        ]
        for kw in spots:
            d = st.decide(state(has_bet=False, pot_bb=20.0, players=2, **kw),
                          stack_bb=100.0, chart=chart)
            with self.subTest(**kw):
                self.assertEqual(d['action'], 'raise', d['reason'])
                self.assertLessEqual(d['pot_frac'], 1.0, d['reason'])
                self.assertLessEqual(d['amount_bb'], 20.0, d['reason'])


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

    def test_random_states_never_crash_and_keep_the_rules(self):
        """Случайные раздачи: решение всегда есть и всегда законно.

        Чек невозможен против ставки, колл/фолд — без ставки; любые None в
        числах банка допустимы.
        """
        import random
        import hand_evaluator as he
        deck = [f'{r}{s}' for r in he.RANKS for s in he.SUITS]
        rnd = random.Random(7)
        for _ in range(2000):
            cards = rnd.sample(deck, 7)
            n = rnd.choice([0, 3, 4, 5])
            has_bet = rnd.random() < 0.6
            s = state(hole=cards[:2], board=cards[2:2 + n],
                      street={0: 'preflop', 3: 'flop', 4: 'turn', 5: 'river'}[n],
                      has_bet=has_bet,
                      to_call_bb=rnd.choice([None, 0.5, 2.0, 10.0, 35.0]) if has_bet else None,
                      pot_bb=rnd.choice([None, 1.5, 6.0, 20.0, 60.0]),
                      position=rnd.choice([None, 'UTG', 'MP', 'CO', 'BTN', 'SB', 'BB']),
                      players=rnd.choice([2, 3, 6]),
                      players_seated=rnd.choice([2, 3, 6]),
                      no_raise=rnd.random() < 0.2)
            d = st.decide(s, stack_bb=rnd.choice([10.0, 69.6, 200.0]))
            self.assertIn(d['action'], ('fold', 'check', 'call', 'raise'), s)
            if has_bet:
                self.assertNotEqual(d['action'], 'check', s)
            else:
                self.assertIn(d['action'], ('check', 'raise'), s)

    def test_loose_opponent_kills_bluff(self):
        base = state(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], street='flop',
                     has_bet=False, pot_bb=6.0, players=2)
        self.assertEqual(st.decide(base)['action'], 'raise')
        loose = st.decide(base, profile={'hands': 30, 'vpip': 0.55, 'agg': 0.5})
        self.assertEqual(loose['action'], 'check')

    def test_fresh_profile_is_not_trusted_yet(self):
        """Одна раздача — ещё не статистика: VPIP 100% после первой руки бывает у всех."""
        base = state(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], street='flop',
                     has_bet=False, pot_bb=6.0, players=2)
        fresh = {'hands': st.MIN_PROFILE_HANDS - 1, 'vpip': 1.0, 'agg': 0.5}
        self.assertEqual(st.decide(base, profile=fresh)['action'], 'raise')
        ripe = dict(fresh, hands=st.MIN_PROFILE_HANDS)
        self.assertEqual(st.decide(base, profile=ripe)['action'], 'check')


if __name__ == '__main__':
    unittest.main(verbosity=2)
