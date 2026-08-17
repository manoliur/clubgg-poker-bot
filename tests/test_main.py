#!/usr/bin/env python3
"""Тесты главного цикла: сквозной проход кадр -> решение -> тап -> лог."""
import io
import os
import sys
import json
import shutil
import tempfile
import unittest
from contextlib import contextmanager, redirect_stdout
from unittest import mock

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import card_reader                      # noqa: E402
import config                           # noqa: E402
import main as main_mod                 # noqa: E402
from build_templates import collect     # noqa: E402
from main import Bot                    # noqa: E402
from tests import synth                 # noqa: E402


class FakeScreen:
    """Экран из подготовленных кадров: grab() отдаёт их по очереди, tap() пишет в список."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.taps = []

    def grab(self):
        return self.frames.pop(0) if self.frames else None

    def tap(self, x, y):
        self.taps.append((int(x), int(y)))


class MainLoopTest(unittest.TestCase):
    tmp = tpl = None

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='clubgg_main_')
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

    @contextmanager
    def tmp_paths(self):
        """CLI создаёт Bot сам — уводим его лог и историю во временную папку."""
        with mock.patch.object(config, 'LOG_FILE', os.path.join(self.tmp, 'cli.log')), \
             mock.patch.object(config, 'HAND_HISTORY',
                               os.path.join(self.tmp, 'cli_history.jsonl')):
            yield

    def make_bot(self, frames, dry_run=False):
        screen = FakeScreen(frames)
        bot = Bot(screen, dry_run=dry_run, tpl_dir=self.tpl,
                  log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        return bot, screen

    def test_no_action_when_not_my_turn(self):
        bot, screen = self.make_bot([synth.render(hole=['Ah', 'Kd'], buttons=False)])
        self.assertIsNone(bot.step())
        self.assertEqual(screen.taps, [])

    def test_check_when_no_bet(self):
        frame = synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], buttons=True,
                             call_amount=False, dealer='opp', players=2)
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertIsNotNone(entry)
        self.assertIn(entry['action'], ('check', 'raise'))
        self.assertEqual(len(screen.taps), 1)

    def test_fold_trash_facing_bet(self):
        frame = synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], buttons=True,
                             call_amount=True, dealer='opp', players=2)
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertEqual(entry['action'], 'fold')
        self.assertEqual(entry['hole'], ['7h', '2c'])
        self.assertEqual(entry['board'], ['Ad', 'Ks', '9c'])
        self.assertEqual(len(screen.taps), 1)
        # тап именно по кнопке фолда (левая часть полосы), а не по коллу
        x, y = screen.taps[0]
        self.assertLess(x, 400)
        self.assertGreater(y, frame.shape[0] * 0.86)

    def test_raise_with_set(self):
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, dealer='me', players=2)
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertEqual(entry['action'], 'raise')
        self.assertGreater(screen.taps[0][0], 600)

    def test_dry_run_does_not_tap(self):
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, players=2)
        bot, screen = self.make_bot([frame], dry_run=True)
        entry = bot.step()
        self.assertEqual(entry['action'], 'raise')
        self.assertEqual(screen.taps, [])

    def test_history_written(self):
        frame = synth.render(hole=['Ah', 'Kd'], board=[], buttons=True, call_amount=True,
                             dealer='me', players=2)
        bot, _ = self.make_bot([frame])
        bot.step()
        with open(bot.history_path, encoding='utf-8') as f:
            lines = [json.loads(x) for x in f if x.strip()]
        self.assertTrue(lines)
        last = lines[-1]
        for key in ('ts', 'hand_id', 'street', 'hole', 'board', 'position', 'action',
                    'reason', 'tap'):
            self.assertIn(key, last)
        self.assertEqual(last['street'], 'preflop')

    def test_hand_id_increments_on_new_hole_cards(self):
        frames = [
            synth.render(hole=['Ah', 'Kd'], buttons=True, call_amount=True, players=2),
            synth.render(hole=['Ah', 'Kd'], board=['2c', '7d', '9s'], buttons=True,
                         call_amount=True, players=2),
            synth.render(hole=['Qs', 'Jd'], buttons=True, call_amount=True, players=2),
        ]
        bot, _ = self.make_bot(frames)
        ids = [bot.step()['hand_id'] for _ in range(3)]
        self.assertEqual(ids, [1, 1, 2], 'новая раздача = новые карманные карты')

    def test_raise_without_bet_button_becomes_call(self):
        """Кнопки бета не видно -> вместо рейза колл: тапать в пустоту нельзя."""
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, dealer='me', players=2)
        H, W = frame.shape[:2]
        rx, _ = config.scale(config.BTN_RAISE, W, H)     # закрасить кнопку рейза сукном
        frame[int(H * 0.86):, rx - int(W * 0.16):] = synth.FELT
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertEqual(entry['action'], 'call')
        self.assertIsNone(entry['amount_bb'], 'размер несостоявшегося рейза не логируем')
        self.assertIn('raise', entry['reason'])
        self.assertEqual(len(screen.taps), 1)
        self.assertLess(screen.taps[0][0], rx - int(W * 0.16))

    def test_cli_image_mode_does_not_tap(self):
        path = os.path.join(self.tmp, 'cli.png')
        cv2.imwrite(path, synth.render(hole=['Ah', 'Kd'], buttons=True, call_amount=True,
                                       players=2))
        buf = io.StringIO()
        with self.tmp_paths(), redirect_stdout(buf):
            code = main_mod.main(['--image', path])
        self.assertEqual(code, 0)
        self.assertIn('"action"', buf.getvalue())

    def test_cli_image_mode_not_my_turn(self):
        path = os.path.join(self.tmp, 'cli_idle.png')
        cv2.imwrite(path, synth.render(hole=['Ah', 'Kd'], buttons=False, players=2))
        buf = io.StringIO()
        with self.tmp_paths(), redirect_stdout(buf):
            self.assertEqual(main_mod.main(['--image', path]), 0)
        self.assertIn('не мой ход', buf.getvalue())

    def test_unreadable_frame_is_safe(self):
        bot, screen = self.make_bot([])
        self.assertIsNone(bot.step())
        self.assertEqual(screen.taps, [])

    def test_no_templates_falls_back_to_safe_action(self):
        """Без эталонов карты не читаются: при ставке — фолд, без ставки — чек."""
        empty = tempfile.mkdtemp(prefix='clubgg_notpl_')
        try:
            frame = synth.render(hole=['Ah', 'Kd'], buttons=True, call_amount=True,
                                 players=2)
            screen = FakeScreen([frame])
            bot = Bot(screen, tpl_dir=empty,
                      log_path=os.path.join(self.tmp, 'bot.log'),
                      history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
            entry = bot.step()
            self.assertEqual(entry['action'], 'fold')
            self.assertEqual(entry['hole'], [None, None])
        finally:
            shutil.rmtree(empty, ignore_errors=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
