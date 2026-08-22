#!/usr/bin/env python3
"""Тесты человечных таймингов: диапазоны по действию, флаг, запас до таймаута."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                # noqa: E402
import main as main_mod                      # noqa: E402
from main import Bot, timing_ranges          # noqa: E402


class StubScreen:
    def __init__(self):
        self.taps = []

    def grab(self):
        return None

    def tap(self, x, y):
        self.taps.append((x, y))


class TimingHarness:
    """Бот с заглушкой экрана и выборка задержек по действию."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_timing_')

    def bot(self, **cfg):
        self.screen = StubScreen()
        return Bot(self.screen, players_path=os.path.join(self.tmp, 'players.json'),
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'), cfg=cfg)

    def samples(self, bot, action, n=200, elapsed=0.0):
        return [bot.human_delay(action, elapsed) for _ in range(n)]


class TimingTest(TimingHarness, unittest.TestCase):
    def test_raise_thinks_longer_than_check(self):
        bot = self.bot()
        raises = self.samples(bot, 'raise')
        checks = self.samples(bot, 'check')
        self.assertGreater(sum(raises) / len(raises), sum(checks) / len(checks))

    def test_every_action_stays_in_its_range_with_jitter(self):
        bot = self.bot()
        for action, key in (('raise', 'timing_raise'), ('call', 'timing_call'),
                            ('check', 'timing_fold'), ('fold', 'timing_fold')):
            lo, hi = config.TIMING_DEFAULTS[key]
            jitter = config.TIMING_JITTER
            with self.subTest(action=action):
                values = self.samples(bot, action)
                self.assertGreaterEqual(min(values), max(0.0, lo - jitter))
                self.assertLessEqual(max(values), hi + jitter)

    def test_delays_are_not_the_same_every_time(self):
        bot = self.bot()
        self.assertGreater(len(set(self.samples(bot, 'call'))), 10)

    def test_flag_off_means_no_delay(self):
        bot = self.bot(human_timing=False)
        self.assertEqual(set(self.samples(bot, 'raise')), {0.0})

    def test_ranges_come_from_the_device_record(self):
        bot = self.bot(timing_raise=[5.0, 5.0])
        self.assertEqual(bot.timing['timing_raise'], (5.0, 5.0))
        values = self.samples(bot, 'raise', n=50)
        self.assertGreaterEqual(min(values), 5.0 - config.TIMING_JITTER)

    def test_broken_ranges_fall_back_to_defaults(self):
        for bad in ({'timing_call': 'быстро'}, {'timing_call': [2.0]},
                    {'timing_call': [3.0, 1.0]}, {'timing_call': None}):
            with self.subTest(bad=bad):
                self.assertEqual(timing_ranges(bad)['timing_call'],
                                 tuple(config.TIMING_DEFAULTS['timing_call']))

    def test_never_more_than_the_cap(self):
        bot = self.bot(timing_raise=[30.0, 60.0])
        self.assertLessEqual(max(self.samples(bot, 'raise', n=50)), config.TIMING_MAX)

    def test_no_delay_when_the_turn_is_about_to_expire(self):
        """Кнопки висят давно — тапаем сразу, ход дороже правдоподобия."""
        bot = self.bot()
        late = config.TURN_BUDGET - config.TURN_RESERVE + 1
        self.assertEqual(set(self.samples(bot, 'raise', n=20, elapsed=late)), {0.0})

    def test_delay_is_trimmed_to_the_remaining_budget(self):
        bot = self.bot(timing_raise=[3.0, 3.0])
        left = config.TURN_BUDGET - config.TURN_RESERVE - 1.0
        self.assertLessEqual(max(self.samples(bot, 'raise', n=50, elapsed=left)), 1.0)

    def test_think_waits_then_the_tap_goes_through(self):
        bot = self.bot()
        with mock.patch.object(main_mod.time, 'sleep') as sleep:
            waited = bot.think('raise')
        self.assertGreater(waited, 0)
        sleep.assert_called_once_with(waited)

    def test_late_turn_is_logged_and_not_delayed(self):
        bot = self.bot()
        bot._turn_seen = main_mod.time.time() - 60
        with mock.patch.object(main_mod.time, 'sleep') as sleep:
            self.assertEqual(bot.think('call'), 0.0)
        sleep.assert_not_called()
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('тапаю сразу, без паузы', f.read())

    def test_flag_switches_live_from_the_panel(self):
        bot = self.bot()
        self.assertTrue(bot.human_timing)
        bot.apply_config({'human_timing': False}, quiet=True)
        self.assertFalse(bot.human_timing)
        self.assertEqual(bot.think('raise'), 0.0)


class FastFoldTest(TimingHarness, unittest.TestCase):
    """Рутина делается мгновенно: чек и сброс руки, в которую мы не вкладывались."""

    def think(self, bot, action):
        """think() без настоящего сна: возвращает, сколько бот собирался ждать."""
        with mock.patch.object(main_mod.time, 'sleep') as sleep:
            waited = bot.think(action)
        self.assertEqual(sleep.call_count, 1 if waited else 0)
        return waited

    def test_fold_without_investment_is_instant(self):
        bot = self.bot()
        self.assertEqual(set(self.samples(bot, 'fold')), {0.0})
        self.assertEqual(self.think(bot, 'fold'), 0.0)
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('фолд без вложений — тап сразу', f.read())

    def test_fold_after_checks_is_instant_too(self):
        """Чек-чек и сброс на общих картах — денег в банке наших нет."""
        bot = self.bot()
        bot._line_acted = {'preflop': 'check', 'flop': 'check'}
        self.assertEqual(set(self.samples(bot, 'fold')), {0.0})

    def test_fold_after_investment_is_short_but_not_zero(self):
        for line in ({'preflop': 'call'}, {'preflop': 'raise', 'flop': 'check'}):
            with self.subTest(line=line):
                bot = self.bot()
                bot._line_acted = dict(line)
                values = self.samples(bot, 'fold')
                self.assertGreater(max(values), 0.0)
                self.assertGreaterEqual(min(values), 0.0)
                self.assertLessEqual(max(values), 0.45)

    def test_check_is_a_blink(self):
        bot = self.bot()
        values = self.samples(bot, 'check')
        self.assertGreaterEqual(min(values), 0.05)
        self.assertLessEqual(max(values), 0.3)

    def test_check_ignores_a_widened_fold_range(self):
        """Диапазон фолда правится в панели, чек всегда мгновенный."""
        bot = self.bot(timing_fold=[3.0, 4.0])
        self.assertLessEqual(max(self.samples(bot, 'check')), 0.3)

    def test_raise_is_untouched(self):
        bot = self.bot()
        lo, hi = config.TIMING_DEFAULTS['timing_raise']
        values = self.samples(bot, 'raise')
        self.assertGreaterEqual(min(values), lo - config.TIMING_JITTER)
        self.assertLessEqual(max(values), hi + config.TIMING_JITTER)
        self.assertGreater(min(values), 0.0)

    def test_flag_off_kills_the_fast_paths_too(self):
        bot = self.bot()
        bot._line_acted = {'preflop': 'call'}
        bot.apply_config({'human_timing': False}, quiet=True)
        for action in ('fold', 'check', 'call', 'raise'):
            with self.subTest(action=action):
                self.assertEqual(set(self.samples(bot, action)), {0.0})

    def test_budget_still_wins_over_the_fast_paths(self):
        """Кнопки висят дольше бюджета — ни чек, ни фолд паузу не добавляют."""
        bot = self.bot()
        bot._line_acted = {'preflop': 'call'}
        late = config.TURN_BUDGET - config.TURN_RESERVE + 1
        for action in ('check', 'fold'):
            with self.subTest(action=action):
                self.assertEqual(set(self.samples(bot, action, n=20, elapsed=late)),
                                 {0.0})


class BotHarness:
    """Бот с заглушкой экрана и готовым состоянием «мой ход» (без картинок)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_step_')

    def state(self):
        return {'my_turn': True, 'in_hand': True, 'hole': ['Ah', 'Kd'], 'board': [],
                'street': 'preflop', 'players': 2, 'players_seated': 2,
                'position': 'BTN', 'dealer': 'me', 'first_to_act': 'opp',
                'pot_bb': 3.0, 'to_call_bb': None, 'has_bet': False,
                'cards_detail': {'hole': [{'card': 'Ah', 'rank_score': 1.0},
                                          {'card': 'Kd', 'rank_score': 1.0}]},
                'raise_presets': [{'i': 0, 'x': 880, 'y': 2315, 'enabled': True}],
                'chevron': None, 'presets_collapsed': False, 'showdown': False,
                'seats': [{'x': 1, 'y': 1, 'hero': True, 'in_hand': True},
                          {'x': 2, 'y': 2, 'hero': False, 'in_hand': True}],
                'taps': {'fold': (185, 2315), 'call': (535, 2315), 'raise': (880, 2315)},
                'call_fp': None}

    def bot(self, **cfg):
        self.screen = StubScreen()
        return Bot(self.screen, players_path=os.path.join(self.tmp, 'players.json'),
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'), cfg=cfg)


class StepTimingTest(BotHarness, unittest.TestCase):
    """Пауза стоит ПЕРЕД тапом и попадает в историю раздач."""

    def test_pause_happens_before_the_tap(self):
        bot = self.bot()
        order = []
        with mock.patch.object(main_mod.time, 'sleep', side_effect=lambda s: order.append('пауза')):
            with mock.patch.object(bot.screen, 'tap', side_effect=lambda x, y: order.append('тап')):
                entry = bot.step(state=self.state())
        self.assertEqual(order, ['пауза', 'тап'])
        self.assertGreater(entry['think_s'], 0)

    def test_no_pause_when_the_flag_is_off(self):
        bot = self.bot(human_timing=False)
        with mock.patch.object(main_mod.time, 'sleep') as sleep:
            entry = bot.step(state=self.state())
        sleep.assert_not_called()
        self.assertEqual(entry['think_s'], 0.0)
        self.assertEqual(len(self.screen.taps), 1, 'ход всё равно сделан')

    def test_dry_run_neither_waits_nor_taps(self):
        bot = self.bot()
        bot.dry_run = True
        with mock.patch.object(main_mod.time, 'sleep') as sleep:
            bot.step(state=self.state())
        sleep.assert_not_called()
        self.assertEqual(self.screen.taps, [])


class TurnClockTest(BotHarness, unittest.TestCase):
    """Часы хода: пауза берёт запас от момента, когда кнопки появились на экране."""

    def run_loop(self, states, **kw):
        bot = self.bot()
        frames = [object()] * (len(states) + 1)

        def grab():
            return frames.pop() if frames else None

        with mock.patch.object(bot.screen, 'grab', side_effect=grab), \
             mock.patch.object(main_mod.ts, 'read_state', side_effect=list(states)), \
             mock.patch.object(main_mod.time, 'sleep'):
            bot.run(interval=0, settle=0, fail_limit=1, **kw)
        return bot

    def test_clock_starts_when_the_turn_appears(self):
        mine = self.state()
        opp = dict(mine, my_turn=False)
        bot = self.run_loop([opp, mine], max_actions=1)
        self.assertEqual(bot._turn_sig, Bot._sig(mine))
        self.assertIsNotNone(bot._turn_seen)

    def test_clock_is_dropped_when_the_turn_is_not_ours(self):
        """Ход ушёл к оппоненту — следующий отсчёт начнётся заново, а не с прошлого."""
        opp = dict(self.state(), my_turn=False)
        bot = self.run_loop([opp, opp])
        self.assertIsNone(bot._turn_seen)
        self.assertIsNone(bot._turn_sig)


if __name__ == '__main__':
    unittest.main(verbosity=2)
