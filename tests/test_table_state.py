#!/usr/bin/env python3
"""Тесты состояния стола на синтетических кадрах + чистая логика позиций."""
import os
import sys
import shutil
import tempfile
import unittest

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import table_state as ts                # noqa: E402
import card_reader                      # noqa: E402
from build_templates import collect     # noqa: E402
from tests import synth                 # noqa: E402


class ButtonsTest(unittest.TestCase):
    def test_buttons_detected_when_my_turn(self):
        img = synth.render(hole=['Ah', 'Kd'], buttons=True)
        btns = ts.detect_action_buttons(img)
        self.assertGreaterEqual(len(btns), 2, 'фолд/колл/рейз в правой части полосы')
        self.assertTrue(ts.is_my_turn(img))

    def test_no_buttons_when_not_my_turn(self):
        img = synth.render(hole=['Ah', 'Kd'], buttons=False)
        self.assertFalse(ts.is_my_turn(img))
        self.assertEqual(ts.detect_action_buttons(img), [])

    def test_autoaction_checkbox_is_not_a_button(self):
        """Автодействие «Чек/Фолд» слева (x 45-520) не должно считаться кнопкой."""
        img = synth.render(hole=['Ah', 'Kd'], buttons=False)
        for b in ts.detect_action_buttons(img):
            self.assertGreaterEqual(b['x0'], int(config_x0(img)), b)

    def test_bet_detected_by_yellow_amount(self):
        self.assertTrue(ts.has_bet(synth.render(call_amount=True)))
        self.assertFalse(ts.has_bet(synth.render(call_amount=False)))

    def test_tap_points_inside_buttons(self):
        img = synth.render(buttons=True)
        pts = ts.action_points(img)
        self.assertEqual(set(pts), {'fold', 'call', 'raise'})
        H, W = img.shape[:2]
        for name, (x, y) in pts.items():
            self.assertTrue(0 < x < W and H * 0.86 < y < H, (name, x, y))


def config_x0(img):
    import config
    return config.ACTION_BAR_X0 * img.shape[1] / config.REF_W


class DealerAndPlayersTest(unittest.TestCase):
    def test_dealer_at_hero(self):
        d = ts.find_dealer(synth.render(dealer='me'))
        self.assertIsNotNone(d)
        self.assertEqual(d['where'], 'me')

    def test_dealer_at_opponent(self):
        d = ts.find_dealer(synth.render(dealer='opp'))
        self.assertIsNotNone(d)
        self.assertEqual(d['where'], 'opp')

    def test_no_dealer_marker(self):
        self.assertIsNone(ts.find_dealer(synth.render(dealer=None)))

    def test_player_count(self):
        for n in (2, 3, 5, 6, 9):
            count, occupied, scores = ts.count_players(synth.render(players=n))
            self.assertEqual(count, n, f'мест занято {occupied}, оценки {scores}')


class PositionLogicTest(unittest.TestCase):
    def test_heads_up_positions(self):
        self.assertEqual(ts.hero_position('me', 2), 'SB')
        self.assertEqual(ts.hero_position('opp', 2), 'BB')

    def test_heads_up_order(self):
        # префлоп первым ходит SB (кнопка D), постфлоп — BB
        self.assertEqual(ts.first_to_act('preflop', 2, True), 'me')
        self.assertEqual(ts.first_to_act('preflop', 2, False), 'opp')
        self.assertEqual(ts.first_to_act('flop', 2, True), 'opp')
        self.assertEqual(ts.first_to_act('flop', 2, False), 'me')

    def test_position_names(self):
        self.assertEqual(ts.position_names(2), ['SB', 'BB'])
        self.assertEqual(ts.position_names(3), ['BTN', 'SB', 'BB'])
        self.assertEqual(ts.position_names(6), ['BTN', 'SB', 'BB', 'UTG', 'MP', 'CO'])

    def test_hero_position_six_max(self):
        occupied = [0, 1, 2, 3, 4]          # 5 оппонентов + герой
        self.assertEqual(ts.hero_position('me', 6, None, occupied), 'BTN')
        # дилер на первом месте после героя -> герой на месте перед баттоном = CO
        self.assertEqual(ts.hero_position('opp', 6, 0, occupied), 'CO')
        # дилер на последнем месте круга -> герой сразу после него = SB
        self.assertEqual(ts.hero_position('opp', 6, 4, occupied), 'SB')
        self.assertEqual(ts.hero_position('opp', 6, 3, occupied), 'BB')

    def test_first_to_act_six_max(self):
        occupied = [0, 1, 2, 3, 4]
        # герой на баттоне: префлоп первым UTG (третий после D), постфлоп SB
        self.assertEqual(ts.first_to_act('preflop', 6, True, None, occupied), 'opp')
        self.assertEqual(ts.first_to_act('flop', 6, True, None, occupied), 'opp')
        # дилер на месте 4 -> герой SB -> постфлоп ходит первым
        self.assertEqual(ts.first_to_act('flop', 6, False, 4, occupied), 'me')
        # дилер на месте 2 -> третий после него — герой -> префлоп ходит первым
        self.assertEqual(ts.first_to_act('preflop', 6, False, 2, occupied), 'me')


class ReadStateTest(unittest.TestCase):
    tmp = tpl = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='clubgg_state_')
        cls.tpl = os.path.join(cls.tmp, 'templates')
        cards = [f'{r}{s}' for r in card_reader.RANK_ORDER for s in card_reader.SUITS]
        labels = []
        for i in range(0, len(cards), 5):
            chunk = cards[i:i + 5]
            p = os.path.join(cls.tmp, f'train{i}.png')
            synth.save(p, board=chunk, hole=[])
            labels.append({'file': p, 'zone': 'board', 'cards': chunk})
        collect(labels, base=cls.tmp, tpl_dir=cls.tpl, verbose=False)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def state(self, **kw):
        return ts.read_state(synth.render(**kw), tpl_dir=self.tpl)

    def test_preflop_heads_up_my_turn(self):
        s = self.state(hole=['Ah', 'Kd'], board=[], dealer='me', players=2,
                       buttons=True, call_amount=True)
        self.assertTrue(s['my_turn'])
        self.assertTrue(s['has_bet'])
        self.assertEqual(s['hole'], ['Ah', 'Kd'])
        self.assertEqual(s['street'], 'preflop')
        self.assertEqual(s['players'], 2)
        self.assertEqual(s['position'], 'SB')
        self.assertTrue(s['hero_is_dealer'])
        self.assertEqual(s['first_to_act'], 'me')

    def test_flop_out_of_position(self):
        s = self.state(hole=['7h', '6s'], board=['Ad', 'Kc', '2h'], dealer='opp',
                       players=2, buttons=True, call_amount=False)
        self.assertEqual(s['street'], 'flop')
        self.assertEqual(s['board'], ['Ad', 'Kc', '2h'])
        self.assertEqual(s['position'], 'BB')
        self.assertFalse(s['has_bet'])
        self.assertEqual(s['first_to_act'], 'me')

    def test_turn_and_river_streets(self):
        s = self.state(board=['Ad', 'Kc', '2h', '9s'])
        self.assertEqual(s['street'], 'turn')
        s = self.state(board=['Ad', 'Kc', '2h', '9s', '4d'])
        self.assertEqual(s['street'], 'river')

    def test_not_my_turn(self):
        s = self.state(hole=['Ah', 'Kd'], buttons=False)
        self.assertFalse(s['my_turn'])

    def test_six_max_table(self):
        s = self.state(hole=['Ah', 'Kd'], players=6, dealer='me')
        self.assertEqual(s['players'], 6)
        self.assertEqual(s['position'], 'BTN')


class NumberReadingTest(unittest.TestCase):
    """Чтение чисел работает после сбора эталонов цифр (жёлтый текст)."""

    def test_read_call_amount(self):
        tmp = tempfile.mkdtemp(prefix='clubgg_dig_')
        try:
            tpl = os.path.join(tmp, 'templates')
            path = os.path.join(tmp, 'frame.png')
            synth.save(path, call_amount=True)
            rect = [380 / 1080, 0.86, 700 / 1080, 0.99]
            collect([{'file': path, 'rect': rect, 'text': '2.5', 'ink': 'yellow'}],
                    base=tmp, tpl_dir=tpl, verbose=False)
            img = cv2.imread(path)
            self.assertEqual(ts.read_number(img, rect, 'yellow', tpl_dir=tpl), 2.5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_templates_no_number(self):
        img = synth.render(call_amount=True)
        empty = tempfile.mkdtemp(prefix='clubgg_empty_')
        try:
            self.assertIsNone(ts.read_number(img, (0.3, 0.86, 0.7, 0.99), tpl_dir=empty))
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
