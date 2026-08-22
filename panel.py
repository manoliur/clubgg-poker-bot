#!/usr/bin/env python3
"""Веб-панель управления ботами ClubGG. Запуск: python panel.py [--port 8090]

Открыть http://127.0.0.1:8090 — список телефонов (adb), настройки
(чарт, агрессия, защита), кнопки Старт/Стоп, живой лог.

Только стандартная библиотека: http.server + subprocess + json.
"""
import argparse
import datetime
import json
import os
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config                      # noqa: E402  (свои модули, сторонних библиотек нет)
import opponents                   # noqa: E402
import stats                       # noqa: E402
import strategy                    # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
DEVICES_FILE = os.path.join(BASE, 'devices.json')
LOGS_DIR = os.path.join(BASE, 'logs')
PYTHON = sys.executable
ADB = os.environ.get('CLUBGG_ADB', r'E:/down/platform-tools/platform-tools/adb.exe')

# имя -> (serial по умолчанию, описание)
DEFAULT_DEVICES = [
    {'name': 'Телефон 1', 'serial': '1cf5db29', 'chart': 'charts/6max_standard.json',
     'aggression': 1.0, 'defense': 1.0, 'stack': 69.6, 'live_stack': True,
     'style': strategy.DEFAULT_STYLE},
]

# Переключатели «вкл/выкл»: ключ -> подпись в панели.
FLAGS = [
    ('bet_sizing', 'Размеры ставок по силе/улице'),
    ('multiway_tight', 'Мультипот — тайтовее'),
    ('short_stack_mode', 'Короткий стек — push/fold'),
    ('blocker_bluff', 'Блеф с блокерами (ривер)'),
    ('position_aware', 'Учитывать позицию (OOP)'),
    ('kicker_grades', 'Кикер топ-пары'),
    ('river_value_bet', 'Тонкая ставка на ривере'),
    ('opponent_lines', 'Следить за игрой оппонента в раздаче'),
]

# Переключатели самого БОТА (в настройки стратегии не входят, живут только в
# записи устройства). live_stack: бот читает свой стек с экрана раз в раздачу и
# сам обновляет поле stack; выключено — играет по числу из панели, как раньше.
# opponent_memory: копить статистику оппонентов и подстраиваться под неё.
# human_timing: случайная пауза «раздумья» перед тапом.
# read_nicks: читать ники с плашек (нужен tesseract); выключено — оппоненты
# зовутся по местам («Оппонент 2»), как было раньше.
BOT_FLAGS = [
    ('live_stack', 'Живой стек — читать с экрана'),
    ('opponent_memory', 'Память оппонентов — статистика'),
    ('human_timing', 'Человечные паузы перед ходом'),
    ('read_nicks', 'Ники оппонентов — читать с экрана'),
]
BOT_FLAG_KEYS = [f[0] for f in BOT_FLAGS]

# Диапазоны пауз (секунды) — правятся в devices.json, панель их показывает.
TIMING_KEYS = list(config.TIMING_DEFAULTS)
TIMING_TITLES = {'timing_raise': 'рейз/бет', 'timing_call': 'колл',
                 'timing_fold': 'фолд'}

# Ползунки порогов: ключ, подпись, min, max, шаг.
SLIDERS = [
    ('cbet_pot', 'Ставка велью', 0.3, 1.0, 0.05),
    ('nuts_pot', 'Ставка натсом', 0.4, 1.0, 0.05),
    ('medium_max_price', 'Порог колла средней', 0.1, 0.7, 0.02),
    ('draw_min_equity', 'Эквити дро', 0.20, 0.50, 0.01),
    ('short_stack_bb', 'Короткий стек, ББ', 10, 60, 1),
    ('bet_nuts', 'Размер: натс', 0.4, 1.0, 0.05),
    ('bet_strong', 'Размер: сильная', 0.3, 1.0, 0.05),
    ('bet_medium', 'Размер: средняя', 0.2, 0.9, 0.05),
    ('bet_draw', 'Размер: дро', 0.2, 0.9, 0.05),
    # сколько наблюдений нужно метрике профиля, чтобы бот её применял
    ('min_hands_vpip', 'Рук на VPIP', 5, 100, 5),
    ('min_hands_pfr', 'Рук на PFR', 10, 200, 10),
    ('min_hands_three_bet', 'Рук на 3-бет', 20, 400, 10),
    ('min_hands_agg', 'Рук на агрессию', 20, 400, 10),
]
SLIDER_KEYS = [s[0] for s in SLIDERS]
FLAG_KEYS = [f[0] for f in FLAGS]

# Галочек полтора десятка — списком они читаются как свалка. Группы: про что
# именно эта настройка. Каждый ключ должен попасть ровно в одну группу (тест).
FLAG_GROUPS = [
    ('Стратегия', ['bet_sizing', 'multiway_tight', 'short_stack_mode',
                   'blocker_bluff', 'position_aware', 'kicker_grades',
                   'river_value_bet']),
    ('Оппоненты', ['opponent_lines', 'opponent_memory', 'read_nicks']),
    ('Поведение', ['live_stack', 'human_timing']),
]

# Однострочные описания стилей для карточек-пресетов на вкладке «Настройки».
STYLE_NOTES = {
    'tighty': 'Мало рук, осторожные ставки. Ждёт сильную карту.',
    'standard': 'Золотая середина: так задумано по умолчанию.',
    'aggressive': 'Чаще и крупнее ставит, давит на оппонентов.',
    'loose': 'Играет много рук и чаще отвечает на ставки.',
}

# ---------------------------------------------------------------------------
# процессы ботов
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Подсказки для панели — простыми словами, без покерного жаргона.
# ---------------------------------------------------------------------------
TIPS = {
    'chart': 'Таблица правил игры: какие руки разыгрывать с каждой позиции за столом. Можно загрузить свою.',
    'style': 'Готовый характер игры. Тайтовый — мало рук, осторожно. Стандартный — сбалансированно. Агрессивный — чаще и крупнее ставит. Лузовый — играет много рук.',
    'stack': 'Сколько «больших ставок» у бота (1 ББ = две малые ставки). Нужно боту для расчётов. Обычно бот следит за своим стеком сам.',
    'aggression': 'Общая смелость бота: больше — крупнее ставки и чаще повышения. 1.0 — как задумано, 1.5 — заметно агрессивнее.',
    'defense': 'Готовность бота отвечать на ставки оппонента: больше — чаще коллит, меньше — чаще сбрасывает карты.',
    'bet_sizing': 'Разные размеры ставок по силе руки: с сильной — крупнее, со слабой — меньше. Выкл — всегда ставка около половины банка.',
    'multiway_tight': 'Когда в раздаче 3 и больше игроков, бот играет осторожнее: не блефует, реже ставит, коллит только выгодные ставки.',
    'short_stack_mode': 'Когда у бота мало фишек (меньше порога «Короткий стек, ББ»), он играет проще: идёт ва-банк с сильными руками вместо обычных ставок.',
    'blocker_bluff': 'Блеф на последней карте, когда карты бота мешают оппоненту собрать самую сильную комбинацию (например, у нас туз масти — значит, у оппонента не может быть тузового флеша).',
    'position_aware': 'Бот учитывает, кто ходит первым. Когда он ходит первым (без позиции) — реже ставит со средними руками.',
    'kicker_grades': 'Бот смотрит на вторую карту в руке. Пара тузов с королём — сильная рука, её он защищает дольше; та же пара с семёркой — слабая, с ней он сбрасывает раньше.',
    'river_value_bet': 'На последней карте один на один бот ставит половину банка средней рукой (две пары, старшая пара с хорошей второй картой), если общие карты не опасные и оппонент не любит повышать. Оппонент не собрал свою комбинацию и заплатит — а бот раньше просто пропускал ход.',
    'opponent_lines': 'Бот помнит, что оппонент делал в текущей раздаче: повышал ли до общих карт, пропускал ли ход, на скольких кругах ставил. Против того, кто давит, играет осторожнее; против того, кто пропустил ход, — смелее. Память живёт одну раздачу и не путается с общей статистикой.',
    'live_stack': 'Бот сам читает свой стек фишек с экрана и обновляет число в панели. Выкл — стек задаётся вручную.',
    'opponent_memory': 'Бот запоминает оппонентов: как часто они играют, повышают ставки — и подстраивается под них. Выкл — играет только по своим картам.',
    'human_timing': 'Бот «думает» перед ходом, как человек: перед крупной ставкой — дольше, чек и фолд — мгновенно. Выкл — действует мгновенно всегда.',
    'read_nicks': 'Бот читает ники оппонентов с экрана, чтобы вести статистику по конкретным игрокам, а не по местам за столом.',
    'cbet_pot': 'Размер обычной ставки бота с сильной рукой после флопа (в долях банка).',
    'nuts_pot': 'Размер ставки с самой сильной возможной комбинацией (в долях банка).',
    'medium_max_price': 'Самая дорогая ставка, на которую бот ещё отвечает со средней рукой. Больше — чаще коллит средними руками.',
    'draw_min_equity': 'Насколько выгодно боту ждать доборную карту (флеш/стрит). Больше — реже ждёт, чаще сбрасывает.',
    'short_stack_bb': 'Граница «короткого стека»: сколько больших ставок должно остаться, чтобы бот перешёл в режим ва-банка.',
    'bet_nuts': 'Размер ставки с самой сильной комбинацией (в долях банка) — при включённом «Размере ставок».',
    'bet_strong': 'Размер ставки с сильной комбинацией (в долях банка) — при включённом «Размере ставок».',
    'bet_medium': 'Размер ставки со средней комбинацией (в долях банка) — при включённом «Размере ставок».',
    'bet_draw': 'Размер ставки, когда бот ждёт доборную карту (в долях банка) — при включённом «Размере ставок».',
    'min_hands_vpip': 'Сколько раздач нужно понаблюдать, чтобы бот начал использовать статистику «как часто оппонент играет».',
    'min_hands_pfr': 'Сколько раздач нужно для статистики «как часто оппонент повышает ставку до флопа».',
    'min_hands_three_bet': 'Сколько раздач нужно для статистики «как часто оппонент повышает повторно, когда уже кто-то повысил».',
    'min_hands_agg': 'Сколько раздач нужно для статистики «как часто оппонент ставит сам, а не просто отвечает».',
    'vpip': 'Как часто оппонент вступает в игру (в процентах раздач). Чем больше — тем «лузовее» он играет.',
    'pfr': 'Как часто оппонент повышает ставку до появления общих карт. Показывает его агрессию на раннем этапе.',
    'three_bet': 'Как часто оппонент повышает повторно, когда кто-то уже повысил до него.',
    'agg': 'Во сколько раз чаще оппонент ставит сам, чем просто отвечает на чужие ставки. Больше — агрессивнее.',
    'timing': 'Сколько секунд бот «думает» перед ходом: перед повышением думает, чек и сброс делает мгновенно. Сброс руки, в которую он ничего не вложил (мимо диапазона до общих карт, после пропусков хода), — вообще без паузы; здешний диапазон — для сброса руки, которую он уже играл. Пауза не больше 5 секунд и только когда есть запас времени.',
    'bb_value': 'Сколько фишек стоит одна большая ставка (ББ) за вашим столом. Стол на 1000 фишек с блайндами 10/20 — это 20 фишек в ББ. Нужно только для показа сумм: бот считает в ББ, а панель переводит в фишки.',
    'stats': 'Победа или поражение считаются по стеку: сколько фишек было в начале раздачи и сколько стало в начале следующей. Блайнды и наши ставки в эту разницу уже входят — это чистый результат.',
    'streak': 'Сколько раздач подряд закончились одинаково: победами или поражениями. Обрывается первой раздачей с другим результатом.',
}

class BotManager:
    _mtime = None             # mtime devices.json на момент последнего чтения/записи

    def __init__(self):
        self.procs = {}       # serial -> Popen
        self.log_files = {}   # serial -> file handle
        self.started = {}     # serial -> время нажатия «Старт» (сессия для статистики)
        self.history = stats.History()   # hand_history.jsonl с кэшем по mtime
        self.load_devices()
        self.resume()

    def pid_path(self, serial):
        return os.path.join(LOGS_DIR, f'{serial}.pid')

    def resume(self):
        """Восстановить привязку к уже запущенным ботам (панель перезапускалась).
        Пишется pid-файл при старте бота; если процесс жив — считаем его своим."""
        os.makedirs(LOGS_DIR, exist_ok=True)
        for d in self.devices:
            serial = d['serial']
            path = self.pid_path(serial)
            try:
                with open(path) as f:
                    pid = int(f.read().strip())
            except (OSError, ValueError):
                continue
            if self._pid_alive(pid):
                self.procs[serial] = pid
                self.log_files[serial] = open(os.path.join(LOGS_DIR, f'{serial}.log'),
                                              'a', encoding='utf-8')
                # панель перезапускалась — началом сессии считаем время, когда был
                # написан pid-файл: это и есть момент последнего «Старта»
                try:
                    self.started[serial] = os.path.getmtime(path)
                except OSError:
                    pass
                print(f'панель: бот {serial} (pid {pid}) уже запущен — подхвачен')

    @staticmethod
    def _pid_alive(pid):
        """Жив ли процесс (Windows: os.kill(pid,0) не поддерживается — WinError 87)."""
        if sys.platform == 'win32':
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                                   False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def devices_path(self):
        return DEVICES_FILE

    def load_devices(self):
        if os.path.exists(DEVICES_FILE):
            try:
                with open(DEVICES_FILE, encoding='utf-8') as f:
                    devices = json.load(f)
            except (OSError, ValueError):
                devices = None
            # список словарей с серийником — единственный формат, который панель
            # понимает; на чужом файле она не должна падать при запуске (MANAGER
            # глобальный). Не осталось ни одной записи — это тот же испорченный
            # файл: добавить устройство из панели нельзя, и без отката к
            # умолчаниям она открылась бы пустой навсегда.
            usable = [d for d in devices if isinstance(d, dict) and d.get('serial')] \
                if isinstance(devices, list) else []
            if usable:
                self.devices = usable
                self._mtime = self._file_mtime()
                return
        self.devices = [dict(d) for d in DEFAULT_DEVICES]   # копия: записи правятся на месте
        self.save_devices()

    @staticmethod
    def _file_mtime():
        try:
            return os.path.getmtime(DEVICES_FILE)
        except OSError:
            return None

    def reload_if_changed(self):
        """Перечитать devices.json, если его правил кто-то ещё.

        Этот «кто-то» — бот: он пишет в свою запись прочитанный с экрана стек.
        Без перечитывания панель показывала бы значение, снятое при её запуске, а
        первое же «Применить» вернуло бы в файл устаревшую константу.
        """
        mtime = self._file_mtime()
        if mtime is None or mtime == getattr(self, '_mtime', None):
            return False
        self.load_devices()
        return True

    def save_devices(self):
        with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.devices, f, ensure_ascii=False, indent=2)
        self._mtime = self._file_mtime()

    def device(self, serial):
        for d in self.devices:
            if d['serial'] == serial:
                return d
        return None

    def adb_online(self):
        """Серийники, которые видит adb сейчас."""
        try:
            out = subprocess.run([ADB, 'devices'], capture_output=True, text=True,
                                 timeout=15).stdout
            return [line.split()[0] for line in out.splitlines()[1:]
                    if line.strip() and 'device' in line and 'offline' not in line]
        except Exception:
            return []

    def running(self, serial):
        p = self.procs.get(serial)
        if isinstance(p, int):                 # подхваченный по pid
            return self._pid_alive(p)
        return p is not None and p.poll() is None


    def status(self, serial):
        self.reload_if_changed()      # бот мог обновить свой стек в файле
        d = self.device(serial) or {}
        online = serial in self.adb_online()
        run = self.running(serial)
        tail = self.tail(serial, 6)
        # что реально применит бот: стиль + отдельные ключи записи (без ползунков —
        # их множители применяются поверх и в поля панели попадать не должны)
        settings = strategy.device_settings(strategy.DEFAULT_SETTINGS, d, sliders=False)
        flags = {k: bool(settings[k]) for k in FLAG_KEYS}
        # переключатели бота живут прямо в записи устройства (в стратегию не идут)
        flags.update({k: bool(d.get(k, True)) for k in BOT_FLAG_KEYS})
        return {'serial': serial, 'name': d.get('name', serial),
                'online': online, 'running': run,
                'chart': d.get('chart'), 'aggression': d.get('aggression', 1.0),
                'defense': d.get('defense', 1.0), 'stack': d.get('stack', 69.6),
                # стек прочитан ботом с экрана, а не задан человеком
                'stack_auto': bool(d.get('stack_auto')),
                'style': str(d.get('style') or strategy.DEFAULT_STYLE).lower(),
                # фишек в одном ББ: панель показывает суммы в фишках
                'bb_value': stats.clean_bb_value(d.get('bb_value')),
                'flags': flags,
                'sliders': {k: settings[k] for k in SLIDER_KEYS},
                'timing': self.timing(serial),
                'opponents': self.opponents(),
                'log': tail}

    def session_start(self, serial):
        """Когда бот последний раз запускался — начало периода «За игру».

        Панель ни разу не жала «Старт» (и не подхватила pid-файл) — None: тогда
        периода «За игру» просто нет, вместо цифр честное «бот ещё не запускался».
        """
        started = self.started.get(serial)
        if not started:
            return None
        return datetime.datetime.fromtimestamp(started)

    def stats(self, serial, limit=100):
        """Статистика в фишках для вкладки «Статистика» и живой раздачи."""
        self.reload_if_changed()
        d = self.device(serial) or {}
        out = self.history.summary(bb_value=d.get('bb_value'),
                                   session_start=self.session_start(serial),
                                   limit=limit)
        out['serial'] = serial
        out['name'] = d.get('name', serial)
        out['running'] = self.running(serial)
        out['stack_bb'] = d.get('stack', 0.0)
        out['stack_chips'] = stats.to_chips(d.get('stack', 0.0), out['bb_value'])
        return out

    def timing(self, serial):
        """Диапазоны пауз этого устройства (секунды) — подписью рядом с галочкой."""
        d = self.device(serial) or {}
        out = {}
        for key, default in config.TIMING_DEFAULTS.items():
            value = d.get(key)
            try:
                out[key] = [float(value[0]), float(value[1])]
            except (TypeError, ValueError, IndexError, KeyError):
                out[key] = list(default)
        return out

    @staticmethod
    def opponents():
        """Профили из players.json — блок «Оппоненты». Файл пишет бот.

        К каждой метрике добавляется признак ready: набралось ли на неё
        наблюдений (strategy.metric_ready). Метрику без него бот не применяет, и
        в таблице она показана бледной — иначе «3-бет 0%» после сорока рук
        читался бы как вывод, а это ещё не цифра.
        """
        rows = opponents.Profiles(config.PLAYERS_FILE).opponents()
        for row in rows:
            row['ready'] = {m: strategy.metric_ready(row, m)
                            for m in strategy.PROFILE_MIN_HANDS}
        return rows

    @staticmethod
    def styles():
        """Пресеты-карточки: подпись, строчка «про что это» и пороги.

        По порогам панель освежает ползунки «Точной настройки», когда человек
        выбирает другой стиль, — иначе они остались бы от прошлого.
        """
        return {key: {'title': strategy.STYLE_TITLES.get(key, key),
                      'note': STYLE_NOTES.get(key, ''),
                      'sliders': {k: strategy.style_settings(key)[k] for k in SLIDER_KEYS}}
                for key in strategy.STYLE_PRESETS}

    def tail(self, serial, n=6):
        path = os.path.join(LOGS_DIR, f'{serial}.log')
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            return ''.join(lines[-n:])
        except OSError:
            return ''

    def start(self, serial):
        if self.running(serial):
            return False, 'уже запущен'
        d = self.device(serial)
        if d is None:
            return False, 'нет такого устройства'
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, f'{serial}.log')
        cmd = [PYTHON, os.path.join(BASE, 'main.py'), '--serial', serial]
        if d.get('chart'):
            cmd += ['--chart', os.path.join(BASE, d['chart'])]
        cmd += ['--aggression', str(d.get('aggression', 1.0))]
        cmd += ['--defense', str(d.get('defense', 1.0))]
        cmd += ['--stack', str(d.get('stack', 69.6))]
        cmd += ['--style', str(d.get('style') or strategy.DEFAULT_STYLE)]
        if not d.get('live_stack', True):
            cmd += ['--no-live-stack']
        if not d.get('opponent_memory', True):
            cmd += ['--no-memory']
        if not d.get('human_timing', True):
            cmd += ['--no-human-timing']
        if not d.get('read_nicks', True):
            cmd += ['--no-nicks']
        if d.get('tesseract'):
            cmd += ['--tesseract', str(d['tesseract'])]
        if d.get('name'):
            cmd += ['--name', d['name']]
        f = open(log_path, 'a', encoding='utf-8')
        f.write(f'\n[{time.strftime("%Y-%m-%d %H:%M:%S")}] === СТАРТ ===\n')
        f.flush()
        # Лог панель пишет и читает в utf-8, а перенаправленный stdout бот берёт
        # из локали Windows (cp1251) — русские строки приходили в файл в другой
        # кодировке, и живой лог в панели превращался в «????». Кодировку дочернего
        # процесса задаём явно; на консольный запуск бота это не влияет.
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8'}
        try:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 cwd=BASE, env=env,
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            f.close()
            return False, f'не запустился: {e}'
        self.procs[serial] = p
        self.log_files[serial] = f
        self.started[serial] = time.time()   # начало сессии для «За игру»
        with open(self.pid_path(serial), 'w') as pf:
            pf.write(str(p.pid))
        return True, 'запущен'

    def stop(self, serial):
        p = self.procs.get(serial)
        if p is None or not self.running(serial):
            return False, 'не запущен'
        try:
            if isinstance(p, int):
                os.kill(p, signal.SIGTERM)       # подхваченный по pid
            else:
                p.terminate()
                p.wait(timeout=5)
        except Exception:
            pass
        self.procs.pop(serial, None)
        f = self.log_files.pop(serial, None)
        if f:
            f.close()
        try:
            os.remove(self.pid_path(serial))
        except OSError:
            pass
        return True, 'остановлен'

    def save_config(self, serial, data):
        """Сохранить настройки устройства. Бот подхватит их со следующего решения.

        Кривые значения (чужой стиль, буквы вместо числа) молча пропускаем:
        панель не должна писать в devices.json то, на чём бот споткнётся.
        """
        self.reload_if_changed()          # бот мог обновить стек, пока правили форму
        d = self.device(serial)
        if d is None:
            d = {'serial': serial, 'name': serial}
            self.devices.append(d)
        for key in ('name', 'chart', 'aggression', 'defense', 'stack'):
            if key in data:
                d[key] = data[key]
        if 'stack' in data:
            # стек ввёл человек — с этого момента он не «авто», и бот считает от
            # него границы здравого чтения (пока сам не прочитает новое значение)
            d['stack_auto'] = False
        style = str(data.get('style', '')).lower().strip()
        if style in strategy.STYLE_PRESETS:
            d['style'] = style
        if 'bb_value' in data:
            # сколько фишек в ББ: только для показа сумм, боту это поле не нужно.
            # Кривое или запредельное значение молча пропускаем — иначе вся
            # статистика показывала бы нули или миллиарды.
            try:
                value = float(data['bb_value'])
            except (TypeError, ValueError):
                value = None
            if value is not None and stats.BB_VALUE_MIN <= value <= stats.BB_VALUE_MAX:
                d['bb_value'] = value
        for key in FLAG_KEYS + BOT_FLAG_KEYS:
            if key in data:
                d[key] = bool(data[key])
        for key in SLIDER_KEYS:
            if key in data:
                try:
                    d[key] = float(data[key])
                except (TypeError, ValueError):
                    pass
        for key in TIMING_KEYS:
            try:                       # пара [мин, макс] секунд; кривое — не пишем
                lo, hi = float(data[key][0]), float(data[key][1])
            except (TypeError, ValueError, IndexError, KeyError):
                continue
            if 0 <= lo <= hi:
                d[key] = [lo, hi]
        self.save_devices()
        return d

    def charts(self):
        charts_dir = os.path.join(BASE, 'charts')
        try:
            return sorted(f for f in os.listdir(charts_dir) if f.endswith('.json'))
        except OSError:
            return []


MANAGER = BotManager()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
PAGE = r"""<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ClubGG панель</title>
<style>
 *{box-sizing:border-box}
 body{font-family:'Segoe UI',Roboto,system-ui,Arial,sans-serif;margin:0;background:#14181c;
  color:#e8e8e8;-webkit-text-size-adjust:100%}
 .wrap{max-width:1060px;margin:0 auto;padding:18px 16px 40px}
 .top{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
 .brand{font-size:20px;font-weight:600;color:#ffd75e}
 .brand span{font-size:13px;font-weight:400;color:#6f8299;margin-left:6px}
 .devs{display:flex;gap:6px;margin-left:auto;flex-wrap:wrap}
 .devbtn{background:#1e242b;border:1px solid #2c3644;color:#9fb0c3;border-radius:8px;
  padding:5px 12px;font-size:13px;cursor:pointer}
 .devbtn.on{border-color:#ffd75e;color:#ffd75e}
 .tabs{display:flex;gap:2px;border-bottom:1px solid #2c3644;margin:16px 0 18px;overflow-x:auto}
 .tabs button{background:none;border:0;border-bottom:2px solid transparent;color:#9fb0c3;
  padding:10px 18px;font-size:15px;cursor:pointer;white-space:nowrap}
 .tabs button:hover{color:#e8e8e8}
 .tabs button.on{color:#ffd75e;border-bottom-color:#ffd75e}
 .card{background:#1e242b;border:1px solid #2c3644;border-radius:12px;padding:18px;
  margin-bottom:14px;box-shadow:0 2px 10px rgba(0,0,0,.25)}
 .card h3{font-size:15px;margin:0 0 14px;color:#ffd75e;font-weight:600}
 .card h4{font-size:13px;margin:0 0 8px;color:#9fb0c3;font-weight:600}
 h2{font-size:18px;margin:0}
 .lbl{font-size:12px;color:#6f8299;text-transform:uppercase;letter-spacing:.04em}
 .sub{font-size:12px;color:#6f8299;margin-top:4px}
 .hint{font-size:12px;color:#6f8299;margin-top:10px;line-height:1.5}
 .muted{color:#6f8299}
 .nodata{color:#6f8299;font-size:13px;padding:12px 0}
 .badge{padding:3px 10px;border-radius:20px;font-size:11px;letter-spacing:.03em}
 .badge.ok{background:#1c3a2a;color:#7fd6a0;border:1px solid #2c5a41}
 .badge.no{background:#38222a;color:#e08b8b;border:1px solid #5a2f38}
 .badge.play{background:#3a3418;color:#ffd75e;border:1px solid #5c5222}
 .badge.idle{background:#242b34;color:#8394a8;border:1px solid #2c3644}
 .up{color:#63d68a}.down{color:#f07a63}
 /* ---- вкладка «Игра» ---- */
 .hero-head{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:16px}
 .hero-grid{display:grid;grid-template-columns:1.1fr 1.3fr 1fr;gap:20px;align-items:start}
 .big{font-size:38px;font-weight:600;line-height:1.1;margin-top:4px}
 .coin{font-size:22px;margin-left:6px}
 .mid{font-size:21px;font-weight:600;margin-top:2px}
 .chart{width:100%;height:auto;display:block}
 .chart .area{fill:rgba(255,215,94,.09);stroke:none}
 .chart .line{fill:none;stroke:#ffd75e;stroke-width:2;stroke-linejoin:round}
 .chart .d-win{fill:#63d68a}.chart .d-loss{fill:#f07a63}
 .legend{font-size:12px;color:#6f8299;margin-top:8px;display:flex;gap:8px;align-items:center}
 .legend .k{width:9px;height:9px;border-radius:50%;display:inline-block;margin-left:10px}
 .legend .k.win{background:#63d68a}.legend .k.loss{background:#f07a63}
 .live{background:#171d23;border:1px solid #2c3644;border-radius:10px;padding:14px;margin:18px 0}
 .live.empty{color:#6f8299;font-size:13px}
 .live-head{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;font-size:13px}
 .pill{background:#242b34;border:1px solid #2c3644;border-radius:20px;padding:2px 10px;
  font-size:11px;color:#9fb0c3}
 .live-head .ts{margin-left:auto;color:#5b6a7d;font-size:11px}
 .live-cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:14px}
 .cardrow{margin-top:6px;min-height:56px;display:flex;align-items:center}
 .pc{display:inline-flex;flex-direction:column;align-items:center;justify-content:center;
  width:40px;height:56px;background:#f6f4ee;border-radius:6px;margin-right:6px;line-height:1;
  box-shadow:0 2px 6px rgba(0,0,0,.45)}
 .pc b{font-size:19px}.pc i{font-size:17px;font-style:normal;margin-top:3px}
 .pc.blk{color:#14181c}.pc.red{color:#c62b2b}
 .decision{margin-top:14px;padding:12px 14px;border-radius:9px;background:#242b34;
  border-left:3px solid #3a4a5c;display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}
 .decision.raise{border-left-color:#63d68a}
 .decision.call{border-left-color:#ffd75e}
 .decision.fold{border-left-color:#f07a63}
 .decision .dec{font-size:17px;font-weight:700;letter-spacing:.04em}
 .decision .amt{font-size:15px;color:#ffd75e}
 .decision .why{font-size:13px;color:#9fb0c3;flex:1 1 240px}
 .acts{display:flex;gap:12px;flex-wrap:wrap;margin-top:18px}
 button{background:#2c6fbb;color:#fff;border:0;border-radius:8px;padding:8px 16px;
  cursor:pointer;font-size:13px;font-family:inherit}
 button:hover{filter:brightness(1.12)}
 button:disabled{opacity:.35;cursor:default;filter:none}
 .big-btn{font-size:16px;font-weight:600;padding:13px 30px;border-radius:10px}
 .go{background:#1f7a44}.stop{background:#a8392a}
 .save{background:#2c6fbb;font-size:15px;font-weight:600;padding:11px 24px}
 .logbox summary{cursor:pointer;font-size:14px;color:#ffd75e;font-weight:600}
 .logbox pre{background:#0c1014;border:1px solid #2c3644;border-radius:8px;padding:10px;
  font-size:12px;max-height:220px;overflow:auto;white-space:pre-wrap;color:#9fe8a0;margin:12px 0 0}
 /* ---- вкладка «Настройки» ---- */
 .styles{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
 .style{background:#171d23;border:1px solid #2c3644;border-radius:10px;padding:12px 14px;
  text-align:left;color:#e8e8e8;display:block}
 .style b{display:block;font-size:14px;margin-bottom:4px}
 .style span{font-size:12px;color:#6f8299;line-height:1.4}
 .style.sel{border-color:#ffd75e;background:#232a22}
 .style.sel b{color:#ffd75e}
 .fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:14px;margin-top:18px}
 .f label{font-size:12px;color:#9fb0c3;display:flex;align-items:center;gap:5px;margin-bottom:6px}
 select,input[type=number]{background:#0f1419;color:#e8e8e8;border:1px solid #3a4a5c;
  border-radius:7px;padding:7px 9px;font-size:13px;font-family:inherit;max-width:100%}
 .f input[type=number]{width:110px}
 .sl{display:flex;gap:10px;align-items:center}
 input[type=range]{flex:1;min-width:90px;accent-color:#ffd75e}
 .val{font-size:12px;color:#ffd75e;min-width:36px;text-align:right}
 .grp{margin-bottom:16px}
 .flags{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:8px 18px}
 .fl{display:flex;gap:8px;align-items:center;font-size:13px;cursor:pointer}
 .fl input{accent-color:#ffd75e;width:16px;height:16px}
 .grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:10px 24px}
 .cell{display:flex;gap:10px;align-items:center}
 .cell label{flex:0 0 150px;font-size:13px;color:#9fb0c3;display:flex;align-items:center;gap:5px}
 .savebar{position:sticky;bottom:0;background:#14181cf0;padding:12px 0;display:flex;
  gap:14px;align-items:center;border-top:1px solid #2c3644}
 .state{font-size:12px;color:#6f8299}
 .state.dirty{color:#ffd75e}
 /* ---- вкладка «Оппоненты» ---- */
 table{border-collapse:collapse;font-size:13px;width:100%}
 th,td{border-bottom:1px solid #232b34;padding:8px 10px;text-align:right}
 th{color:#9fb0c3;font-weight:500;font-size:12px}
 th:first-child,td:first-child{text-align:left}
 td.raw{color:#4f5d6e}
 .tablewrap{overflow-x:auto}
 /* ---- вкладка «Статистика» ---- */
 .periods{display:grid;grid-template-columns:repeat(auto-fit,minmax(205px,1fr));gap:12px}
 .pcard{background:#171d23;border:1px solid #2c3644;border-radius:10px;padding:14px}
 .ptitle{font-size:13px;font-weight:600;color:#e8e8e8}
 .pnote{display:block;font-size:11px;color:#5b6a7d;font-weight:400;margin-top:2px}
 .pl{font-size:26px;font-weight:600;margin:10px 0 8px}
 .prow{font-size:12px;color:#9fb0c3;margin-top:4px}
 .bbline{color:#4f5d6e;font-size:11px;margin-top:8px}
 /* ---- подсказки «?» ---- */
 .tip{position:relative;display:inline-block;width:15px;height:15px;line-height:15px;
  text-align:center;font-size:10px;font-weight:bold;border-radius:50%;background:#3a4a5c;
  color:#c8d8e8;cursor:help;flex:none}
 .tip .tt{display:none;position:absolute;bottom:150%;left:50%;transform:translateX(-50%);
  width:270px;background:#0c1014;border:1px solid #3a4a5c;border-radius:8px;padding:9px 11px;
  font-size:12px;line-height:1.45;color:#e8e8e8;z-index:50;white-space:normal;text-align:left;
  font-weight:400;text-transform:none;letter-spacing:0;box-shadow:0 6px 18px rgba(0,0,0,.6)}
 .tip:hover .tt,.tip:focus .tt{display:block}
 .foot{font-size:12px;color:#4f5d6e;margin-top:20px;line-height:1.5}
 @media(max-width:760px){
  .wrap{padding:14px 12px 30px}
  .hero-grid{grid-template-columns:1fr;gap:16px}
  .big{font-size:32px}
  .cell{flex-wrap:wrap}
  .cell label{flex:1 0 100%}
  .tabs button{padding:10px 12px;font-size:14px}
  .tip .tt{width:210px}
  .big-btn{flex:1;padding:14px 10px}
 }
</style></head><body>
<div class="wrap">
 <header class="top">
  <div class="brand">🎰 ClubGG<span>панель управления ботами</span></div>
  <div class="devs" id="devs"></div>
 </header>
 <nav class="tabs" id="tabs">
  <button data-tab="game" class="on">Игра</button>
  <button data-tab="setup">Настройки</button>
  <button data-tab="opps">Оппоненты</button>
  <button data-tab="stats">Статистика</button>
 </nav>
 <main id="view"><div class="card nodata">Загружаю…</div></main>
 <footer class="foot">Обновление каждые 3 секунды. «Применить» действует сразу —
  бот перечитывает настройки перед каждым решением, перезапускать его не нужно.</footer>
</div>
<script>
const T = {tab:'game', serial:null, data:null, stats:null, dirty:false, log:false, force:false};

async function api(path, method, body){
  const r = await fetch(path, {method: method||'GET',
    headers:{'Content-Type':'application/json'},
    body: body?JSON.stringify(body):undefined});
  return r.json();
}
function esc(s){ return (s==null?'':String(s))
  .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function num(n){ return Math.round(n||0).toLocaleString('ru-RU'); }
function sgn(n){ return (n>0?'+':'') + num(n); }
function pct(x){ return Math.round((x||0)*100)+'%'; }
function cls(v){ return v>0?'up':v<0?'down':''; }
function devs(){ return (T.data&&T.data.devices)||[]; }
function dev(){ return devs().find(d=>d.serial===T.serial)||devs()[0]||null; }
function bbv(){ const d=dev(); return (d&&d.bb_value)||20; }
// значок «?» с объяснением простыми словами (словарь приходит с сервера)
function tip(k){ const t=((T.data||{}).tips||{})[k];
  return t?'<span class="tip" tabindex="0">?<span class="tt">'+esc(t)+'</span></span>':''; }
// метрика, по которой ещё мало наблюдений: бот её не применяет — показываем бледной
function raw(o,m){ return ((o.ready||{})[m])?'':
  ' class="raw" title="наблюдений мало — метрика не применяется"'; }
function plural(n,f){ const a=n%10,b=n%100;
  return (a===1&&b!==11)?f[0]:(a>=2&&a<=4&&(b<10||b>=20))?f[1]:f[2]; }

// ---- карты с цветными мастями ----
const SUITS={s:['♠','blk'],h:['♥','red'],d:['♦','red'],c:['♣','blk']};
function card(c){
  c = String(c||'');
  if(c.length<2) return '';
  const s = SUITS[c.slice(1,2).toLowerCase()];
  if(!s) return '';
  const r = c[0].toUpperCase()==='T'?'10':c[0].toUpperCase();
  return '<span class="pc '+s[1]+'"><b>'+r+'</b><i>'+s[0]+'</i></span>';
}
function cards(list){
  const html=(list||[]).map(card).filter(Boolean).join('');
  return html||'<span class="muted">—</span>';
}
const STREET={preflop:'до флопа',flop:'флоп',turn:'тёрн',river:'ривер'};
const ACTION={raise:'СТАВКА',call:'КОЛЛ',check:'ЧЕК',fold:'ФОЛД',allin:'ВА-БАНК'};

// ---- график стека (SVG руками: библиотек в панели нет) ----
function chartSvg(rows,w,h,dots){
  rows = rows||[];
  if(!rows.length) return '<div class="nodata">Пока нет раздач — график появится после первой.</div>';
  const vals = rows.map(r=>r.stack||0);
  let lo=Math.min.apply(null,vals), hi=Math.max.apply(null,vals);
  const span=(hi-lo)||Math.max(1,Math.abs(hi)*0.1);
  lo-=span*0.15; hi+=span*0.15;
  const px=i=>rows.length<2?w/2:6+i*(w-12)/(rows.length-1);
  const py=v=>h-8-((v-lo)/(hi-lo))*(h-16);
  const line=rows.map((r,i)=>(i?'L':'M')+px(i).toFixed(1)+','+py(r.stack||0).toFixed(1)).join(' ');
  const area=line+' L'+px(rows.length-1).toFixed(1)+','+h+' L'+px(0).toFixed(1)+','+h+' Z';
  const pts=!dots?'':rows.map((r,i)=>(r.result==='win'||r.result==='loss')
    ?'<circle cx="'+px(i).toFixed(1)+'" cy="'+py(r.stack||0).toFixed(1)+'" r="3.2" class="d-'+r.result+'">'
      +'<title>раздача '+esc(r.hand_id)+': '+(r.delta>0?'+':'')+num(r.delta)+' фишек</title></circle>':'').join('');
  return '<svg class="chart" viewBox="0 0 '+w+' '+h+'" width="'+w+'" height="'+h+'">'
    +'<path d="'+area+'" class="area"/><path d="'+line+'" class="line"/>'+pts+'</svg>';
}

// ---- вкладка «Игра» ----
function viewGame(){
  const d=dev(); if(!d) return '<div class="card nodata">Устройств нет — проверьте devices.json.</div>';
  const s=T.stats||{}, bb=bbv();
  const ses=(s.periods||[]).find(p=>p.key==='session');
  return `
  <section class="card">
   <div class="hero-head"><h2>${esc(d.name)||esc(d.serial)}</h2>
    <span class="badge ${d.online?'ok':'no'}">${d.online?'в сети':'нет связи'}</span>
    <span class="badge ${d.running?'play':'idle'}">${d.running?'ИГРАЕТ':'остановлен'}</span></div>
   <div class="hero-grid">
    <div><div class="lbl">Стек</div>
     <div class="big">${num(d.stack*bb)}<span class="coin">🪙</span></div>
     <div class="sub">${d.stack} ББ · ${d.stack_auto?'бот читает с экрана':'задан вручную'}</div></div>
    <div><div class="lbl">Стек по раздачам</div>
     ${chartSvg((s.chart||[]).slice(-40),300,72,false)}</div>
    <div><div class="lbl">За игру ${tip('stats')}</div>
     ${(ses&&!ses.unknown)
      ? `<div class="big ${cls(ses.pl_chips)}">${sgn(ses.pl_chips)}<span class="coin">🪙</span></div>
         <div class="sub">${ses.hands} ${plural(ses.hands,['раздача','раздачи','раздач'])} ·
          <b class="up">${ses.wins}</b> побед / <b class="down">${ses.losses}</b> поражений</div>`
      : `<div class="big muted">—</div><div class="sub">бот ещё не запускался</div>`}</div>
   </div>
   ${liveBlock(s.live)}
   <div class="acts">
    <button class="big-btn go" data-a="start" ${d.running?'disabled':''}>▶ Старт</button>
    <button class="big-btn stop" data-a="stop" ${d.running?'':'disabled'}>■ Стоп</button>
   </div>
  </section>
  <details class="card logbox" id="logbox" ${T.log?'open':''}>
   <summary>Лог бота — что он делает прямо сейчас</summary>
   <pre>${esc(d.log)||'пока пусто'}</pre></details>`;
}
function liveBlock(h){
  if(!h) return `<div class="live empty">Живая раздача появится здесь, как только бот
    примет первое решение.</div>`;
  const decision=ACTION[h.action]||String(h.action||'').toUpperCase();
  return `<div class="live">
   <div class="live-head"><b>Раздача №${esc(h.hand_id)}</b>
    <span class="pill">${esc(STREET[h.street]||h.street||'')}</span>
    ${h.position?`<span class="pill">место ${esc(h.position)}</span>`:''}
    ${h.players?`<span class="pill">${esc(h.players)} в игре</span>`:''}
    <span class="ts">${esc(h.ts||'')}</span></div>
   <div class="live-cards">
    <div><div class="lbl">Наши карты</div><div class="cardrow">${cards(h.hole)}</div></div>
    <div><div class="lbl">Доска</div><div class="cardrow">${cards(h.board)}</div></div>
    <div><div class="lbl">Банк</div><div class="mid">${num(h.pot_chips)}<span class="coin">🪙</span></div></div>
    <div><div class="lbl">Наш стек</div><div class="mid">${num(h.stack_chips)}<span class="coin">🪙</span></div></div>
   </div>
   <div class="decision ${esc(h.action)}"><span class="dec">${esc(decision)}</span>
    ${h.amount_chips?`<span class="amt">${num(h.amount_chips)} 🪙</span>`:''}
    <span class="why">${esc(h.reason||'')}</span></div>
   ${h.made_note?`<div class="sub">${esc(h.made_note)}</div>`:''}
  </div>`;
}

// ---- вкладка «Настройки» ----
function viewSetup(){
  const d=dev(), data=T.data||{}; if(!d) return '';
  const titles={}; (data.flags||[]).forEach(f=>titles[f[0]]=f[1]);
  return `
  <section class="card"><h3>Характер игры</h3>
   <div class="styles">${Object.keys(data.styles||{}).map(k=>{
     const s=data.styles[k];
     return `<button class="style ${d.style===k?'sel':''}" data-style="${esc(k)}">
       <b>${esc(s.title)}</b><span>${esc(s.note||'')}</span></button>`;}).join('')}</div>
   <input type="hidden" data-k="style" value="${esc(d.style)}">
   <div class="fields">
    <div class="f"><label>Чарт ${tip('chart')}</label>
     <select data-k="chart">${(data.charts||[]).map(c=>
       `<option ${d.chart==='charts/'+c?'selected':''}>charts/${esc(c)}</option>`).join('')}</select></div>
    <div class="f"><label>Стек, ББ ${tip('stack')}</label>
     <input type="number" data-k="stack" step="0.1" value="${d.stack}">
     <div class="sub">${d.stack_auto?'бот читает стек с экрана':'задан вручную'}</div></div>
    <div class="f"><label>Агрессия ${tip('aggression')}</label>
     <div class="sl"><input type="range" data-k="aggression" min="0.5" max="2" step="0.1"
      value="${d.aggression}"><span class="val">${d.aggression}</span></div></div>
    <div class="f"><label>Защита ${tip('defense')}</label>
     <div class="sl"><input type="range" data-k="defense" min="0.5" max="2" step="0.1"
      value="${d.defense}"><span class="val">${d.defense}</span></div></div>
   </div>
  </section>
  <section class="card"><h3>Переключатели</h3>
   ${(data.groups||[]).map(g=>`<div class="grp"><h4>${esc(g[0])}</h4>
    <div class="flags">${g[1].filter(k=>k in (d.flags||{})).map(k=>
      `<label class="fl"><input type="checkbox" data-k="${esc(k)}" ${d.flags[k]?'checked':''}>
       <span>${esc(titles[k]||k)}</span>${tip(k)}</label>`).join('')}</div></div>`).join('')}
  </section>
  <section class="card"><h3>Точная настройка</h3>
   <div class="grid2">${(data.sliders||[]).map(sl=>
     `<div class="cell"><label>${esc(sl[1])}${tip(sl[0])}</label>
      <div class="sl"><input type="range" data-k="${esc(sl[0])}" min="${sl[2]}" max="${sl[3]}"
       step="${sl[4]}" value="${d.sliders[sl[0]]}"><span class="val">${d.sliders[sl[0]]}</span></div>
     </div>`).join('')}</div>
   <div class="hint">Паузы перед ходом ${tip('timing')}: ${Object.keys(d.timing||{}).map(k=>
     `${(T.data.timings||{})[k]||k} ${d.timing[k][0]}–${d.timing[k][1]}с`).join(' · ')}
    (плюс небольшой разброс, не больше 5с на ход и только когда есть запас времени;
    диапазоны правятся в devices.json)</div>
  </section>
  <div class="savebar"><button class="save" data-a="save">💾 Применить</button>
   <span class="state">применяется сразу, без перезапуска</span></div>`;
}

// ---- вкладка «Оппоненты» ----
function viewOpps(){
  const d=dev(); const rows=(d&&d.opponents)||[];
  if(!rows.length) return `<section class="card"><h3>Оппоненты</h3>
    <div class="nodata">Пока пусто — профили появятся после первых раздач.</div></section>`;
  return `<section class="card"><h3>Оппоненты — что бот о них знает</h3>
   <div class="tablewrap"><table>
    <tr><th>Имя</th><th>Рук</th><th>VPIP ${tip('vpip')}</th><th>PFR ${tip('pfr')}</th>
     <th>3-бет ${tip('three_bet')}</th><th>Agg ${tip('agg')}</th></tr>
    ${rows.map(o=>`<tr><td>${esc(o.name)}</td><td>${o.hands}</td>
     <td${raw(o,'vpip')}>${pct(o.vpip)}</td><td${raw(o,'pfr')}>${pct(o.pfr)}</td>
     <td${raw(o,'three_bet')}>${pct(o.three_bet)}</td>
     <td${raw(o,'agg')}>${(o.agg||0).toFixed(1)}</td></tr>`).join('')}
   </table></div>
   <div class="hint">Бледные цифры бот пока не применяет: наблюдений на эту метрику
    ещё мало. Сколько именно нужно — настраивается на вкладке «Настройки»,
    блок «Точная настройка».</div></section>`;
}

// ---- вкладка «Статистика» ----
function viewStats(){
  const s=T.stats||{};
  return `
  <section class="card"><h3>Итоги в фишках ${tip('stats')}</h3>
   <div class="periods">${(s.periods||[]).map(periodCard).join('')}</div>
   <div class="hint">Всего раздач в истории: ${s.hands_total||0}, из них с известным
    результатом ${s.hands_counted||0}. У последней раздачи результата ещё нет —
    он станет виден, когда начнётся следующая.</div>
  </section>
  <section class="card"><h3>Стек по раздачам (последние ${(s.chart||[]).length})</h3>
   ${chartSvg(s.chart||[],900,220,true)}
   <div class="legend">точками отмечены<span class="k win"></span>победы
    <span class="k loss"></span>поражения</div>
  </section>
  <section class="card"><h3>Сколько фишек в одной большой ставке ${tip('bb_value')}</h3>
   <div class="fields"><div class="f"><label>1 ББ = сколько фишек</label>
    <div class="sl"><input type="number" data-k="bb_value" min="0.01" step="1"
      value="${s.bb_value||20}"><button class="save" data-a="save">💾 Сохранить</button></div>
   </div></div>
   <div class="hint">Бот считает в больших ставках (ББ), а показывать удобнее фишки.
    Стол на 1000 фишек с блайндами 10/20 — это 20 фишек в ББ, то есть 50 ББ стека.</div>
  </section>`;
}
function periodCard(p){
  const st=p.streak||{};
  const body = p.unknown
   ? `<div class="pl muted">—</div><div class="prow">бот ещё не запускался из панели</div>`
   : `<div class="pl ${cls(p.pl_chips)}">${sgn(p.pl_chips)}<span class="coin">🪙</span></div>
      <div class="prow">Раздач <b>${p.hands}</b>${p.folded?` (сыграно ${p.played} · не сыграно ${p.folded})`:''}</div>
      <div class="prow"><b class="up">${p.wins}</b> побед (${p.win_pct}%) ·
       <b class="down">${p.losses}</b> поражений (${p.loss_pct}%)</div>
      <div class="prow">${streakText(st)}</div>
      <div class="prow bbline">${p.pl_bb>0?'+':''}${p.pl_bb} ББ${p.folded?
        ` (сыгранных ${p.pl_played_bb>0?'+':''}${p.pl_played_bb})`:''}</div>`;
  return `<div class="pcard"><div class="ptitle">${esc(p.title)}
    <span class="pnote">${esc(p.note||'')}</span></div>${body}</div>`;
}
function streakText(st){
  if(!st.count) return 'Серия ' + tip('streak') + ': <b>—</b>';
  const win = st.kind==='win';
  const word = plural(st.count, win?['победа','победы','побед']
                                   :['поражение','поражения','поражений']);
  return 'Серия ' + tip('streak') + ': <b class="'+(win?'up':'down')+'">'
    + st.count + ' ' + word + ' подряд</b>';
}

// ---- отрисовка и события ----
function render(){
  if(!T.data) return;
  const view=document.getElementById('view');
  // вкладку настроек НЕ перерисовываем на автообновлении: стёрлись бы правки,
  // которые человек ещё не применил
  if(T.tab==='setup' && view.dataset.tab==='setup' && !T.force) return;
  T.force=false;
  view.dataset.tab=T.tab;
  view.innerHTML = T.tab==='game'?viewGame():T.tab==='setup'?viewSetup()
                 : T.tab==='opps'?viewOpps():viewStats();
  bind(view);
  document.querySelectorAll('#tabs button').forEach(b=>
    b.classList.toggle('on', b.dataset.tab===T.tab));
  renderDevs();
}
function renderDevs(){
  const el=document.getElementById('devs'), list=devs();
  el.innerHTML = list.length<2 ? '' : list.map(d=>
    `<button class="devbtn ${d.serial===T.serial?'on':''}" data-s="${esc(d.serial)}">
      ${esc(d.name)||esc(d.serial)}</button>`).join('');
  el.querySelectorAll('.devbtn').forEach(b=>b.onclick=()=>{
    T.serial=b.dataset.s; T.force=true; load(); });
}
function bind(view){
  const serial=T.serial, state=view.querySelector('.state');
  const touch=()=>{ T.dirty=true; if(state){ state.textContent='не сохранено — нажмите «Применить»';
    state.classList.add('dirty'); } };
  view.querySelectorAll('input[type=range]').forEach(r=>{
    r.oninput=()=>{ r.nextElementSibling.textContent=r.value; touch(); }; });
  view.querySelectorAll('input[type=checkbox],input[type=number],select')
      .forEach(i=>i.onchange=touch);
  view.querySelectorAll('.style').forEach(b=>{ b.onclick=()=>{
    view.querySelectorAll('.style').forEach(x=>x.classList.remove('sel'));
    b.classList.add('sel');
    view.querySelector('[data-k=style]').value=b.dataset.style;
    // стиль перезаписывает связанные с ним пороги — как в старой панели
    const preset=((T.data.styles||{})[b.dataset.style]||{}).sliders||{};
    view.querySelectorAll('.grid2 input[type=range]').forEach(r=>{
      if(preset[r.dataset.k]!==undefined){
        r.value=preset[r.dataset.k]; r.nextElementSibling.textContent=r.value; }});
    touch(); }; });
  const box=view.querySelector('#logbox');
  if(box) box.ontoggle=()=>{ T.log=box.open; };
  view.querySelectorAll('[data-a]').forEach(b=>{ b.onclick=async()=>{
    b.disabled=true;
    if(b.dataset.a==='save'){
      const cfg={serial};
      view.querySelectorAll('[data-k]').forEach(i=>{
        cfg[i.dataset.k] = i.type==='checkbox' ? i.checked
                         : (i.tagName==='SELECT'||i.type==='hidden') ? i.value
                         : parseFloat(i.value); });
      await api('/api/device/'+serial+'/config','POST',cfg);
    } else {
      await api('/api/device/'+serial+'/'+b.dataset.a,'POST',{});
    }
    T.dirty=false; T.force=true; await load(); }; });
}
async function load(){
  try{
    T.data = await api('/api/devices');
    const list = T.data.devices||[];
    if(!T.serial || !list.some(d=>d.serial===T.serial))
      T.serial = (list[0]||{}).serial||null;
    if(T.serial) T.stats = await api('/api/stats?serial='+encodeURIComponent(T.serial));
  }catch(e){ return; }        // панель недоступна — просто ждём следующего опроса
  render();
}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>{
  if(T.dirty && T.tab==='setup' &&
     !confirm('Настройки изменены, но не применены. Уйти со вкладки?')) return;
  T.dirty=false; T.tab=b.dataset.tab; T.force=true; render();
});
setInterval(load, 3000);
load();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text') else ''))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ('/', '/index.html'):
            self._send(200, PAGE, 'text/html')
            return
        if u.path == '/api/devices':
            MANAGER.reload_if_changed()      # бот пишет сюда живой стек
            devices = [MANAGER.status(d['serial']) for d in MANAGER.devices]
            self._send(200, {'devices': devices, 'charts': MANAGER.charts(),
                             'styles': MANAGER.styles(), 'flags': FLAGS + BOT_FLAGS,
                             'sliders': SLIDERS, 'timings': TIMING_TITLES,
                             'groups': FLAG_GROUPS,
                             'tips': TIPS, 'total': len(devices)})
            return
        if u.path == '/api/stats':
            q = parse_qs(u.query)
            serial = (q.get('serial') or [''])[0]
            if not serial and MANAGER.devices:
                serial = MANAGER.devices[0]['serial']
            try:
                limit = max(0, min(1000, int((q.get('limit') or ['100'])[0])))
            except ValueError:
                limit = 100
            self._send(200, MANAGER.stats(serial, limit=limit))
            return
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        u = urlparse(self.path)
        parts = u.path.strip('/').split('/')
        # /api/device/<serial>/start|stop|config
        if len(parts) == 4 and parts[0] == 'api' and parts[1] == 'device':
            serial, action = parts[2], parts[3]
            length = int(self.headers.get('Content-Length') or 0)
            data = {}
            if length:
                raw = self.rfile.read(length)
                for enc in ('utf-8', 'cp1251', 'cp866'):
                    try:
                        data = json.loads(raw.decode(enc))
                        break
                    except (ValueError, UnicodeDecodeError):
                        continue
            if action == 'start':
                ok, msg = MANAGER.start(serial)
                self._send(200, {'ok': ok, 'msg': msg})
                return
            if action == 'stop':
                ok, msg = MANAGER.stop(serial)
                self._send(200, {'ok': ok, 'msg': msg})
                return
            if action == 'config':
                d = MANAGER.save_config(serial, data)
                self._send(200, {'ok': True, 'device': d})
                return
        self._send(404, {'error': 'not found'})


class PanelServer(ThreadingHTTPServer):
    """Панель на порту одна.

    На Windows SO_REUSEADDR разрешает ВТОРОМУ сокету сесть на уже занятый порт:
    вторая панель молча запускалась, запросы начинали уходить то в неё, то в
    старую, а pid-файлы ботов держали обе. allow_reuse_address там выключаем —
    вторая панель честно скажет «порт занят». На посиксе он нужен, чтобы панель
    перезапускалась сразу после Ctrl+C, а второй bind там и так не проходит.
    """

    allow_reuse_address = sys.platform != 'win32'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Веб-панель ClubGG')
    ap.add_argument('--port', type=int, default=8090)
    args = ap.parse_args(argv)
    try:
        server = PanelServer(('127.0.0.1', args.port), Handler)
    except OSError as e:
        print(f'ERR: порт {args.port} занят — панель уже запущена? ({e})')
        return 1
    print(f'Панель: http://127.0.0.1:{args.port}  (Ctrl+C — выход)')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
