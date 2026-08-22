#!/usr/bin/env python3
"""Тесты состояния стола на синтетических кадрах + чистая логика позиций."""
import os
import sys
import shutil
import tempfile
import unittest
from unittest import mock

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import table_state as ts                # noqa: E402
import card_reader                      # noqa: E402
import config                           # noqa: E402
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

    def test_wide_bar_background_is_not_a_button(self):
        """Фон панели (широкий серый прямоугольник справа) — не кнопка.

        В ClubGG панель действий остаётся на экране, даже когда ход не наш
        (меню паузы, «сидеть за столом»): это широкий серый прямоугольник,
        покрывающий сразу оба центра (Колл и Бет). Настоящая кнопка уже и
        покрывает ровно один центр.
        """
        img = synth.render(buttons=False)
        H, W = img.shape[:2]
        img[int(H * config.ACTION_BAR_Y[0]):, int(W * 0.48):] = (55, 55, 55)
        self.assertEqual(ts.detect_action_buttons(img), [])
        self.assertFalse(ts.is_my_turn(img))

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


class TapPointTest(unittest.TestCase):
    """Тапы в долях экрана: одна и та же точка попадает в кнопку на любом телефоне.

    Второй телефон (Redmi 8) — 1080x2340 вместо 1080x2400, и жёсткая пиксельная
    точка (185, 2315) била там на 60px ниже кнопки (wm size на MIUI без root не
    поправить). Поэтому кнопки заданы долями, а в пиксели их переводит
    config.tap_point по размеру ФАКТИЧЕСКОГО кадра.
    """

    SHORT = (1080, 2340)      # Redmi 8

    def test_buttons_are_fractions(self):
        self.assertEqual(config.BTN_FOLD, (185 / 1080, 2315 / 2400))
        self.assertEqual(config.BTN_CALL, (535 / 1080, 2315 / 2400))
        self.assertEqual(config.BTN_RAISE, (880 / 1080, 2315 / 2400))
        self.assertEqual(config.CHEVRON, (617 / 1080, 2176 / 2400))
        for pt in (config.BTN_FOLD, config.BTN_CALL, config.BTN_RAISE, config.CHEVRON):
            self.assertTrue(all(0 < c < 1 for c in pt), pt)

    def test_reference_screen_keeps_measured_pixels(self):
        W, H = config.REF_W, config.REF_H
        self.assertEqual(config.tap_point(config.BTN_FOLD, W, H), (185, 2315))
        self.assertEqual(config.tap_point(config.BTN_CALL, W, H), (535, 2315))
        self.assertEqual(config.tap_point(config.BTN_RAISE, W, H), (880, 2315))
        self.assertEqual(config.tap_point(config.CHEVRON, W, H), (617, 2176))

    def test_short_screen_scales_y(self):
        W, H = self.SHORT
        self.assertEqual(config.tap_point(config.BTN_FOLD, W, H), (185, 2257))
        self.assertEqual(config.tap_point(config.BTN_CALL, W, H), (535, 2257))
        self.assertEqual(config.tap_point(config.BTN_RAISE, W, H), (880, 2257))

    def test_action_points_follow_frame_size(self):
        """Точки тапа берутся из кадра: на 2340 они выше, чем на 2400, и в кнопках."""
        for size in ((config.REF_W, config.REF_H), self.SHORT):
            with self.subTest(size=size):
                img = synth.render(hole=['Ah', 'Kd'], buttons=True, size=size)
                H, W = img.shape[:2]
                pts = ts.action_points(img)
                self.assertEqual(pts['fold'], config.tap_point(config.BTN_FOLD, W, H))
                for name in ('call', 'raise'):
                    x, y = pts[name]
                    ref_x, ref_y = config.tap_point(getattr(config, 'BTN_' + name.upper()), W, H)
                    self.assertLess(abs(x - ref_x), W * 0.06, name)
                    self.assertLess(abs(y - ref_y), H * 0.02, name)

    def test_chevron_point_follows_frame_size(self):
        img = synth.render(buttons=True, chevron=True, size=self.SHORT)
        H, W = img.shape[:2]
        self.assertEqual(ts.chevron_point(img), config.tap_point(config.CHEVRON, W, H))


class RaisePresetsTest(unittest.TestCase):
    """Правый столбец ставки: какие пресеты видны и какие из них живые."""

    def test_bet_button_alone_when_column_collapsed(self):
        presets = ts.raise_presets(synth.render(buttons=True))
        self.assertEqual([p['i'] for p in presets], [0], 'видна только кнопка «Бет»')
        self.assertTrue(presets[0]['enabled'])

    def test_expanded_column_lists_presets_bottom_up(self):
        presets = ts.raise_presets(synth.render(buttons=True, presets=3))
        self.assertEqual([p['i'] for p in presets], [0, 1, 2, 3])
        self.assertTrue(all(p['enabled'] for p in presets))
        ys = [p['y'] for p in presets]
        self.assertEqual(ys, sorted(ys, reverse=True), 'снизу вверх = сверху вниз по y')

    def test_dimmed_preset_is_not_enabled(self):
        """Пресет меньше минимальной ставки клиент гасит — тап по нему не проходит.

        Живой кадр 15:48:41: банк 2ББ, «33% Бет 0.6ББ» погашен, а эталонная точка
        рейза бьёт ровно в него — ход сгорел по таймауту.
        """
        presets = ts.raise_presets(synth.render(buttons=True, presets=3, dim_presets=(0,)))
        self.assertFalse(presets[0]['enabled'], 'нижний пресет погашен')
        self.assertTrue(all(p['enabled'] for p in presets[1:]))

    def test_no_presets_without_buttons(self):
        self.assertEqual(ts.raise_presets(synth.render(buttons=False)), [])


class ChevronTest(unittest.TestCase):
    """Шеврон «^» и свёрнутый столбец ставки (его надо раскрывать до ставки)."""

    def test_chevron_found(self):
        point = ts.chevron_point(synth.render(buttons=True, chevron=True))
        self.assertIsNotNone(point)
        W, H = config.REF_W, config.REF_H
        self.assertEqual(point, config.tap_point(config.CHEVRON, W, H))

    def test_no_chevron_no_point(self):
        self.assertIsNone(ts.chevron_point(synth.render(buttons=True)))
        self.assertIsNone(ts.chevron_point(synth.render(buttons=False)))

    def test_collapsed_column_detected(self):
        s = ts.read_state(synth.render(hole=['Ah', 'Kd'], buttons=True, chevron=True))
        self.assertTrue(s['presets_collapsed'], 'одна кнопка вместо четырёх + шеврон')
        self.assertEqual(len(s['raise_presets']), 1)

    def test_expanded_column_is_not_collapsed(self):
        s = ts.read_state(synth.render(hole=['Ah', 'Kd'], buttons=True, presets=3,
                                       chevron=True))
        self.assertFalse(s['presets_collapsed'], 'все четыре строки видны — раскрывать нечего')

    def test_no_column_at_all_is_not_collapsed(self):
        """Кнопки ставки нет вовсе (олл-ин оппонента) — раскрывать нечего."""
        s = ts.read_state(synth.render(hole=['Ah', 'Kd'], buttons=False, chevron=True))
        self.assertFalse(s['presets_collapsed'])

    def test_showdown_has_no_chevron(self):
        s = ts.read_state(synth.render(hole=['Ah', 'Kd'], showdown=True, chevron=True))
        self.assertIsNone(s['chevron'])
        self.assertFalse(s['presets_collapsed'])


class LightStateTest(unittest.TestCase):
    """Лёгкое чтение: столбец ставки сканируется только под рейз (см. fill_presets)."""

    def frame(self):
        return synth.render(hole=['Ah', 'Kd'], buttons=True, presets=3, chevron=True)

    def test_light_state_skips_preset_scan(self):
        img = self.frame()
        with mock.patch.object(ts, 'raise_presets') as presets, \
             mock.patch.object(ts, 'chevron_point') as chevron:
            s = ts.read_state(img, light=True)
        presets.assert_not_called()
        chevron.assert_not_called()
        self.assertEqual(s['raise_presets'], [])
        self.assertIsNone(s['chevron'])
        self.assertFalse(s['presets_collapsed'])
        self.assertTrue(s['light'], 'состояние помечено как недочитанное')

    def test_light_state_keeps_everything_decision_needs(self):
        """Всё, по чему принимается решение, лёгкое чтение отдаёт как полное."""
        img = self.frame()
        light = ts.read_state(img, light=True)
        full = ts.read_state(img)
        for key in ('my_turn', 'in_hand', 'hole', 'board', 'street', 'has_bet',
                    'pot_bb', 'to_call_bb', 'players', 'players_seated', 'position',
                    'dealer', 'first_to_act', 'taps', 'showdown'):
            self.assertEqual(light[key], full[key], key)

    def test_fill_presets_reads_column_from_the_same_frame(self):
        img = self.frame()
        s = ts.read_state(img, light=True)
        self.assertIs(ts.fill_presets(s, img), s, 'состояние правится на месте')
        self.assertFalse(s['light'])
        self.assertEqual(s['raise_presets'], ts.raise_presets(img))
        self.assertEqual(s['chevron'], ts.chevron_point(img))

    def test_fill_presets_does_nothing_on_a_full_state(self):
        img = self.frame()
        s = ts.read_state(img)
        with mock.patch.object(ts, 'raise_presets') as presets:
            ts.fill_presets(s, img)
        presets.assert_not_called()

    def test_showdown_stays_without_presets(self):
        """На вскрытии столбца нет вовсе — дочитывать нечего (и тапать нельзя)."""
        img = synth.render(hole=['Ah', 'Kd'], showdown=True, chevron=True)
        s = ts.read_state(img, light=True)
        self.assertTrue(s['showdown'])
        self.assertFalse(s['light'])
        with mock.patch.object(ts, 'raise_presets') as presets:
            ts.fill_presets(s, img)
        presets.assert_not_called()
        self.assertEqual(s['raise_presets'], [])


class ShowdownTest(unittest.TestCase):
    """Вскрытие: плашки «Показать» стоят на местах кнопок, но ходить там нельзя."""

    def test_showdown_is_not_my_turn(self):
        img = synth.render(hole=['Ah', 'Kd'], board=['2c', '7d', '9s'], showdown=True)
        self.assertTrue(ts.is_showdown(img))
        self.assertGreaterEqual(len(ts.detect_action_buttons(img)), 1,
                                'плашки «Показать» неотличимы от кнопок по форме')
        self.assertFalse(ts.is_my_turn(img), 'но ходом это не считается')

    def test_normal_turn_is_not_showdown(self):
        self.assertFalse(ts.is_showdown(synth.render(buttons=True, call_amount=True)))
        self.assertFalse(ts.is_showdown(synth.render(buttons=False)))

    def test_state_hides_buttons_on_showdown(self):
        state = ts.read_state(synth.render(hole=['Ah', 'Kd'], showdown=True))
        self.assertTrue(state['showdown'])
        self.assertFalse(state['my_turn'])
        self.assertEqual(state['buttons'], [])
        self.assertEqual(state['raise_presets'], [])


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
        for n in (2, 3, 4, 5, 6):
            count, occupied, panels = ts.count_players(synth.render(players=n))
            self.assertEqual(count, n, f'мест занято {occupied}, плашек {len(panels)}')

    def test_sitting_out_players_not_counted(self):
        """Занятое место без карт («Вне игры», 0 ББ) в раздаче не участвует."""
        img = synth.render(players=2, sitting_out=2)
        count, _, panels = ts.count_players(img)
        self.assertEqual(count, 2)
        self.assertEqual(len(panels), 4, 'плашки видны все, но в раздаче только две')

    def test_seats_ordered_clockwise_from_hero(self):
        panels = ts.player_panels_ordered(synth.render(players=6))
        self.assertEqual(len(panels), 6)
        self.assertTrue(panels[0]['is_hero'], 'герой открывает круг')
        # круг идёт по часовой стрелке: сначала места слева от героя, потом верх, потом справа
        self.assertLess(panels[1]['y'], panels[0]['y'])
        self.assertEqual(min(range(6), key=lambda i: panels[i]['y']), 3, 'верх стола — третий')
        self.assertGreater(panels[5]['x'], panels[1]['x'], 'последнее место — справа')

    def test_dealer_seat_matches_panel(self):
        for seat in range(5):
            d = ts.find_dealer(synth.render(players=6, dealer=seat))
            self.assertIsNotNone(d, seat)
            self.assertEqual(d['where'], 'opp', seat)
            self.assertEqual(d['seat'], seat)


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

    def test_positions_for_three_to_six_players(self):
        """Позиция героя по кнопке D для любого числа игроков (2..6).

        Место N = N-е по часовой стрелке от героя. Дилер на месте 0 (сразу за
        героем) -> герой в хвосте (CO/BB), дилер на последнем месте -> герой SB.
        """
        expected = {
            (3, 'me'): 'BTN', (3, 0): 'BB', (3, 1): 'SB',
            (4, 'me'): 'BTN', (4, 0): 'CO', (4, 1): 'BB', (4, 2): 'SB',
            (5, 'me'): 'BTN', (5, 0): 'CO', (5, 1): 'UTG', (5, 2): 'BB', (5, 3): 'SB',
            (6, 'me'): 'BTN', (6, 0): 'CO', (6, 1): 'MP', (6, 2): 'UTG',
            (6, 3): 'BB', (6, 4): 'SB',
        }
        for (players, dealer), pos in expected.items():
            s = self.state(hole=['Ah', 'Kd'], players=players, dealer=dealer)
            self.assertEqual(s['players'], players, (players, dealer))
            self.assertEqual(s['position'], pos, (players, dealer))

    def test_first_to_act_for_three_to_six_players(self):
        """Префлоп говорит первым UTG (третий после D), постфлоп — SB (первый после D).

        Исключение — 3 игрока: за тремя (BTN/SB/BB) префлоп первым ходит сам BTN.
        """
        for players in (3, 4, 5, 6):
            # герой на баттоне: первым и до, и после флопа говорит не он
            # (при 3 игроках префлоп первым ходит сам баттон = герой)
            s = self.state(hole=['Ah', 'Kd'], players=players, dealer='me')
            self.assertEqual(s['first_to_act'], 'me' if players == 3 else 'opp', players)
            # дилер на последнем месте круга -> герой SB -> постфлоп первым ходит он
            s = self.state(hole=['Ah', 'Kd'], players=players, dealer=players - 2,
                           board=['Ad', 'Kc', '2h'])
            self.assertEqual(s['position'], 'SB', players)
            self.assertEqual(s['first_to_act'], 'me', players)

    def test_sitting_out_player_does_not_shift_position(self):
        """Игрок вне раздачи не сдвигает позиции: круг считается по числу СИДЯЩИХ.

        За столом 5 мест (2 вне раздачи), дилер на месте 1 -> герой на третьем
        месте от баттона = UTG в 5-max. Раньше позиция считалась по числу в
        раздаче (3 -> SB) и бот включал HU-тактику на столе с 3-5 игроками.
        """
        s = self.state(hole=['Ah', 'Kd'], players=3, dealer=1, sitting_out=2)
        self.assertEqual(s['players'], 3)
        self.assertEqual(s['players_seated'], 5)
        # 5 сидящих, дилер на месте 1: BTN=опп1, SB=опп2, BB=опп3, UTG=герой
        self.assertEqual(s['position'], 'UTG')

    def test_full_board_read_at_six_max(self):
        """Панели игроков по краям заходят на зону доски — карты всё равно читаются."""
        for board in (['6d', '5h', 'As'], ['6d', '5h', 'As', '9c', 'Qc']):
            s = self.state(hole=['Ah', 'Kd'], board=board, players=6, dealer='opp')
            self.assertEqual(s['board'], board)
            self.assertEqual(s['street'], {3: 'flop', 5: 'river'}[len(board)])

    def test_board_cards_do_not_add_players(self):
        """Карты доски заходят на крайние места — считать их игроками нельзя."""
        for board in ([], ['6d', '5h', 'As'], ['6d', '5h', 'As', '9c', 'Qc']):
            s = self.state(hole=['Ah', 'Kd'], board=board, players=6, dealer='opp')
            self.assertEqual(s['players'], 6, board)

    def test_hero_has_cards(self):
        self.assertTrue(ts.hero_has_cards(synth.render(hole=['Ah', 'Kd'])))
        self.assertFalse(ts.hero_has_cards(synth.render(hole=[])))


class NumberReadingTest(unittest.TestCase):
    """Чтение чисел: эталоны цифр собраны с живых кадров (см. build_templates)."""

    def test_templates_cover_all_digits(self):
        digits = ts.load_digit_templates()
        for ch in '0123456789':
            self.assertIn(ch, digits, f'нет эталона цифры {ch}')
        self.assertIn('dot', digits, 'нет эталона точки — 1.5 читалось бы как 15')
        self.assertIn('bb', digits, 'нет эталона «Б» — подпись ББ липнет к числу')

    def test_read_call_amount(self):
        img = synth.render(hole=['Ah', 'Kd'], call_amount=True)
        self.assertEqual(ts.read_number(img, config.call_amount_rect()), 2.5)

    def test_read_pot(self):
        for pot in (2.0, 4.5, 12.5, 0.5):
            img = synth.render(hole=['Ah', 'Kd'], pot_bb=pot)
            self.assertEqual(ts.read_number(img, config.POT_ZONE), pot)

    def test_state_carries_numbers(self):
        s = ts.read_state(synth.render(hole=['Ah', 'Kd'], pot_bb=6.5, call_amount=True))
        self.assertEqual(s['pot_bb'], 6.5)
        self.assertEqual(s['to_call_bb'], 2.5)

    def test_no_bet_no_call_amount(self):
        s = ts.read_state(synth.render(hole=['Ah', 'Kd'], pot_bb=3.0, call_amount=False))
        self.assertFalse(s['has_bet'])
        self.assertIsNone(s['to_call_bb'])

    def test_read_on_downscaled_frame(self):
        """Кадр вдвое меньше эталонного: точка мельче порога площади, но читается.

        Так сняты кадры shots_digits/ (540px), на них же собраны эталоны.
        """
        img = synth.render(hole=['Ah', 'Kd'], pot_bb=1.5, call_amount=True,
                           size=(config.REF_W // 2, config.REF_H // 2))
        self.assertEqual(ts.read_number(img, config.POT_ZONE), 1.5)

    def test_bb_suffix_not_read_as_digits(self):
        """«4 ББ» — это 4, а не 466: у буквы Б свой эталон, дальше него не читаем."""
        img = synth.render(hole=['Ah', 'Kd'], pot_bb=4.0)
        self.assertEqual(ts.read_number(img, config.POT_ZONE), 4.0)

    def test_collect_digits_from_marked_rect(self):
        """Разметка {'rect','text'} собирает эталоны цифр из кадра."""
        tmp = tempfile.mkdtemp(prefix='clubgg_dig_')
        try:
            tpl = os.path.join(tmp, 'templates')
            path = os.path.join(tmp, 'frame.png')
            synth.save(path, hole=['Ah', 'Kd'], call_amount=True)
            rect = list(config.call_amount_rect())
            _, _, skipped = collect([{'file': path, 'rect': rect, 'text': '2.5ББ',
                                      'ink': 'amber'}], base=tmp, tpl_dir=tpl,
                                    verbose=False)
            self.assertEqual(skipped, [])
            self.assertEqual(sorted(ts.load_digit_templates(tpl)), ['2', '5', 'bb', 'dot'])
            img = cv2.imread(path)
            self.assertEqual(ts.read_number(img, rect, 'amber', tpl_dir=tpl), 2.5)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_no_templates_no_number(self):
        img = synth.render(call_amount=True)
        empty = tempfile.mkdtemp(prefix='clubgg_empty_')
        try:
            self.assertIsNone(ts.read_number(img, config.call_amount_rect(), tpl_dir=empty))
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
