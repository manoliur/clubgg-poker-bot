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
        self.assertEqual(n_rank, 12, 'все ранги кроме T (T узнаётся по двум глифам)')

    def test_board_detected(self):
        boxes = card_reader.find_board_cards(
            synth.render(board=['Ah', 'Kd', 'Qs', 'Jc', 'Tc']))
        self.assertEqual(len(boxes), 5)

    def test_hole_boxes_split_from_fan(self):
        """Слипшийся контур двух карт делится на две правдоподобные карты."""
        boxes = card_reader.my_card_boxes(synth.render(hole=['Ah', 'Kd']))
        self.assertEqual(len(boxes), 2)
        (x0, y0, x1, y1), (u0, v0, u1, v1) = boxes
        self.assertLess(x0, u0)
        for b in boxes:
            w, h = b[2] - b[0], b[3] - b[1]
            self.assertGreater(h / w, 1.2)
            self.assertLess(h / w, 1.8)

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


if __name__ == '__main__':
    unittest.main(verbosity=2)
