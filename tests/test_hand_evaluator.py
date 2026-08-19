#!/usr/bin/env python3
"""Юнит-тесты эвалюатора рук: известные комбинации и их порядок."""
import os
import sys
import random
import unittest
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from hand_evaluator import (        # noqa: E402
    evaluate, evaluate5, best_five, compare, describe, parse_card, BadCard,
    flush_draw, straight_draw, count_outs, hand_class,
    HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS,
    STRAIGHT_FLUSH, RANKS, SUITS)


class ParseTest(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_card('Ah'), (14, 'h'))
        self.assertEqual(parse_card('2c'), (2, 'c'))
        self.assertEqual(parse_card('td'), (10, 'd'))
        self.assertEqual(parse_card('10s'), (10, 's'))

    def test_bad(self):
        for bad in ['', 'X', 'Zh', 'Ax', None, '1h']:
            with self.assertRaises(BadCard):
                parse_card(bad)

    def test_duplicates_rejected(self):
        with self.assertRaises(BadCard):
            evaluate(['Ah', 'Ah', 'Kd', 'Qc', 'Js', '2h', '3h'])


class CategoryTest(unittest.TestCase):
    CASES = [
        (['Ah', 'Kh', 'Qh', 'Jh', 'Th'], STRAIGHT_FLUSH),
        (['5h', '4h', '3h', '2h', 'Ah'], STRAIGHT_FLUSH),   # стил-флеш «колесо»
        (['9c', '9d', '9h', '9s', 'Kd'], QUADS),
        (['Qc', 'Qd', 'Qh', '4s', '4d'], FULL_HOUSE),
        (['Ac', 'Jc', '8c', '5c', '2c'], FLUSH),
        (['9c', '8d', '7h', '6s', '5d'], STRAIGHT),
        (['Ac', '2d', '3h', '4s', '5d'], STRAIGHT),          # колесо
        (['Tc', 'Td', 'Th', '6s', '2d'], TRIPS),
        (['Kc', 'Kd', '7h', '7s', '2d'], TWO_PAIR),
        (['Ac', 'Ad', '9h', '5s', '2d'], PAIR),
        (['Ac', 'Jd', '9h', '5s', '2d'], HIGH_CARD),
    ]

    def test_categories(self):
        for cards, cat in self.CASES:
            self.assertEqual(evaluate5(cards)[0], cat, cards)

    def test_names(self):
        self.assertEqual(describe(evaluate5(['Ac', 'Ad', '9h', '5s', '2d'])), 'пара A')
        self.assertEqual(describe(evaluate5(['9c', '8d', '7h', '6s', '5d'])), 'стрит до 9')
        self.assertEqual(describe(evaluate5(['Ac', '2d', '3h', '4s', '5d'])), 'стрит до 5')

    def test_ordering_of_categories(self):
        scores = [evaluate5(c) for c, _ in self.CASES]
        cats = [s[0] for s in scores]
        for i in range(len(cats)):
            for j in range(len(cats)):
                if cats[i] > cats[j]:
                    self.assertGreater(scores[i], scores[j], (self.CASES[i], self.CASES[j]))


class TiebreakTest(unittest.TestCase):
    def test_kicker_decides(self):
        self.assertEqual(compare(['Ac', 'Ad', 'Kh', '5s', '2d'],
                                 ['Ah', 'As', 'Qh', '5c', '2c']), 1)

    def test_higher_pair_wins(self):
        self.assertEqual(compare(['Kc', 'Kd', '9h', '5s', '2d'],
                                 ['Qc', 'Qd', 'Ah', 'Js', 'Td']), 1)

    def test_two_pair_top_pair_decides(self):
        self.assertEqual(compare(['Ac', 'Ad', '2h', '2s', '9d'],
                                 ['Kc', 'Kd', 'Qh', 'Qs', 'Jd']), 1)

    def test_flush_compared_by_all_cards(self):
        self.assertEqual(compare(['Ah', 'Qh', '9h', '5h', '3h'],
                                 ['Ah', 'Qh', '9h', '5h', '2h'.replace('2h', '2h')]), 1)

    def test_split_pot(self):
        # доска даёт обоим один и тот же стрит
        board = ['9c', '8d', '7h', '6s', '2d']
        self.assertEqual(compare(['Th', '3c'] + board, ['Td', '4c'] + board), 0)

    def test_wheel_loses_to_six_high_straight(self):
        self.assertEqual(compare(['Ac', '2d', '3h', '4s', '5d'],
                                 ['2c', '3d', '4h', '5s', '6d']), -1)

    def test_straight_flush_beats_quads(self):
        self.assertEqual(compare(['5h', '4h', '3h', '2h', 'Ah'],
                                 ['9c', '9d', '9h', '9s', 'Kd']), 1)


class SevenCardTest(unittest.TestCase):
    def test_picks_best_five(self):
        score, five = best_five(['As', 'Ks', 'Qs', 'Js', 'Ts', '2c', '2d'])
        self.assertEqual(score[0], STRAIGHT_FLUSH)
        self.assertEqual(sorted(five), sorted(['As', 'Ks', 'Qs', 'Js', 'Ts']))

    def test_board_plays(self):
        board = ['Ah', 'Kh', 'Qh', 'Jh', 'Th']
        self.assertEqual(compare(['2c', '3d'] + board, ['2s', '3h'] + board), 0)

    def test_full_house_from_seven(self):
        score = evaluate(['Ah', 'Ad', 'Ac', 'Kh', 'Kd', '5s', '2c'])
        self.assertEqual(score[0], FULL_HOUSE)
        self.assertEqual(score[1], 14)
        self.assertEqual(score[2], 13)

    def test_flush_of_six_uses_top_five(self):
        score, five = best_five(['Ah', 'Kh', 'Qh', 'Jh', '9h', '2h', '3c'])
        self.assertEqual(score[0], FLUSH)
        self.assertNotIn('2h', five)

    def test_evaluate_matches_bruteforce(self):
        """evaluate(7) == max по всем сочетаниям 5 из 7 (случайные раздачи)."""
        deck = [f'{r}{s}' for r in RANKS for s in SUITS]
        rnd = random.Random(1234)
        for _ in range(300):
            cards = rnd.sample(deck, 7)
            brute = max(evaluate5(list(c)) for c in combinations(cards, 5))
            self.assertEqual(evaluate(cards), brute, cards)


class DrawTest(unittest.TestCase):
    def test_flush_draw(self):
        self.assertEqual(flush_draw(['Ah', '2h', '7h', 'Th', '9c']), 'h')
        self.assertIsNone(flush_draw(['Ah', '2h', '7h', 'Ts', '9c']))
        self.assertIsNone(flush_draw(['Ah', '2h', '7h', 'Th', '9h']), 'готовый флеш — не дро')

    def test_open_ended(self):
        # 9-8-7-6: закрывают и T, и 5 -> два значения аутов
        self.assertEqual(straight_draw(['9c', '8d', '7h', '6s', '2c']), 'open')

    def test_gutshot(self):
        self.assertEqual(straight_draw(['9c', '8d', '7h', '5s']), 'gutshot')
        # A-K-Q-J закрывает только T (сверху стрит не продолжить) -> 4 аута
        self.assertEqual(straight_draw(['Ac', 'Kd', 'Qh', 'Js', '7c']), 'gutshot')

    def test_no_draw(self):
        self.assertIsNone(straight_draw(['Ac', '9d', '5h', '2s']))
        self.assertIsNone(straight_draw(['9c', '8d', '7h', '2s']), 'три подряд — ещё не дро')

    def test_made_straight_is_not_draw(self):
        self.assertIsNone(straight_draw(['9c', '8d', '7h', '6s', '5c']))

    def test_outs(self):
        self.assertEqual(count_outs(['Ah', 'Kh'], ['2h', '7h', '9c']), 9)
        self.assertEqual(count_outs(['9h', '8c'], ['7d', '6s', '2c']), 8)
        self.assertEqual(count_outs(['7h', '7c'], ['Ad', 'Ks', '2c']), 2)

    def test_combo_draw_outs_are_not_double_counted(self):
        """Флеш-дро + двусторонний = 15 аутов, а не 9+8: две карты общие."""
        self.assertEqual(count_outs(['Jh', 'Th'], ['9h', '8c', '2h']), 15)
        # флеш-дро + гатшот: одна общая карта -> 9 + 3
        self.assertEqual(count_outs(['Ah', 'Kh'], ['Qh', 'Jc', '2h']), 12)

    def test_flush_draw_on_the_board_is_not_ours(self):
        """4 карты масти на доске, а в руке этой масти нет — дро чужое."""
        board = ['2s', '8s', 'Qs', '4s']
        self.assertIsNone(flush_draw(['6c', '6d'] + board, hole=['6c', '6d']))
        self.assertEqual(flush_draw(['6s', '6d'] + board[:3], hole=['6s', '6d']), 's')
        self.assertEqual(hand_class(['6c', '6d'], board)['draws'], [])
        self.assertEqual(count_outs(['6c', '6d'], board), 2, 'остаётся только сет')

    def test_straight_draw_on_the_board_is_not_ours(self):
        """Доска 5-6-7-8: девятку/четвёрку получают все, это не наши ауты."""
        board = ['5h', '6s', '7c', '8d']
        self.assertIsNone(straight_draw(['Ac', '2d'] + board, board=board))
        self.assertEqual(count_outs(['Ac', '2d'], board), 0)
        # а вот свои карты в стрите — настоящее дро
        self.assertEqual(straight_draw(['8d', '7h', '5h', '6s', '2c'],
                                       board=['5h', '6s', '2c']), 'open')


class HandClassTest(unittest.TestCase):
    def test_overpair(self):
        c = hand_class(['Qh', 'Qc'], ['9d', '5s', '2c'])
        self.assertEqual(c['pair_type'], 'overpair')
        self.assertEqual(c['made'], 'medium')

    def test_top_pair(self):
        c = hand_class(['Ah', 'Jc'], ['Ad', '9s', '2c'])
        self.assertEqual(c['pair_type'], 'top')
        self.assertEqual(c['made'], 'medium')

    def test_bottom_pair_is_weak(self):
        c = hand_class(['2h', 'Jc'], ['Ad', '9s', '2c'])
        self.assertEqual(c['pair_type'], 'bottom')
        self.assertEqual(c['made'], 'weak')

    def test_set_is_strong(self):
        c = hand_class(['9h', '9c'], ['9d', '5s', '2c'])
        self.assertEqual(c['made'], 'strong')
        self.assertEqual(c['pair_type'], 'set')

    def test_flush_is_nuts_class(self):
        c = hand_class(['Ah', 'Kh'], ['2h', '7h', '9h'])
        self.assertEqual(c['made'], 'nuts')

    def test_air_with_draw(self):
        c = hand_class(['Ah', 'Kh'], ['2h', '7h', '9c'])
        self.assertEqual(c['made'], 'draw')
        self.assertIn('flush', c['draws'])

    def test_pure_air(self):
        c = hand_class(['Ah', 'Kc'], ['2d', '7s', '9c'])
        self.assertEqual(c['made'], 'air')

    def test_preflop_no_board(self):
        c = hand_class(['Ah', 'Kc'], [])
        self.assertIsNone(c['score'])
        self.assertEqual(c['made'], 'unknown')


class MadeHandRankTest(unittest.TestCase):
    """Флеш и стрит — не автоматически натс: решает ранг НАШЕЙ карты.

    Живая раздача 19.08 15:49 #21: 6s6c на 2s 8s Qs 4s — «флеш Q», но своя
    флеш-карта шестёрка, и любая пика 7,9,T,J,K,A у оппонента бьёт нас.
    """

    LIVE_BOARD = ['2s', '8s', 'Qs', '4s']

    def test_low_flush_card_is_weak(self):
        c = hand_class(['6s', '6c'], self.LIVE_BOARD)
        self.assertEqual(c['name'], 'флеш Q')
        self.assertEqual(c['made'], 'weak', c['made_note'])
        self.assertIn('6s', c['made_note'])

    def test_flush_grades_by_our_card(self):
        for hole, made in ((['As', '6c'], 'nuts'), (['Ks', '6c'], 'strong'),
                           (['Ts', '6c'], 'medium'), (['7s', '6c'], 'medium'),
                           (['6s', '6c'], 'weak'), (['3s', '6c'], 'weak')):
            with self.subTest(hole=hole):
                self.assertEqual(hand_class(hole, self.LIVE_BOARD)['made'], made)

    def test_nut_flush_is_nuts(self):
        self.assertEqual(hand_class(['Ah', '2h'], ['Kh', '9h', '7h'])['made'], 'nuts')

    def test_king_flush_is_nuts_when_ace_is_on_the_board(self):
        """Туза масти нет ни у кого: наш король — лучший возможный флеш."""
        self.assertEqual(hand_class(['Kh', '2c'], ['Ah', '9h', '7h', '5h'])['made'], 'nuts')

    def test_flush_entirely_on_the_board_is_weak(self):
        """Своей карты масти нет — нас бьёт любая карта масти у оппонента."""
        c = hand_class(['6c', '2d'], ['Ah', 'Kh', 'Qh', 'Jh', '3h'])
        self.assertEqual(c['made'], 'weak', c['made_note'])

    def test_low_straight_is_not_nuts(self):
        c = hand_class(['6c', '5d'], ['2s', '3h', '4c', 'Kd'])
        self.assertEqual(c['name'], 'стрит до 6')
        self.assertEqual(c['made'], 'medium', c['made_note'])

    def test_broadway_straight_is_nuts(self):
        self.assertEqual(hand_class(['Ah', 'Td'], ['Js', 'Qh', 'Kc', '2d'])['made'], 'nuts')

    def test_straight_with_higher_one_possible_is_medium(self):
        """T-J-Q на доске: наш стрит до Q бьётся любым AK."""
        c = hand_class(['9h', '8d'], ['Ts', 'Jh', 'Qc', '2d'])
        self.assertEqual(c['made'], 'medium')
        self.assertIn('до A', c['made_note'])

    def test_four_flush_board_downgrades_our_set(self):
        strong = hand_class(['9h', '9d'], ['9s', 'Kh', '2s', '7c', '4c'])
        self.assertEqual(strong['made'], 'strong')
        weak = hand_class(['9h', '9d'], ['9s', 'Kh', '2s', '7s', '4s'])
        self.assertEqual(weak['made'], 'medium', weak['made_note'])
        self.assertIn('флеш', weak['made_note'])

    def test_full_house_under_a_board_pair_is_medium(self):
        self.assertEqual(hand_class(['Kh', 'Kd'], ['Ks', '2s', '2h', '7c', '4c'])['made'],
                         'strong')
        low = hand_class(['2h', '2d'], ['2s', 'Ks', 'Kh', '7c', '4c'])
        self.assertEqual(low['made'], 'medium', low['made_note'])

    def test_two_pair_with_the_board_pair_is_not_strong(self):
        """Доска K K 5 и наши A5: «две пары» есть у всех, у нас пятёрка с тузом."""
        c = hand_class(['Ac', '5d'], ['Ks', 'Kd', '5c', '2h', '9s'])
        self.assertEqual(c['made'], 'weak', c['made_note'])
        self.assertIn('на доске', c['made_note'])
        # обе пары на доске — мы играем один кикер
        both = hand_class(['Ac', '7d'], ['Ks', 'Kd', '5c', '5h', '9s'])
        self.assertEqual(both['made'], 'weak', both['made_note'])
        # своя карманная пара выше пары доски — настоящие две пары
        self.assertEqual(hand_class(['Ac', 'Ad'], ['9s', '9d', '4c'])['made'], 'strong')
        # ...а ниже пары доски — нет: любой король бьёт нас трипсом
        self.assertEqual(hand_class(['9c', '9d'], ['Ks', 'Kd', '4c'])['made'], 'weak')
        # доска без пары — две пары остаются сильной рукой
        self.assertEqual(hand_class(['Ah', 'Kc'], ['Ad', 'Kd', '2c'])['made'], 'strong')

    def test_two_pair_under_board_pair_is_weak(self):
        """Живая раздача 19.08 #58: Ac4c на 8s8d4s6hKd — «две пары 8/4».

        Пара 8 целиком на доске (общая у всех), наша пара 4 мельче — рука
        слабая: любая 8 (трипс), пара 6/7 (8866/8877) или пара доски с лучшим
        кикером бьют нас. Раньше был 'medium' — бот коллил ставку на ривере
        и проигрывал оппоненту с K8.
        """
        c = hand_class(['Ac', '4c'], ['8s', '8d', '4s', '6h', 'Kd'])
        self.assertEqual(c['made'], 'weak', c['made_note'])
        self.assertIn('8', c['made_note'])
        # то же на флопе
        flop = hand_class(['Ac', '4c'], ['8s', '8d', '4s'])
        self.assertEqual(flop['made'], 'weak', flop['made_note'])
        # а вот карманная пара выше пары доски — по-прежнему сильная
        over = hand_class(['Qc', 'Qd'], ['8s', '8d', '4s'])
        self.assertEqual(over['made'], 'strong', over['made_note'])
        # пара с нашей картой, но старшая не с доски (AcQs на AA44) — средняя
        mixed = hand_class(['Ac', 'Qs'], ['5c', '4h', '4c', '9d', 'Ah'])
        self.assertEqual(mixed['made'], 'medium', mixed['made_note'])

    def test_underpair_with_one_overcard_is_medium(self):
        """QQ на K-7-2 — вторая пара с лучшим кикером, одну ставку она держит."""
        c = hand_class(['Qc', 'Qd'], ['Ks', '7d', '2c'])
        self.assertEqual(c['pair_type'], 'underpair')
        self.assertEqual(c['made'], 'medium', c['made_note'])
        # две карты выше — уже слабо
        self.assertEqual(hand_class(['Qc', 'Qd'], ['Ks', 'Ad', '2c'])['made'], 'weak')
        self.assertEqual(hand_class(['5c', '5d'], ['Ks', 'Qd', '2c'])['made'], 'weak')

    def test_playing_the_board_is_weak(self):
        """Свои карты ничего не добавляют: лучшее, на что мы играем, — сплит."""
        c = hand_class(['2c', '3d'], ['5h', '6s', '7c', '8d', '9h'])
        self.assertEqual(c['name'], 'стрит до 9')
        self.assertEqual(c['made'], 'weak', c['made_note'])
        self.assertIn('доску', c['made_note'])
        # непобиваемая доска — коллить с ней можно (сплит не проигрыш)
        self.assertEqual(hand_class(['2c', '3d'], ['Th', 'Js', 'Qc', 'Kd', 'Ah'])['made'],
                         'nuts')
        # своя карта в комбинации — доску мы не играем, скидки за сплит нет
        own = hand_class(['Tc', '3d'], ['5h', '6s', '7c', '8d', '9h'])
        self.assertEqual(own['name'], 'стрит до T')
        self.assertNotIn('доску', own['made_note'])

    def test_quads_and_straight_flush_stay_nuts(self):
        self.assertEqual(hand_class(['9h', '9d'], ['9s', '9c', 'Kh'])['made'], 'nuts')
        self.assertEqual(hand_class(['Ah', 'Kh'], ['Qh', 'Jh', 'Th'])['made'], 'nuts')


if __name__ == '__main__':
    unittest.main(verbosity=2)
