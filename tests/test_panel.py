#!/usr/bin/env python3
"""Тесты связки «панель -> devices.json -> бот»: сохранение настроек в панели и
их применение ботом без перезапуска.
"""
import contextlib
import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.request import urlopen

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

    def test_live_stack_flag_is_on_by_default(self):
        serial = self.mgr.devices[0]['serial']
        with mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            s = self.mgr.status(serial)
        self.assertTrue(s['flags']['live_stack'])
        self.assertFalse(s['stack_auto'])

    def test_live_stack_flag_is_saved(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'live_stack': False})
        with open(self.path, encoding='utf-8') as f:
            self.assertIs(json.load(f)[0]['live_stack'], False)

    def test_stack_written_by_the_bot_is_picked_up(self):
        """Бот правит devices.json на ходу — панель показывает новое значение."""
        serial = self.mgr.devices[0]['serial']
        devices = json.load(open(self.path, encoding='utf-8'))
        devices[0]['stack'] = 61.2
        devices[0]['stack_auto'] = True
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(devices, f)
        os.utime(self.path, (0, 1_700_000_100))
        with mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            s = self.mgr.status(serial)
        self.assertEqual(s['stack'], 61.2)
        self.assertTrue(s['stack_auto'])

    def test_manual_stack_is_not_auto_and_keeps_the_read_one_out(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'stack': 61.2})       # как будто писал бот
        self.mgr.device(serial)['stack_auto'] = True
        self.mgr.save_devices()
        self.mgr.save_config(serial, {'stack': 30.0})       # а теперь человек
        d = self.mgr.device(serial)
        self.assertEqual(d['stack'], 30.0)
        self.assertIs(d['stack_auto'], False)

    def test_start_turns_live_stack_off(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'live_stack': False})
        with mock.patch.object(self.panel.subprocess, 'Popen') as popen, \
             mock.patch.object(self.panel.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            popen.return_value = mock.Mock(pid=4242)
            with mock.patch.object(self.panel, 'LOGS_DIR', self.tmp):
                ok, _ = self.mgr.start(serial)
        self.assertTrue(ok)
        self.assertIn('--no-live-stack', popen.call_args[0][0])

    def test_bot_flags_are_on_by_default(self):
        serial = self.mgr.devices[0]['serial']
        with mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            flags = self.mgr.status(serial)['flags']
        self.assertTrue(flags['opponent_memory'])
        self.assertTrue(flags['human_timing'])

    def test_bot_flags_are_saved(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'opponent_memory': False, 'human_timing': False})
        with open(self.path, encoding='utf-8') as f:
            saved = json.load(f)[0]
        self.assertIs(saved['opponent_memory'], False)
        self.assertIs(saved['human_timing'], False)

    def test_start_turns_the_bot_flags_off(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'opponent_memory': False, 'human_timing': False})
        with mock.patch.object(self.panel.subprocess, 'Popen') as popen, \
             mock.patch.object(self.panel.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            popen.return_value = mock.Mock(pid=4242)
            with mock.patch.object(self.panel, 'LOGS_DIR', self.tmp):
                self.mgr.start(serial)
        cmd = popen.call_args[0][0]
        self.assertIn('--no-memory', cmd)
        self.assertIn('--no-human-timing', cmd)

    def test_timing_ranges_are_shown_and_saved(self):
        serial = self.mgr.devices[0]['serial']
        with mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            shown = self.mgr.status(serial)['timing']
        self.assertEqual(shown['timing_raise'], [1.0, 3.0])
        self.mgr.save_config(serial, {'timing_raise': [2.0, 4.0]})
        with mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            self.assertEqual(self.mgr.status(serial)['timing']['timing_raise'], [2.0, 4.0])

    def test_bad_timing_range_is_ignored(self):
        serial = self.mgr.devices[0]['serial']
        self.mgr.save_config(serial, {'timing_call': [3.0, 1.0], 'timing_fold': 'быстро'})
        d = self.mgr.device(serial)
        self.assertNotIn('timing_call', d)
        self.assertNotIn('timing_fold', d)

    def test_opponents_block_reads_players_json(self):
        import opponents
        players = os.path.join(self.tmp, 'players.json')
        db = opponents.Profiles(players)
        db.update('Оппонент 1', {'vpip': True, 'pfr': True, 'bets': 2, 'passive': 1})
        db.save()
        serial = self.mgr.devices[0]['serial']
        with mock.patch.object(self.panel.config, 'PLAYERS_FILE', players), \
             mock.patch.object(self.mgr, 'adb_online', return_value=[]):
            rows = self.mgr.status(serial)['opponents']
        self.assertEqual(rows[0]['name'], 'Оппонент 1')
        self.assertEqual(rows[0]['hands'], 1)
        self.assertAlmostEqual(rows[0]['vpip'], 1.0)
        self.assertAlmostEqual(rows[0]['agg'], 2.0)

    def test_opponents_block_marks_metrics_without_observations(self):
        """Панель показывает, какие цифры бот уже применяет, а какие ещё нет."""
        import opponents
        import strategy
        players = os.path.join(self.tmp, 'players.json')
        db = opponents.Profiles(players)
        db.db['Вася'] = dict(opponents.blank(opponents.NICK_NOTE), hands=25,
                             vpip_hands=10, three_bet_spots=3, agg_bets=4, agg_calls=2)
        db.save()
        with mock.patch.object(self.panel.config, 'PLAYERS_FILE', players):
            row = self.mgr.opponents()[0]
        self.assertTrue(row['ready']['vpip'], '25 рук — VPIP уже считается')
        for metric in ('pfr', 'three_bet', 'agg'):
            with self.subTest(metric=metric):
                self.assertFalse(row['ready'][metric])
        self.assertEqual(sorted(row['ready']), sorted(strategy.PROFILE_MIN_HANDS))

    def test_opponents_block_survives_a_missing_file(self):
        with mock.patch.object(self.panel.config, 'PLAYERS_FILE',
                               os.path.join(self.tmp, 'нет.json')):
            self.assertEqual(self.mgr.opponents(), [])

    def test_styles_for_the_dropdown(self):
        styles = self.mgr.styles()
        self.assertEqual(set(styles), set(st.STYLE_PRESETS))
        self.assertEqual(styles['loose']['title'], st.STYLE_TITLES['loose'])
        self.assertIn('cbet_pot', styles['loose']['sliders'])

    def test_a_foreign_devices_file_falls_back_to_defaults(self):
        """Панель — глобальный объект: на чужом файле она не должна падать при импорте.

        Раньше load_devices ловила только сломанный JSON. Целый, но не тот
        (словарь настроек, список строк) валил панель на старте — с ним она не
        открывалась вовсе, а починить файл было неоткуда.
        """
        for junk in ({'serial': 'abc'}, ['1cf5db29'], [], [{'name': 'без серийника'}],
                     'не json вовсе', 17):
            with self.subTest(junk=junk):
                with open(self.path, 'w', encoding='utf-8') as f:
                    if junk == 'не json вовсе':
                        f.write(junk)
                    else:
                        json.dump(junk, f)
                mgr = self.panel.BotManager()
                self.assertEqual([d['serial'] for d in mgr.devices],
                                 [d['serial'] for d in self.panel.DEFAULT_DEVICES])
                with open(self.path, encoding='utf-8') as f:
                    self.assertEqual(json.load(f), mgr.devices, 'умолчания записаны')

    def test_records_without_a_serial_are_dropped(self):
        """Мусор рядом с живой записью — выкидываем мусор, а не всё устройство."""
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump([{'serial': 'live1', 'style': 'loose'}, 'строка', {'name': 'ничей'}], f)
        mgr = self.panel.BotManager()
        self.assertEqual([d['serial'] for d in mgr.devices], ['live1'])

    def test_the_bot_writes_its_log_in_utf8(self):
        """Лог панель читает в utf-8; без этого бот писал в него по локали Windows.

        Русские строки приходили в cp1251, и живой лог в панели был «????».
        """
        serial = self.mgr.devices[0]['serial']
        with mock.patch.object(self.panel.subprocess, 'Popen') as popen, \
             mock.patch.object(self.panel.subprocess, 'CREATE_NO_WINDOW', 0, create=True):
            popen.return_value = mock.Mock(pid=4242)
            with mock.patch.object(self.panel, 'LOGS_DIR', self.tmp):
                self.assertTrue(self.mgr.start(serial)[0])
        self.addCleanup(self.mgr.log_files[serial].close)
        self.assertEqual(popen.call_args[1]['env'].get('PYTHONIOENCODING'), 'utf-8')
        self.assertIn('PATH', popen.call_args[1]['env'], 'остальное окружение на месте')

    def test_a_second_panel_does_not_take_a_busy_port(self):
        """Порт занят — вторая панель честно ругается и выходит с кодом 1.

        Иначе (на Windows SO_REUSEADDR это позволяет) поднималась вторая панель,
        запросы уходили то в неё, то в старую, а pid-файлы ботов держали обе.
        """
        busy = socket.socket()
        busy.bind(('127.0.0.1', 0))
        busy.listen(1)
        self.addCleanup(busy.close)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.panel.main(['--port', str(busy.getsockname()[1])])
        self.assertEqual(code, 1)
        self.assertIn('занят', out.getvalue())

    def test_the_panel_serves_and_closes_its_port(self):
        """Обычный запуск: панель села на порт, отдала список устройств и освободила его."""
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0))
            port = s.getsockname()[1]
        served = []

        def ask():
            with urlopen(f'http://127.0.0.1:{port}/api/devices', timeout=5) as r:
                served.append(json.load(r))

        client = threading.Thread(target=ask, daemon=True)

        def serve(server):
            """Вместо вечного цикла — ровно один запрос, потом выход как по Ctrl+C."""
            client.start()
            server.handle_request()      # запрос обслуживает отдельный поток
            client.join(5)
            raise KeyboardInterrupt

        with mock.patch.object(self.panel.PanelServer, 'serve_forever', autospec=True,
                               side_effect=serve), \
             mock.patch.object(self.panel, 'MANAGER', self.mgr), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.panel.main(['--port', str(port)]), 0)
        self.assertEqual(served[0]['total'], len(self.mgr.devices))
        # порт освобождён (server_close в finally) — следующая панель на него сядет
        self.panel.PanelServer(('127.0.0.1', port), self.panel.Handler).server_close()

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
