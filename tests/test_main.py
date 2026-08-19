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
import strategy                         # noqa: E402
import table_state as ts                # noqa: E402
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

    def make_bot(self, frames, dry_run=False, **kw):
        screen = FakeScreen(frames)
        kw.setdefault('tpl_dir', self.tpl)
        bot = Bot(screen, dry_run=dry_run,
                  log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'), **kw)
        return bot, screen

    def numeric_tpl(self):
        """Эталоны с цифрами: тогда бот читает банк и сумму колла, как на живом столе.

        В общий self.tpl цифры не кладём — половина тестов написана на состояние
        БЕЗ чисел (эталонов цифр может не быть вовсе), и решения там другие.
        """
        out = os.path.join(self.tmp, 'templates_num')
        if not os.path.isdir(out):
            shutil.copytree(self.tpl, out)
            for name in os.listdir(config.TEMPLATES_DIR):
                if name.startswith('digit_'):
                    shutil.copy(os.path.join(config.TEMPLATES_DIR, name), out)
        return out

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
        """Кнопки бета не видно -> вместо рейза колл: тапать в пустоту нельзя.

        Решение принимает стратегия, а не главный цикл: сет коллит потому, что
        это сильная рука, и в причине это видно (см. test_blocked_raise_*).
        """
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, dealer='me', players=2)
        H, W = frame.shape[:2]
        rx, _ = config.scale(config.BTN_RAISE, W, H)     # закрасить кнопку рейза сукном
        frame[int(H * 0.86):, rx - int(W * 0.16):] = synth.FELT
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertEqual(entry['action'], 'call')
        self.assertIsNone(entry['amount_bb'], 'размер несостоявшегося рейза не логируем')
        self.assertIn('рейз недоступен', entry['reason'])
        self.assertEqual(len(screen.taps), 1)
        self.assertLess(screen.taps[0][0], rx - int(W * 0.16))

    def test_raise_skips_dimmed_preset(self):
        """Нижний пресет погашен -> тапаем следующий живой, а не мёртвую кнопку.

        Живой баг: банк 2ББ, «33% Бет 0.6ББ» меньше минимальной ставки и погашен;
        бот бил в эталонную точку (881,2319) — ровно в него — и терял ход по
        таймауту (кадры 15:48:41 и 15:49:25, между ними 34 секунды тишины).
        """
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, dealer='me', players=2,
                             presets=3, dim_presets=(0,))
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertEqual(entry['action'], 'raise')
        self.assertEqual(len(screen.taps), 1)
        dead = ts.raise_presets(frame)[0]
        self.assertGreater(dead['y'], screen.taps[0][1],
                           'тап выше погашенной кнопки — по живому пресету')

    def test_raise_becomes_call_when_all_presets_dimmed(self):
        """Живой кнопки ставки нет вовсе -> играем пассивно, а не в пустоту."""
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=True, dealer='me', players=2,
                             presets=3, dim_presets=(0, 1, 2, 3))
        bot, screen = self.make_bot([frame])
        entry = bot.step()
        self.assertEqual(entry['action'], 'call')
        self.assertEqual(len(screen.taps), 1)
        self.assertLess(screen.taps[0][0], config.PRESET_X[0], 'тап по коллу, не по столбцу')

    def all_in_bot(self, dim, hole=('7d', '6d'), pot=51.7, call=23.7):
        """Кадр живой раздачи 19.08 09:52 #27: банк 51.7, колл 23.7, стол 3-max.

        dim — погашен ли столбец ставки целиком (оппонент в алл-ине, рейзить
        нечем). Чарт тот же, что играл бот, и стек тот же — 69.6ББ.
        """
        frame = synth.render(hole=list(hole), board=[], buttons=True,
                             call_amount=call, pot_bb=pot, dealer='me',
                             players=2, sitting_out=1, presets=3,
                             dim_presets=(0, 1, 2, 3) if dim else ())
        chart = strategy.load_chart(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'charts', 'gto_6max.json'))
        return self.make_bot([frame], tpl_dir=self.numeric_tpl(), chart=chart,
                             stack_bb=69.6)

    def test_all_in_against_suited_connector_is_folded(self):
        """Главный баг: 76s заколлил алл-ин 23.7ББ (34% стека) вместо фолда.

        Стратегия выдавала «3-бет на велью» (76s есть в 3-бете чарта gto_6max),
        живого пресета рейза не было, и главный цикл молча менял рейз на колл.
        """
        bot, screen = self.all_in_bot(dim=True)
        entry = bot.step()
        self.assertEqual(entry['hole'], ['7d', '6d'])
        self.assertEqual((entry['pot_bb'], entry['to_call_bb']), (51.7, 23.7))
        self.assertEqual((entry['position'], entry['players_seated']), ('BTN', 3))
        self.assertEqual(entry['action'], 'fold', entry['reason'])
        self.assertNotIn('невозможен -> call', entry['reason'])
        self.assertEqual(len(screen.taps), 1)
        self.assertLess(screen.taps[0][0], 400, 'тап по фолду, а не по коллу')

    def test_all_in_is_called_with_premium(self):
        """Обратная сторона: премиум против алл-ина коллирует, а не пасует."""
        bot, screen = self.all_in_bot(dim=True, hole=('Ah', 'Kd'))
        entry = bot.step()
        self.assertEqual(entry['action'], 'call', entry['reason'])
        self.assertIn('пот-оддсам', entry['reason'])
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('рейз недоступен', f.read())

    def test_three_bet_against_a_normal_open_is_kept(self):
        """Первое действие той же раздачи (09:52:19): открытие 0.5ББ при банке 1.5.

        Здесь 3-бет с 76s по чарту законен — фикс не должен его трогать.
        """
        bot, screen = self.all_in_bot(dim=False, pot=1.5, call=0.5)
        entry = bot.step()
        self.assertEqual((entry['pot_bb'], entry['to_call_bb']), (1.5, 0.5))
        self.assertEqual(entry['action'], 'raise', entry['reason'])

    # ---------- свёрнутый столбец ставки: двухшаговый тап ----------
    def collapsed_bot(self, frames, dry_run=False):
        """Бот без пауз между тапом шеврона и перечитыванием кадра."""
        bot, screen = self.make_bot(frames, dry_run=dry_run)
        bot.EXPAND_WAIT = 0
        return bot, screen

    @staticmethod
    def collapsed_frame(**kw):
        """Свёрнутый столбец: одна кнопка «Бет» + шеврон. Погашена — ставить нечем."""
        return synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                            call_amount=True, dealer='me', players=2, chevron=True, **kw)

    def test_collapsed_column_expanded_before_bet(self):
        """Единственная кнопка ставки погашена -> тап шеврона, потом живой пресет.

        Из отчёта по живой сессии: свёрнутый столбец + погашенная кнопка = бот
        чекал вместо ставки, потому что раскрывать столбец не умел.
        """
        # после раскрытия нижняя кнопка так и остаётся погашенной (её размер
        # меньше минимальной ставки) — ставку делает пресет покрупнее
        frames = [self.collapsed_frame(dim_presets=(0,)),
                  self.collapsed_frame(presets=3, dim_presets=(0,))]
        bot, screen = self.collapsed_bot(frames)
        entry = bot.step()
        self.assertEqual(entry['action'], 'raise')
        self.assertEqual(len(screen.taps), 2, 'шеврон + пресет')
        W, H = frames[0].shape[1], frames[0].shape[0]
        self.assertEqual(screen.taps[0], config.scale(config.CHEVRON, W, H))
        self.assertGreater(screen.taps[1][0], int(config.PRESET_X[0] * W / config.REF_W),
                           'второй тап — по столбцу ставки')
        dead = ts.raise_presets(frames[1])[0]
        self.assertLess(screen.taps[1][1], dead['y'],
                        'по живому пресету выше, а не по погашенной кнопке')

    def test_column_not_expanded_falls_back_to_passive(self):
        """Столбец не раскрылся (кадр не изменился) -> безопасный колл/чек."""
        frames = [self.collapsed_frame(dim_presets=(0,))] * 3
        bot, screen = self.collapsed_bot(frames)
        entry = bot.step()
        self.assertEqual(entry['action'], 'call')
        self.assertEqual(len(screen.taps), 2, 'шеврон (один раз!) + колл')
        self.assertLess(screen.taps[1][0], config.PRESET_X[0], 'тап по коллу, не по столбцу')
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('столбец не раскрылся', f.read())

    def test_column_expand_stops_when_turn_is_gone(self):
        """Пока раскрывали столбец, ход ушёл — не тапаем в чужой кадр."""
        frames = [self.collapsed_frame(dim_presets=(0,)),
                  synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=False)]
        bot, screen = self.collapsed_bot(frames)
        entry = bot.step()
        self.assertEqual(entry['action'], 'call', 'играем пассивно по прежнему кадру')
        self.assertEqual(len(screen.taps), 2)

    def test_no_chevron_tap_when_column_already_open(self):
        """Столбец раскрыт — шеврон не трогаем (иначе свернём его сами)."""
        frames = [self.collapsed_frame(presets=3)]
        bot, screen = self.collapsed_bot(frames)
        entry = bot.step()
        self.assertEqual(entry['action'], 'raise')
        self.assertEqual(len(screen.taps), 1)

    def test_dry_run_does_not_tap_chevron(self):
        frames = [self.collapsed_frame(dim_presets=(0,))]
        bot, screen = self.collapsed_bot(frames, dry_run=True)
        entry = bot.step()
        self.assertEqual(entry['action'], 'call')
        self.assertEqual(screen.taps, [])

    def test_expand_wanted_when_size_does_not_fit(self):
        """Кнопка живая, но размер не тот — столбец тоже стоит раскрыть."""
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             call_amount=False, dealer='me', players=2, chevron=True)
        state = ts.read_state(frame, tpl_dir=self.tpl)
        bot, _ = self.collapsed_bot([])
        self.assertTrue(state['presets_collapsed'])
        self.assertTrue(bot.wants_expand(state, 1.00), 'доступен только нижний пресет 33%')
        self.assertFalse(bot.wants_expand(state, 0.33), 'нужный размер и так под рукой')
        self.assertFalse(bot.wants_expand(state, None))

    def test_bet_size_picks_nearest_preset(self):
        """Пресет выбирается по доле банка из стратегии, а не «всегда нижний»."""
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], buttons=True,
                             presets=3, players=2)
        state = ts.read_state(frame, tpl_dir=self.tpl)
        bot, _ = self.make_bot([])
        rows = {p['i']: (p['x'], p['y']) for p in state['raise_presets']}
        for frac, row in ((0.30, 0), (0.55, 1), (0.80, 2), (1.10, 3)):
            self.assertEqual(bot.bet_point(state, frac), rows[row], f'доля банка {frac}')
        self.assertEqual(bot.bet_point(state, None), rows[0],
                         'без доли — самый мелкий пресет')

    def test_no_tap_on_showdown(self):
        """Вскрытие: плашки «Показать» — не кнопки действий, тапать их нельзя.

        Живые кадры 15:47:32 и 15:48:20: бот принял вскрытие за свой ход, решил
        RAISE и тапнул «Показать», открыв столу свои карты.
        """
        frame = synth.render(hole=['9h', '9c'], board=['9d', '5s', '2c'], showdown=True,
                             players=2)
        bot, screen = self.make_bot([frame])
        self.assertIsNone(bot.step())
        self.assertEqual(screen.taps, [])
        self.assertTrue(bot.last_state['showdown'])
        self.assertFalse(bot.last_state['my_turn'])

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

    def test_acts_again_when_opponent_reraises(self):
        """Ререйз оппонента = новая сумма колла -> играем снова, а не молчим.

        Живой тест: QQ -> RAISE, оппонент переставил; сигнатура не учитывала
        to_call_bb, has_bet оставался True, и бот просидел до таймаута (фолд).
        """
        frame = synth.render(hole=['Qs', 'Qc'], board=[], buttons=True,
                             call_amount=True, dealer='opp', players=2)
        base = ts.read_state(frame, tpl_dir=self.tpl)
        base['my_turn'] = True
        base['in_hand'] = True
        st1 = dict(base)
        st1['to_call_bb'] = 1.0          # рейз оппонента, который мы переставили
        st1['has_bet'] = True
        st2 = dict(base)
        st2['to_call_bb'] = 6.0          # после нашего рейза оппонент переставил
        st2['has_bet'] = True
        self.assertNotEqual(Bot._sig(st1), Bot._sig(st2),
                            'ререйз обязан менять сигнатуру (сумма колла)')
        states = iter([st1] * 4 + [st2] * 4)

        def fake_read(img, tpl_dir=None):
            return next(states)

        screen = FakeScreen([frame] * 8)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'), \
             mock.patch.object(main_mod.ts, 'read_state', side_effect=fake_read):
            bot.run(interval=0, settle=0, max_actions=5, retry_after=1000)
        self.assertEqual(bot.actions, 2, 'ререйз оппонента = новое решение')
        self.assertEqual(len(screen.taps), 2)

    def test_acts_again_after_opponent_turn_same_sig(self):
        """Ход уходил к оппоненту и вернулся при ТОЙ ЖЕ сигнатуре = ререйз.

        Живой тест: сумма колла не читается (нет эталонов цифр, to_call_bb=None
        всегда), поэтому ререйз НЕ меняет сигнатуру. Различаем его по факту:
        между нашими ходами был кадр my_turn=False (оппонент переставил).
        """
        frame = synth.render(hole=['Qs', 'Qc'], board=[], buttons=True,
                             call_amount=True, dealer='opp', players=2)
        base = ts.read_state(frame, tpl_dir=self.tpl)
        base['my_turn'] = True
        base['in_hand'] = True
        base['to_call_bb'] = None          # OCR суммы не работает — как вживую
        base['has_bet'] = True
        mine = dict(base)                  # наш ход (до и после ререйза — одна сигнатура)
        opp = dict(base)
        opp['my_turn'] = False             # оппонент думает/переставляет
        states = iter([mine, mine] + [opp] * 3 + [mine] * 4)

        def fake_read(img, tpl_dir=None):
            return next(states)

        screen = FakeScreen([frame] * 12)
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'), \
             mock.patch.object(main_mod.ts, 'read_state', side_effect=fake_read):
            bot.run(interval=0, settle=0, max_actions=5, retry_after=1000)
        self.assertEqual(bot.actions, 2,
                         'ход вернулся после оппонента = новое решение, не таймаут')
        self.assertEqual(len(screen.taps), 2)

    def test_call_fp_changes_on_reraise(self):
        """Отпечаток зоны суммы меняется при переставке: ререйз виден даже
        когда to_call_bb не читается.

        Живой баг: CALL -> оппонент переставил -> has_bet остался True,
        to_call_bb=None, сигнатура не менялась, бот молчал до следующей карты.
        call_fp (раскладка жёлтых пикселей суммы) — единственный сигнал.
        """
        import numpy as np
        f1 = synth.render(hole=['Qs', 'Qc'], board=[], buttons=True,
                          call_amount=True, dealer='opp', players=2)
        st1 = ts.read_state(f1, tpl_dir=self.tpl)
        # «переставка»: та же доска, та же ставка (True), но сумма больше
        f2 = synth.render(hole=['Qs', 'Qc'], board=[], buttons=True,
                          call_amount=True, dealer='opp', players=2)
        st2 = ts.read_state(f2, tpl_dir=self.tpl)
        st2['to_call_bb'] = 8.0           # ререйз: колл дороже
        st1['to_call_bb'] = 2.5
        st1['has_bet'] = st2['has_bet'] = True
        # синтетика рисует одинаковую сумму — подменим отпечаток руками:
        # разные суммы = разные раскладки пикселей
        st2['call_fp'] = tuple((i * 7) % 5 for i in range(32))
        self.assertNotEqual(Bot._sig(st1), Bot._sig(st2),
                            'переставка обязана менять сигнатуру (call_fp)')

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

    def test_waits_for_both_hole_cards(self):
        """Не действуем, пока не распознаны ОБЕ карманные карты — перечитываем кадр.

        Живой тест: [None, '2c'] и '7d'->'2d' ломали решения (фолд карманной пары).
        """
        partial = synth.render(hole=['Ah', None], buttons=True, call_amount=True, players=2)
        full = synth.render(hole=['Ah', 'Kd'], buttons=True, call_amount=True, players=2)
        screen = FakeScreen([partial, full, full, full])
        bot = Bot(screen, tpl_dir=self.tpl, log_path=os.path.join(self.tmp, 'bot.log'),
                  history_path=os.path.join(self.tmp, 'hand_history.jsonl'))
        with mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, max_actions=2, retry_after=1000)
        self.assertEqual(bot.actions, 1)
        self.assertEqual(len(screen.taps), 1)
        self.assertEqual([c for c in bot.last_state['hole'] if c], ['Ah', 'Kd'],
                         'решение принято по ПОЛНОМУ чтению карт')

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
