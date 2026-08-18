#!/usr/bin/env python3
"""Тесты главного цикла: сквозной проход кадр -> решение -> тап -> лог."""
import io
import os
import random
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

    def test_no_tap_when_not_in_hand(self):
        """Кнопки внизу есть, но карманных карт нет — мы не в раздаче, тапать нельзя."""
        frame = synth.render(hole=[], board=['Ad', 'Ks', '9c'], buttons=True,
                             call_amount=True, players=2)
        bot, screen = self.make_bot([frame])
        self.assertIsNone(bot.step())
        self.assertEqual(screen.taps, [])
        self.assertFalse(bot.last_state['in_hand'])
        self.assertTrue(bot.last_state['my_turn'])

    def test_stats_and_summary(self):
        frames = [
            synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], buttons=True,
                         call_amount=True, dealer='opp', players=2),
            synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                         call_amount=True, dealer='me', players=2),
        ]
        bot, _ = self.make_bot(frames)
        bot.step()
        bot.step()
        self.assertEqual(bot.actions, 2)
        self.assertEqual(bot.stats['fold'], 1)
        self.assertEqual(bot.stats['raise'], 1)
        bot.summary()
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('ИТОГИ: раздач=2 решений=2', f.read())

    def test_run_stops_at_max_actions(self):
        frame = synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], buttons=True,
                             call_amount=True, dealer='opp', players=2)
        screen = FakeScreen([frame] * 12)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            # retry_after=0: состояние не меняется -> считаем тап не прошедшим и повторяем
            bot.run(interval=0, settle=0, max_actions=2, retry_after=0)
        self.assertEqual(bot.actions, 2)
        self.assertEqual(len(screen.taps), 2)

    def test_no_double_action_on_same_state(self):
        """После хода на том же состоянии (панель не исчезла) повторно не тапаем."""
        frame = synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], buttons=True,
                             call_amount=True, dealer='opp', players=2)
        screen = FakeScreen([frame] * 10)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, max_actions=5, retry_after=1000)
        self.assertEqual(bot.actions, 1, 'одинаковые кадры = один ход, без повторов')
        self.assertEqual(len(screen.taps), 1)

    def test_acts_again_when_state_changes(self):
        """Оппонент сыграл (доска сменилась) -> бот действует на новом состоянии."""
        f1 = synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c'], buttons=True,
                          call_amount=True, dealer='opp', players=2)
        f2 = synth.render(hole=['7h', '2c'], board=['Ad', 'Ks', '9c', '2h'], buttons=True,
                          call_amount=True, dealer='opp', players=2)
        screen = FakeScreen([f1] * 4 + [f2] * 4)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, max_actions=5, retry_after=1000)
        self.assertEqual(bot.actions, 2, 'флоп -> тёрн = два решения')
        self.assertEqual(len(screen.taps), 2)

    def test_raise_not_retried_on_same_state(self):
        """Рейз не повторяем: второй тап по «Бет» = двойная ставка."""
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, dealer='me', players=2)   # сет -> рейз
        screen = FakeScreen([frame] * 8)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, max_actions=5, retry_after=0)
        self.assertEqual(bot.actions, 1, 'рейз повторно не тапаем')
        self.assertEqual(len(screen.taps), 1)

    def test_check_retried_when_tap_missed(self):
        """Чек при непрошедшем тапе повторяем (повторный чек безвреден)."""
        # комбо из живой игры: Kh2h на ривере -> «смотрим карту бесплатно» (чек)
        frame = synth.render(hole=['Kh', '2h'], board=['6s', '4d', '9d', '4h', '3c'],
                             buttons=True, call_amount=False, dealer='opp', players=2)
        screen = FakeScreen([frame] * 8)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, max_actions=2, retry_after=0)
        self.assertEqual(bot.actions, 2, 'чек повторяем, пока состояние не сменилось')
        self.assertEqual(len(screen.taps), 2)

    def test_run_gives_up_when_no_frames(self):
        """Телефон отключён (grab() -> None): цикл не висит вечно, а выходит."""
        bot, screen = self.make_bot([])
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, fail_limit=3)
        self.assertEqual(screen.taps, [])
        self.assertEqual(bot.actions, 0)

    def test_run_skips_frames_without_hole_cards(self):
        """Кнопки без карт не должны тратить лимит решений и вызывать тапы."""
        frames = [synth.render(hole=[], board=['Ad', 'Ks', '9c'], buttons=True,
                               call_amount=True, players=2)] * 4
        screen = FakeScreen(frames)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, fail_limit=1)
        self.assertEqual(screen.taps, [])
        self.assertEqual(bot.actions, 0)

    def test_wait_until_idle(self):
        acted = synth.render(hole=['7h', '2c'], buttons=True, players=2)
        idle = synth.render(hole=['7h', '2c'], buttons=False, players=2)
        bot, _ = self.make_bot([acted, idle])
        with mock.patch.object(main_mod.time, 'sleep'):
            self.assertTrue(bot.wait_until_idle(interval=0))
        bot2, _ = self.make_bot([acted] * 3)
        with mock.patch.object(main_mod.time, 'sleep'):
            self.assertFalse(bot2.wait_until_idle(interval=0, tries=3))

    def test_log_survives_non_utf8_console(self):
        """Консоль Windows в cp866 не знает «—»: лог не должен ронять бота."""
        bot, _ = self.make_bot([])
        buf = io.TextIOWrapper(io.BytesIO(), encoding='cp866', errors='strict')
        with mock.patch.object(sys, 'stdout', buf):
            bot.log('ИТОГИ — проверка')
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('ИТОГИ — проверка', f.read())

    def test_missing_adb_reports_clearly(self):
        screen = main_mod.AdbScreen(adb='/nonexistent/adb', serial='xxx')
        with self.assertRaises(SystemExit) as cm:
            screen.grab()
        self.assertIn('adb не найден', str(cm.exception))

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


class SoakTest(unittest.TestCase):
    """Сквозная проверка на случайных столах: карты, игроки, законность действия, тап."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='clubgg_soak_')
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

    def test_random_tables(self):
        cards = [f'{r}{s}' for r in card_reader.RANK_ORDER for s in card_reader.SUITS]
        rnd = random.Random(7)
        for i in range(40):
            deck = rnd.sample(cards, 7)
            n_board = rnd.choice([0, 3, 4, 5])
            hole, board = deck[:2], deck[2:2 + n_board]
            players = rnd.randint(2, config.MAX_PLAYERS)
            bet = rnd.choice([True, False])
            frame = synth.render(hole=hole, board=board, buttons=True, call_amount=bet,
                                 dealer=rnd.choice(['me'] + list(range(players - 1))),
                                 players=players, sitting_out=rnd.choice([0, 0, 1]))
            screen = FakeScreen([frame])
            bot = Bot(screen, tpl_dir=self.tpl,
                      log_path=os.path.join(self.tmp, 'soak.log'),
                      history_path=os.path.join(self.tmp, 'soak.jsonl'))
            e = bot.step()
            msg = f'#{i} {hole} {board} игроков={players} ставка={bet}'
            self.assertIsNotNone(e, msg)
            self.assertEqual([c for c in e['hole'] if c], hole, msg)
            self.assertEqual(e['board'], board, msg)
            self.assertEqual(e['players'], players, msg)
            self.assertIn(e['action'], ('fold', 'check', 'call', 'raise'), msg)
            if bet:
                self.assertNotEqual(e['action'], 'check', 'чек при ставке — ' + msg)
            else:
                self.assertNotEqual(e['action'], 'call', 'колл без ставки — ' + msg)
            self.assertEqual(len(screen.taps), 1, msg)
            _, y = screen.taps[0]
            self.assertTrue(config.REF_H * 0.86 <= y <= config.REF_H * 0.99, msg)


if __name__ == '__main__':
    unittest.main(verbosity=2)
