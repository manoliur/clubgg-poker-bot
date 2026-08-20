#!/usr/bin/env python3
"""Живой стек: бот читает свою сумму с экрана и обновляет её в devices.json.

Проверяются три звена:
    table_state.read_own_stack — чтение зоны config.HERO_STACK_ZONE,
    Bot.update_stack           — раз в раздачу, с проверкой на здравость,
    Bot.save_stack             — точечная правка своей записи в devices.json.
Кадры телефона (shots_stack/) в git не лежат, поэтому тест на них пропускается
там, где папки нет; синтетика прогоняется всегда.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                           # noqa: E402
import strategy                         # noqa: E402
import table_state as ts                # noqa: E402
from main import Bot, FileScreen        # noqa: E402
from tests import synth                 # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(BASE, 'shots_stack')

# Живые кадры 400x888 (сессии 18-19.08.2026) -> свой стек, прочитанный глазами.
# Здесь есть все три интересных случая: сотни ББ, глубокий стек под 280 ББ и
# короткий (17-21 ББ), при котором включается push/fold.
LIVE_STACKS = {
    '20260818_125126_fold.jpg': 118.4, '20260818_125153_raise.jpg': 117.4,
    '20260818_125221_check.jpg': 117.4, '20260818_133555_fold.jpg': 107.4,
    '20260818_133639_raise.jpg': 105.9, '20260818_133739_fold.jpg': 104.4,
    '20260818_141956_fold.jpg': 149.5, '20260818_142012_raise.jpg': 148.5,
    '20260818_142036_check.jpg': 141.7, '20260818_142107_fold.jpg': 174.9,
    '20260818_145413_fold.jpg': 278.8, '20260818_145430_check.jpg': 277.8,
    '20260818_145509_call.jpg': 277.3, '20260818_153410_call.jpg': 247.8,
    '20260818_153421_raise.jpg': 247.3, '20260818_153436_raise.jpg': 247.3,
    '20260818_153535_fold.jpg': 247.3, '20260818_171447_raise.jpg': 246.6,
    '20260818_171513_check.jpg': 233.1, '20260818_171539_fold.jpg': 234.6,
    '20260818_171606_raise.jpg': 232.6, '20260818_171632_call.jpg': 229.6,
    '20260818_171704_raise.jpg': 243.8, '20260818_171802_fold.jpg': 246.8,
    '20260819_092333_fold.jpg': 19.5, '20260819_092349_check.jpg': 18.5,
    '20260819_092356_raise.jpg': 18.5, '20260819_092412_fold.jpg': 19.9,
    '20260819_092428_raise.jpg': 18.9, '20260819_092440_raise.jpg': 17.9,
    '20260819_092503_call.jpg': 21.4,
    '20260819_092510_call.jpg': 20.9,
}


class ReadOwnStackTest(unittest.TestCase):
    """Чтение своей суммы на синтетическом кадре."""

    def test_reads_the_hero_amount(self):
        for text, value in (('61.2', 61.2), ('9.5', 9.5), ('25', 25.0),
                            ('278.8', 278.8)):
            with self.subTest(text):
                img = synth.render(hole=['Ah', 'Kd'], hero_stack=text)
                self.assertEqual(ts.read_own_stack(img), value)

    def test_reads_a_downscaled_frame(self):
        """Кадр 400px (бот жмёт скриншоты) читается так же, как эталонный."""
        img = synth.render(hole=['Ah', 'Kd'], hero_stack='61.2')
        small = cv2.resize(img, (400, 888), interpolation=cv2.INTER_AREA)
        self.assertEqual(ts.read_own_stack(small), 61.2)

    def test_opponent_stacks_are_not_read(self):
        """В зоне только своя плашка: у оппонентов на кадре стек 259 ББ."""
        img = synth.render(hole=['Ah', 'Kd'], players=6, hero_stack='61.2')
        self.assertEqual(ts.read_own_stack(img), 61.2)

    def test_garbage_in_the_zone_is_not_a_number(self):
        img = synth.render(hole=['Ah', 'Kd'], hero_stack='61.2')
        H, W = img.shape[:2]
        x0, y0, x1, y1 = config.zone_px(config.HERO_STACK_ZONE, W, H)
        img[y0:y1, x0:x1] = synth.CYAN            # сплошная голубая заливка
        self.assertIsNone(ts.read_own_stack(img))

    def test_empty_zone_gives_none(self):
        img = synth.render(hole=['Ah', 'Kd'], hero_stack='61.2')
        H, W = img.shape[:2]
        x0, y0, x1, y1 = config.zone_px(config.HERO_STACK_ZONE, W, H)
        img[y0:y1, x0:x1] = synth.PANEL           # плашка без суммы
        self.assertIsNone(ts.read_own_stack(img))

    def test_without_digit_templates_there_is_no_number(self):
        img = synth.render(hole=['Ah', 'Kd'], hero_stack='61.2')
        empty = tempfile.mkdtemp(prefix='clubgg_notpl_')
        self.addCleanup(shutil.rmtree, empty, True)
        self.assertIsNone(ts.read_own_stack(img, tpl_dir=empty))


@unittest.skipUnless(os.path.isdir(SHOTS), 'нет папки shots_stack/ с живыми кадрами')
class LiveStackFramesTest(unittest.TestCase):
    """Те же 32 кадра телефона, по которым размечена зона стека."""

    def test_every_frame_is_read(self):
        digits = ts.load_digit_templates()
        for name, value in LIVE_STACKS.items():
            path = os.path.join(SHOTS, name)
            if not os.path.exists(path):
                continue
            with self.subTest(name):
                img = FileScreen(path).grab()
                self.assertEqual(ts.read_own_stack(img, digits), value)


class BotStackUpdateTest(unittest.TestCase):
    """Bot.update_stack: когда обновляем стек, когда остаёмся на прежнем."""

    SERIAL = 'test1234'

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_stack_')
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = os.path.join(self.tmp, 'devices.json')
        self.write({'serial': 'someone-else', 'stack': 42.0, 'style': 'loose'},
                   {'serial': self.SERIAL, 'name': 'Телефон 1', 'stack': 69.6,
                    'style': 'standard', 'chart': 'charts/6max_standard.json'})

    def write(self, *records):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(list(records), f, ensure_ascii=False)

    def read(self):
        with open(self.path, encoding='utf-8') as f:
            return json.load(f)

    def bot(self, **kw):
        kw.setdefault('stack_bb', 69.6)
        return Bot(StubScreen(), serial=self.SERIAL, devices_path=self.path,
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'), **kw)

    def frame(self, text):
        return synth.render(hole=['Ah', 'Kd'], hero_stack=text)

    def test_stack_from_the_screen_wins(self):
        bot = self.bot()
        self.assertEqual(bot.update_stack(self.frame('61.2')), 61.2)
        self.assertEqual(bot.stack_bb, 61.2)
        self.assertTrue(bot.stack_auto)

    def test_short_stack_mode_switches_on(self):
        """Ради этого всё и делалось: проиграли до 25 ББ — включается push/fold."""
        bot = self.bot()
        self.assertFalse(strategy.is_short(bot.stack_bb, bot.chart.settings))
        bot.update_stack(self.frame('25'))
        self.assertTrue(strategy.is_short(bot.stack_bb, bot.chart.settings))

    def test_devices_json_keeps_other_records_and_keys(self):
        bot = self.bot()
        bot.update_stack(self.frame('61.2'))
        other, mine = self.read()
        self.assertEqual(other, {'serial': 'someone-else', 'stack': 42.0,
                                 'style': 'loose'})
        self.assertEqual(mine['stack'], 61.2)
        self.assertIs(mine['stack_auto'], True)
        self.assertEqual(mine['name'], 'Телефон 1')
        self.assertEqual(mine['style'], 'standard')
        self.assertEqual(mine['chart'], 'charts/6max_standard.json')

    def test_unreadable_frame_keeps_the_previous_stack(self):
        bot = self.bot()
        blank = synth.render(hole=['Ah', 'Kd'])
        H, W = blank.shape[:2]
        x0, y0, x1, y1 = config.zone_px(config.HERO_STACK_ZONE, W, H)
        blank[y0:y1, x0:x1] = synth.PANEL
        self.assertIsNone(bot.update_stack(blank))
        self.assertEqual(bot.stack_bb, 69.6)
        self.assertEqual(self.read()[1]['stack'], 69.6)
        with open(os.path.join(self.tmp, 'bot.log'), encoding='utf-8') as f:
            self.assertIn('стек не прочитан', f.read())

    def test_nonsense_value_is_rejected(self):
        """Слипшиеся цифры («118.4» без точки — 1184) дальше 10x стека не проходят."""
        bot = self.bot()
        with mock.patch.object(ts, 'read_own_stack', return_value=1184.0):
            self.assertIsNone(bot.update_stack(self.frame('61.2')))
        self.assertEqual(bot.stack_bb, 69.6)

    def test_zero_is_rejected(self):
        bot = self.bot()
        with mock.patch.object(ts, 'read_own_stack', return_value=0.0):
            self.assertIsNone(bot.update_stack(self.frame('61.2')))
        self.assertEqual(bot.stack_bb, 69.6)

    def test_live_stack_off_keeps_the_constant(self):
        bot = self.bot(cfg={'stack': 69.6, 'live_stack': False})
        with mock.patch.object(ts, 'read_own_stack',
                               side_effect=AssertionError('лишнее чтение')):
            self.assertIsNone(bot.update_stack(self.frame('25')))
        self.assertEqual(bot.stack_bb, 69.6)
        self.assertEqual(self.read()[1]['stack'], 69.6)

    def test_flag_from_devices_json_switches_reading_off(self):
        bot = self.bot()
        self.write({'serial': self.SERIAL, 'stack': 69.6, 'live_stack': False})
        os.utime(self.path, (0, 1_700_000_042))
        bot.refresh_settings()
        self.assertFalse(bot.live_stack)

    def test_same_value_is_not_written_twice(self):
        bot = self.bot()
        bot.update_stack(self.frame('61.2'))
        mtime = os.path.getmtime(self.path)
        os.utime(self.path, (mtime - 5, mtime - 5))
        bot.update_stack(self.frame('61.2'))
        self.assertEqual(os.path.getmtime(self.path), mtime - 5)

    def test_broken_devices_file_does_not_break_the_hand(self):
        bot = self.bot()
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{это не json')
        self.assertEqual(bot.update_stack(self.frame('61.2')), 61.2)
        self.assertEqual(bot.stack_bb, 61.2)          # играем по прочитанному

    def test_missing_record_is_not_invented(self):
        self.write({'serial': 'someone-else', 'stack': 42.0})
        bot = self.bot()
        bot.update_stack(self.frame('61.2'))
        self.assertEqual(self.read(), [{'serial': 'someone-else', 'stack': 42.0}])
        self.assertEqual(bot.stack_bb, 61.2)

    def test_panel_value_overrides_the_read_one(self):
        """Человек вписал стек в панель — бот берёт его, пока не прочитает новый."""
        bot = self.bot()
        bot.update_stack(self.frame('61.2'))
        self.write({'serial': self.SERIAL, 'stack': 30.0, 'stack_auto': False})
        os.utime(self.path, (0, 1_700_000_043))
        bot.refresh_settings()
        self.assertEqual(bot.stack_bb, 30.0)
        self.assertFalse(bot.stack_auto)


class OncePerHandTest(unittest.TestCase):
    """Стек читается раз в раздачу, а не на каждом кадре: чтение глифов дорогое."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_hand_')
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def bot(self):
        return Bot(StubScreen(), dry_run=True,
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'))

    @staticmethod
    def state(hole, board=(), street='preflop'):
        return {'my_turn': True, 'in_hand': True, 'hole': list(hole),
                'board': list(board), 'street': street, 'has_bet': False,
                'to_call_bb': None, 'pot_bb': 3.0, 'position': 'BTN',
                'players': 2, 'players_seated': 2, 'dealer': 'me',
                'first_to_act': 'me', 'raise_presets': [], 'chevron': None,
                'presets_collapsed': False, 'taps': {'call': (535, 2315)},
                'cards_detail': {'hole': [{'card': c, 'rank_score': 1.0}
                                          for c in hole]}}

    def test_one_read_per_hand(self):
        bot = self.bot()
        with mock.patch.object(Bot, 'update_stack') as read:
            bot.step(None, self.state(['Ah', 'Kd']))
            bot.step(None, self.state(['Ah', 'Kd'], ['2c', '7d', '9s'], 'flop'))
            bot.step(None, self.state(['Qs', 'Jd']))
        self.assertEqual(read.call_count, 2)          # две раздачи — два чтения


class StubScreen:
    def grab(self):
        return None

    def tap(self, x, y):
        pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
