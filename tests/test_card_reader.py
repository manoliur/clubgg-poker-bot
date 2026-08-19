#!/usr/bin/env python3
"""Тесты распознавания карт на синтетических кадрах.

Повторяют реальный рабочий цикл: собрать эталоны по размеченным кадрам
(build_templates) -> распознать карты на ДРУГИХ кадрах (card_reader).
Проверяют то, что раньше ломалось: мои карты в веере, разный масштаб карт
(доска крупнее моих), ранг «10», различение h/d и c/s.
"""
import os
import sys
import shutil
import tempfile
import unittest

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import card_reader                       # noqa: E402
from build_templates import collect      # noqa: E402
from tests import synth                  # noqa: E402

RANKS = card_reader.RANK_ORDER
SUITS = card_reader.SUITS


def _all_cards():
    return [f'{r}{s}' for r in RANKS for s in SUITS]


class CardReaderTest(unittest.TestCase):
    tmp = None
    tpl = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='clubgg_test_')
        cls.tpl = os.path.join(cls.tmp, 'templates')
        # обучающие кадры: каждая карта хотя бы раз, на доске (крупные карты)
        labels = []
        cards = _all_cards()
        for i in range(0, len(cards), 5):
            chunk = cards[i:i + 5]
            path = os.path.join(cls.tmp, f'train{i}.png')
            synth.save(path, board=chunk, hole=[])
            labels.append({'file': path, 'zone': 'board', 'cards': chunk})
        n_rank, n_suit, skipped = collect(labels, base=cls.tmp, tpl_dir=cls.tpl, verbose=False)
        cls.build_result = (n_rank, n_suit, skipped)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, **kw):
        path = os.path.join(self.tmp, 'frame.png')
        synth.save(path, **kw)
        img = cv2.imread(path)
        return card_reader.read_table(img, tpl_dir=self.tpl)

    def test_templates_built(self):
        n_rank, n_suit, skipped = self.build_result
        self.assertEqual(skipped, [])
        self.assertEqual(n_suit, 4)
        self.assertEqual(n_rank, 13, 'все ранги включая T (живой шрифт компактный, '
                                     'wide=0.7<0.9 — без эталона десятка не читалась)')

    def test_board_detected(self):
        boxes = card_reader.find_board_cards(
            synth.render(board=['Ah', 'Kd', 'Qs', 'Jc', 'Tc']))
        self.assertEqual(len(boxes), 5)

    def test_hole_boxes_split_from_fan(self):
        """Фиксированные окна индексов (my_index_rects): обе карты дают окна.

        Окна берутся из config.HERO_INDEX_RECTS и отдаются только там, где реально
        лежит лицо карты: с картами их два (слева направо), без карт — ни одного.
        """
        img = synth.render(hole=['Ah', 'Kd'])
        rects = card_reader.my_index_rects(img)
        self.assertEqual(len(rects), 2)
        self.assertLess(rects[0][0], rects[1][0])
        self.assertEqual(card_reader.my_index_rects(synth.render(hole=[])), [])

    def test_my_cards_recognized(self):
        """Главный баг: мои карты возвращали '??'."""
        res = self.read(hole=['Jh', '2d'], board=['4h', '3s', 'Kc', '5c', 'Ad'])
        self.assertEqual(res['hole'], ['Jh', '2d'], res['detail']['hole'])
        self.assertEqual(res['board'], ['4h', '3s', 'Kc', '5c', 'Ad'])

    def test_hole_only_preflop(self):
        res = self.read(hole=['8h', '2h'], board=[])
        self.assertEqual(res['hole'], ['8h', '2h'], res['detail']['hole'])
        self.assertEqual(res['board'], [])

    def test_ten_rank(self):
        res = self.read(hole=['Th', 'Ts'], board=['Td', '2c', '3c'])
        self.assertEqual(res['hole'], ['Th', 'Ts'], res['detail']['hole'])
        self.assertEqual(res['board'][0], 'Td')

    def test_red_and_black_suits_distinguished(self):
        res = self.read(hole=['Ah', 'Ad'], board=['Ac', 'As', '9h'])
        self.assertEqual(res['hole'], ['Ah', 'Ad'], res['detail']['hole'])
        self.assertEqual(res['board'][:2], ['Ac', 'As'])

    def test_every_card_recognized_in_hole_position(self):
        """Все 52 карты, распознанные в позиции МОИХ карт (мелкий масштаб, веер)."""
        cards = _all_cards()
        bad = []
        for i in range(0, len(cards), 2):
            pair = cards[i:i + 2]
            res = self.read(hole=pair, board=[])
            if res['hole'] != pair:
                bad.append((pair, res['hole']))
        self.assertEqual(bad, [], f'не распознаны: {bad}')

    def test_no_cards_when_folded(self):
        res = self.read(hole=[], board=[])
        self.assertEqual(res['hole'], [None, None])
        self.assertEqual(res['board'], [])

    # ---------- 2 против 7 ----------
    def templates(self):
        return card_reader.load_templates(self.tpl)[0]

    def test_bottom_bar_separates_2_and_7(self):
        """У эталона двойки низ занят чертой, у семёрки — нет."""
        ranks = self.templates()
        b2 = card_reader.bottom_bar_ratio(ranks['2'])
        b7 = card_reader.bottom_bar_ratio(ranks['7'])
        self.assertGreaterEqual(b2 - b7, card_reader.BOTTOM_BAR_MIN_GAP,
                                f'признак не разделяет эталоны: 2={b2:.2f} 7={b7:.2f}')

    def test_slanted_seven_is_not_read_as_two(self):
        """Главный баг: наклонённая семёрка коррелировала с двойкой лучше, чем с семёркой.

        Живые кадры 18.08: 8 семёрок из 13 прочитались двойками (скор 2 = 0.49,
        7 = 0.28 — оба выше MIN_SCORE, побеждала двойка).
        """
        ranks = self.templates()
        glyph = synth.rank_glyph('7', slant=0.3)
        _, s2 = card_reader.match_best(glyph, ranks, allowed={'2'})
        _, s7 = card_reader.match_best(glyph, ranks, allowed={'7'})
        self.assertGreater(s2, s7, 'глиф должен воспроизводить путаницу корреляции')
        rank, score = card_reader.resolve_2_vs_7(glyph, s2, s7, ranks['2'], ranks['7'])
        self.assertEqual(rank, '7')
        # скор берётся лучший из двух: на живых кадрах корреляция семёрки падала
        # до 0.28, и с ним верно опознанная карта отсеклась бы порогом MIN_SCORE
        self.assertEqual(score, max(s2, s7))

    def test_two_stays_two(self):
        """Настоящие двойки признак не ломает — ни прямые, ни слегка наклонённые."""
        ranks = self.templates()
        for slant in (0.0, 0.1, 0.2):
            with self.subTest(slant=slant):
                glyph = synth.rank_glyph('2', slant=slant)
                _, s2 = card_reader.match_best(glyph, ranks, allowed={'2'})
                _, s7 = card_reader.match_best(glyph, ranks, allowed={'7'})
                rank, _ = card_reader.resolve_2_vs_7(glyph, s2, s7, ranks['2'], ranks['7'])
                self.assertEqual(rank, '2')
        for slant in (0.0, 0.1, 0.2, 0.3):
            with self.subTest(seven=slant):
                glyph = synth.rank_glyph('7', slant=slant)
                _, s2 = card_reader.match_best(glyph, ranks, allowed={'2'})
                _, s7 = card_reader.match_best(glyph, ranks, allowed={'7'})
                rank, _ = card_reader.resolve_2_vs_7(glyph, s2, s7, ranks['2'], ranks['7'])
                self.assertEqual(rank, '7')

    def test_resolve_without_templates_keeps_correlation(self):
        """Без эталонов пары (или когда они неразличимы) решает корреляция."""
        glyph = synth.rank_glyph('7', slant=0.3)
        self.assertEqual(card_reader.resolve_2_vs_7(glyph, 0.5, 0.4), ('2', 0.5))
        ranks = self.templates()
        # эталоны с одинаковым низом признаком не разводятся
        self.assertEqual(card_reader.resolve_2_vs_7(glyph, 0.5, 0.4, ranks['2'], ranks['2']),
                         ('2', 0.5))


if __name__ == '__main__':
    unittest.main(verbosity=2)
