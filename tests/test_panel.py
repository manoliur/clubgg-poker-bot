#!/usr/bin/env python3
"""Тесты связки «панель -> devices.json -> бот»: сохранение настроек в панели и
их применение ботом без перезапуска.
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import strategy as st                      # noqa: E402
from main import Bot                       # noqa: E402


def state(**kw):
    base = {'hole': ['Ah', 'Kd'], 'board': [], 'street': 'preflop', 'has_bet': False,
            'to_call_bb': None, 'pot_bb': 3.0, 'position': 'BTN', 'players': 6}
    base.update(kw)
    return base


class StubScreen:
    def grab(self):
        return None

    def tap(self, x, y):
        pass


class LiveReloadTest(unittest.TestCase):
    """Панель сохранила devices.json — бот применяет настройки со следующего решения."""

    SERIAL = 'test1234'

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_cfg_')
        self.path = os.path.join(self.tmp, 'devices.json')
        self.write({'serial': self.SERIAL, 'style': 'standard'})

    def write(self, *records):
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(list(records), f)
        # mtime сравнивается по значению: на грубых таймерах Windows подвинем его сами
        os.utime(self.path, (0, self._stamp()))

    _tick = 0

    def _stamp(self):
        LiveReloadTest._tick += 1
        return 1_700_000_000 + LiveReloadTest._tick

    def bot(self, **kw):
        return Bot(StubScreen(), serial=self.SERIAL, devices_path=self.path,
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'), **kw)

    def test_new_style_applies_without_restart(self):
        bot = self.bot()
        self.assertAlmostEqual(bot.chart.settings['cbet_pot'], 0.6)
        self.write({'serial': self.SERIAL, 'style': 'aggressive'})
        bot.decide(state(hole=['Ah', 'Kd']))
        self.assertAlmostEqual(bot.chart.settings['cbet_pot'], 0.7)

    def test_flag_toggles_live(self):
        bot = self.bot()
        self.assertFalse(bot.chart.settings['bet_sizing'])
        self.write({'serial': self.SERIAL, 'style': 'standard', 'bet_sizing': True})
        bot.decide(state(hole=['Ah', 'Kd']))
        self.assertTrue(bot.chart.settings['bet_sizing'])

    def test_unchanged_file_is_not_reread(self):
        """Файл читается по mtime: на каждом решении диск не дёргаем."""
        bot = self.bot()
        for _ in range(3):
            bot.decide(state(hole=['Ah', 'Kd']))
        with mock.patch('builtins.open', side_effect=AssertionError('лишнее чтение')):
            self.assertFalse(bot.refresh_settings())

    def test_stack_comes_from_the_panel(self):
        bot = self.bot(stack_bb=100.0)
        self.write({'serial': self.SERIAL, 'stack': 25.0})
        bot.decide(state(hole=['Ah', 'Kd']))
        self.assertEqual(bot.stack_bb, 25.0)

    def test_other_devices_are_ignored(self):
        bot = self.bot()
        self.write({'serial': 'someone-else', 'style': 'aggressive'})
        bot.decide(state(hole=['Ah', 'Kd']))
        self.assertAlmostEqual(bot.chart.settings['cbet_pot'], 0.6)

    def test_broken_file_keeps_previous_settings(self):
        bot = self.bot()
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write('{это не json')
        os.utime(self.path, (0, self._stamp()))
        bot.decide(state(hole=['Ah', 'Kd']))
        self.assertAlmostEqual(bot.chart.settings['cbet_pot'], 0.6)

    def test_missing_file_is_not_an_error(self):
        os.remove(self.path)
        bot = self.bot()
        self.assertFalse(bot.refresh_settings())
        self.assertEqual(bot.decide(state(hole=['Ah', 'Kd']))['action'], 'raise')

    def test_cli_settings_are_the_fallback(self):
        """Ключи запуска работают, пока в devices.json нет своей записи."""
        self.write({'serial': 'someone-else'})
        bot = self.bot(cfg={'style': 'tighty'})
        self.assertAlmostEqual(bot.chart.settings['cbet_pot'], 0.55)

    def test_default_chart_is_not_touched(self):
        """Настройки бота живут в его копии чарта, а не в общей таблице процесса."""
        bot = self.bot(cfg={'style': 'aggressive'})
        self.assertAlmostEqual(bot.chart.settings['cbet_pot'], 0.7)
        self.assertAlmostEqual(st.DEFAULT_CHART.settings['cbet_pot'], 0.6)

    def test_blocker_bluff_frequency_counter(self):
        bot = self.bot(cfg={'blocker_bluff': True, 'blocker_bluff_every': 3})
        allowed = []
        for i in range(6):
            bot.hand_id = i
            s = state(hole=['As', '4d'], board=['Ks', '9s', '2s', '7h', '3c'],
                      street='river', has_bet=False, pot_bb=10.0, players=2)
            allowed.append(bot.decide(s)['action'])
        self.assertEqual(allowed.count('raise'), 2)      # 1 из 3
        self.assertEqual(allowed[0], 'raise')

    def test_the_same_spot_is_counted_once(self):
        """За один ход стратегию спрашивают дважды (раскрытие столбца, no_raise)."""
        bot = self.bot(cfg={'blocker_bluff': True, 'blocker_bluff_every': 3})
        s = state(hole=['As', '4d'], board=['Ks', '9s', '2s', '7h', '3c'],
                  street='river', has_bet=False, pot_bb=10.0, players=2)
        self.assertEqual(bot.decide(s)['action'], 'raise')
        self.assertEqual(bot.decide(s)['action'], 'raise')
        self.assertEqual(bot._blocker_spots, 1)


class PanelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_panel_')
        self.path = os.path.join(self.tmp, 'devices.json')
        import panel
        self.panel = panel
        patcher = mock.patch.object(panel, 'DEVICES_FILE', self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.mgr = panel.BotManager()

    def test_defaults_are_written_with_a_style(self):
        with open(self.path, encoding='utf-8') as f:
            devices = json.load(f)
        self.assertEqual(devices[0]['style'], st.DEFAULT_STYLE)

    def test_save_config_stores_style_flags_and_thresholds(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'style': 'aggressive', 'bet_sizing': True,
                                      'multiway_tight': False, 'cbet_pot': 0.8})
        with open(self.path, encoding='utf-8') as f:
            saved = json.load(f)[0]
        self.assertEqual(saved['style'], 'aggressive')
        self.assertIs(saved['bet_sizing'], True)
        self.assertIs(saved['multiway_tight'], False)
        self.assertEqual(saved['cbet_pot'], 0.8)

    def test_bad_values_are_ignored(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'style': 'ультра-луз', 'cbet_pot': 'много'})
        d = self.mgr.device(serial)
        self.assertEqual(d.get('style'), st.DEFAULT_STYLE)
        self.assertNotIn('cbet_pot', d)

    def test_status_shows_what_the_bot_will_use(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'style': 'tighty', 'blocker_bluff': True})
        with mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            s = self.mgr.status(serial)
        self.assertEqual(s['style'], 'tighty')
        self.assertTrue(s['flags']['blocker_bluff'])
        self.assertAlmostEqual(s['sliders']['cbet_pot'], 0.55)

    def test_styles_for_the_dropdown(self):
        styles = self.mgr.styles()
        self.assertEqual(set(styles), set(st.STYLE_PRESETS))
        self.assertEqual(styles['loose']['title'], st.STYLE_TITLES['loose'])
        self.assertIn('cbet_pot', styles['loose']['sliders'])

    def test_start_passes_the_style(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'style': 'loose'})
        with mock.patch.object(self.panel.subprocess, 'Popen') as popen, \
             mock.patch.object(self.panel.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            popen.return_value = mock.Mock(pid=4242)
            with mock.patch.object(self.panel, 'LOGS_DIR', self.tmp):
                ok, _ = self.mgr.start(serial)
        self.assertTrue(ok)
        cmd = popen.call_args[0][0]
        self.assertIn('--style', cmd)
        self.assertEqual(cmd[cmd.index('--style') + 1], 'loose')
        self.assertIn('--serial', cmd)


if __name__ == '__main__':
    unittest.main(verbosity=2)
