#!/usr/bin/env python3
"""Автономный покерный бот ClubGG: скриншот -> состояние -> решение -> тап.

В игровом цикле НЕТ ИИ: карты распознаются шаблонами (card_reader), состояние
стола читается локально (table_state), решение принимается по таблицам (strategy).

Запуск на компе (Windows):
    python main.py                 # играть
    python main.py --dry-run       # всё считать и логировать, но НЕ тапать
    python main.py --once          # один проход (проверка)
    python main.py --max-actions 20                # сыграть 20 решений и выйти
    python main.py --image shots/turn_191709.png   # разбор кадра из файла
    python main.py --chart charts/6max_tight.json  # играть по загруженному чарту

Лог решений: bot.log, история раздач: hand_history.jsonl
"""
import argparse
import json
import os
import random
import subprocess
import sys
import time

import cv2
import numpy as np

import config
import nick_reader
import opponents
import table_state as ts
import strategy

# масти для живого лога: 'Ah' -> 'A♥'
SUIT_SIGNS = {'s': '♠', 'h': '♥', 'd': '♦', 'c': '♣'}
# действие -> диапазон задержки в devices.json (см. config.TIMING_DEFAULTS)
TIMING_KEYS = {'raise': 'timing_raise', 'call': 'timing_call',
               'check': 'timing_fold', 'fold': 'timing_fold'}
MADE_TITLES = {'preflop': 'префлоп', 'unknown': 'не разобрана'}


def timing_ranges(cfg):
    """Диапазоны «раздумья» из записи устройства: {'timing_raise': (lo, hi), ...}.

    Кривое значение (не пара чисел, перепутанные границы) молча заменяется
    значением по умолчанию: из-за опечатки в панели бот думать вечность не должен.
    """
    out = {}
    for key, default in config.TIMING_DEFAULTS.items():
        try:
            lo, hi = float((cfg or {})[key][0]), float((cfg or {})[key][1])
        except (TypeError, ValueError, IndexError, KeyError):
            lo, hi = default
        out[key] = (lo, hi) if 0 <= lo <= hi else tuple(default)
    return out


def pretty_cards(cards):
    """['Ah','Kd'] -> 'A♥K♦'. Нераспознанная карта — «??»."""
    out = []
    for c in cards or []:
        if not c or len(c) < 2:
            out.append('??')
        else:
            out.append(c[0].upper() + SUIT_SIGNS.get(c[1].lower(), c[1]))
    return ''.join(out)


# --------------------------------------------------------------------------
# источники кадров
# --------------------------------------------------------------------------
class AdbScreen:
    """Телефон через adb. Скриншот только через subprocess (в git-bash бинарь портится)."""

    def __init__(self, adb=None, serial=None):
        self.adb = adb or config.ADB
        self.serial = serial or config.SERIAL

    def grab(self):
        try:
            p = subprocess.run([self.adb, '-s', self.serial, 'exec-out', 'screencap', '-p'],
                               capture_output=True, timeout=20)
        except FileNotFoundError:
            raise SystemExit(f'ERR: adb не найден: {self.adb}\n'
                             'путь задаётся переменной CLUBGG_ADB или ключом --adb')
        if len(p.stdout) < 1000:
            return None
        buf = np.frombuffer(p.stdout, np.uint8)
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)

    def tap(self, x, y):
        subprocess.run([self.adb, '-s', self.serial, 'shell', 'input', 'tap',
                        str(int(x)), str(int(y))], check=False, timeout=20)


class FileScreen:
    """Один кадр из файла — для проверки без телефона."""

    def __init__(self, path):
        self.img = cv2.imread(path)
        if self.img is None:
            raise SystemExit(f'ERR: не читается {path}')
        self.taps = []

    def grab(self):
        return self.img

    def tap(self, x, y):
        self.taps.append((int(x), int(y)))


# --------------------------------------------------------------------------
# бот
# --------------------------------------------------------------------------
class Bot:
    # раскрытие свёрнутого столбца ставки (двухшаговый тап)
    EXPAND_WAIT = 0.7        # сколько ждать перерисовки после тапа шеврона, с
    EXPAND_TRIES = 2         # столько кадров перечитываем (тап при этом ОДИН)
    PRESET_FRAC_TOL = 0.2    # ближе этого к нужной доле банка — размер устраивает
    STACK_SANE_MULT = 10.0   # стек с экрана дальше 10x начального — не верим
    STACK_EPS = 0.05         # мельче — то же значение (не пишем файл и не логируем)

    def __init__(self, screen, dry_run=False, stack_bb=69.6, players_db=None,
                 tpl_dir=None, log_path=None, history_path=None, chart=None,
                 serial=None, devices_path=None, cfg=None, players_path=None):
        self.screen = screen
        self.dry_run = dry_run
        self.stack_bb = stack_bb
        # стек, от которого считается «здравый» диапазон живого чтения: значение
        # с экрана дальше STACK_SANE_MULT от него — почти наверняка мусор
        self.start_stack_bb = stack_bb
        self.live_stack = True          # читать свой стек с экрана (флаг live_stack)
        self.stack_auto = False         # текущее значение прочитано ботом, а не панелью
        self.players_db = players_db if players_db is not None else {}
        # профили правятся прямо в players_db, поэтому opponent_profile() видит
        # свежие цифры сразу после записи раздачи
        self.profiles = opponents.Profiles(players_path or config.PLAYERS_FILE,
                                           db=self.players_db)
        self.observer = opponents.HandObserver()
        self.opponent_memory = True     # флаг opponent_memory (панель)
        self.human_timing = True        # флаг human_timing (панель)
        self.read_nicks = True          # флаг read_nicks: ники с экрана (панель)
        self.tesseract = config.TESSERACT
        self.nicks = {}                 # место по кругу -> ник этой раздачи (кэш)
        self._nick_seen = {}            # что уже писали в лог по каждому месту
        self.timing = dict(config.TIMING_DEFAULTS)
        self.style = strategy.DEFAULT_STYLE
        self._turn_sig = None           # сигнатура хода, который сейчас на экране
        self._turn_seen = None          # когда мы увидели его впервые (запас до таймаута)
        # копия: настройки меняются на лету (панель), чарт по умолчанию — общий на процесс
        self.chart = (chart or strategy.active_chart()).copy()
        # база, поверх которой каждый раз собираются стиль и ползунки: применять
        # их к уже применённым настройкам нельзя — множители «уползали» бы
        self.base_settings = dict(self.chart.settings)
        self.serial = serial
        self.devices_path = devices_path or config.DEVICES_FILE
        self.cli_cfg = dict(cfg or {})       # настройки из ключей запуска (запасные)
        self._cfg_mtime = None
        self._blocker_spots = 0              # счётчик ситуаций для блефа с блокером
        self._bluff_key = self._bluff_last = None
        self.apply_config(self.cli_cfg, quiet=True)
        self.tpl_dir = tpl_dir
        self.log_path = log_path or config.LOG_FILE
        self.history_path = history_path or config.HAND_HISTORY
        self.hand_id = 0
        self.last_hole = None
        self.last_action_ts = 0.0
        self.last_state = None
        self.stable = 0
        self._retries = 0
        self._last_action = None
        self.actions = 0
        self.save_frames = isinstance(screen, AdbScreen)
        self.stats = {'fold': 0, 'check': 0, 'call': 0, 'raise': 0}
        self.started = time.time()

    def _save_frame(self, img, tag):
        """Кадр решения/промаха распознавания — для диагностики на компе."""
        if not self.save_frames or img is None:
            return
        try:
            os.makedirs(config.SHOTS_LIVE, exist_ok=True)
            name = f"{time.strftime('%Y%m%d_%H%M%S')}_{tag}.png"
            cv2.imwrite(os.path.join(config.SHOTS_LIVE, name), img)
        except OSError:
            pass

    @staticmethod
    def _cards_ok(state, min_rank=0.6):
        """Обе карманные карты распознаны И уверенно (rank_score >= min_rank).

        Слабый эталон «2» (0.51-0.55 против порога 0.45) пропускал искажённые
        «7» как «2» (живой тест: фолд карманных 77). Низкоуверенное чтение
        отбрасываем — перечитываем кадр или играем безопасно.
        """
        detail = state['cards_detail']['hole']
        if len(detail) < 2:
            return False
        for d in detail[:2]:
            if not d['card'] or d.get('rank_score', 0) < min_rank:
                return False
        return True

    @staticmethod
    def _sig(state):
        """Сигнатура состояния: (карты, улица, доска, ставка, сумма колла, отпечаток суммы).

        to_call_bb (жёлтая сумма на кнопке колла) включена, потому что ререйз
        оппонента меняет именно её, тогда как has_bet остаётся True. Без неё
        после нашего рейза бот не видит переставку и молчит до таймаута
        (живой тест: QQ -> RAISE, оппонент переставил — бот спасовал по таймеру).

        call_fp — отпечаток зоны суммы (цифры не читаются, но раскладка жёлтых
        пикселей меняется при каждой переставке): ловит ререйз даже когда
        to_call_bb=None (нет эталонов цифр) — живой тест: CALL -> переставка.
        """
        tc = state.get('to_call_bb')
        return (tuple(c for c in state['hole'] if c), state['street'],
                tuple(state['board']), state['has_bet'],
                round(tc, 1) if tc is not None else None,
                state.get('call_fp'))

    # ---------- вспомогательное ----------
    def log(self, msg):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            # консоль Windows не в UTF-8 (cp866 не знает тире «—») — не падать из-за лога
            enc = sys.stdout.encoding or 'ascii'
            print(line.encode(enc, 'replace').decode(enc, 'replace'), flush=True)
        try:
            with open(self.log_path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass

    def active_flags(self):
        """Включённые переключатели (стратегии и самого бота) — для истории раздач."""
        on = [k for k in strategy.FLAG_KEYS if self.chart.settings.get(k)]
        on += [k for k in config.BOT_FLAGS if getattr(self, k, False)]
        return on

    def decision_line(self, state, note, action, reason, amount=None):
        """Полный контекст решения одной строкой (перед ней лог ставит время).

        [#42 river] A♠Q♥ | доска 8♠8♦4♠6♥K♦ | банк 24ББ | колл 8ББ (25%) |
        стек 61ББ | поз BTN (2 игр) | сделано: две пары 8/4 (weak) |
        решение: fold | причина: ...
        """
        pot, to_call, price = state['pot_bb'], state['to_call_bb'], note['pot_odds']
        parts = [f"[#{self.hand_id} {state['street']}] {pretty_cards(state['hole'])}"]
        if state['board']:
            parts.append(f"доска {pretty_cards(state['board'])}")
        parts.append(f'банк {pot:.1f}ББ' if pot is not None else 'банк ?')
        if not state['has_bet']:
            parts.append('ставки нет')
        else:
            price_note = f' ({price:.0%})' if price is not None else ''
            parts.append(f'колл {to_call}ББ{price_note}' if to_call is not None
                         else f'колл ?ББ{price_note}')
        parts.append(f'стек {self.stack_bb:.1f}ББ')
        parts.append(f"поз {state['position'] or '?'} ({state['players']} игр)")
        made = MADE_TITLES.get(note['made'], note['made'])
        name = note['name'] + (f" ({note['made_note']})" if note['made_note'] else '')
        parts.append(f"сделано: {name or '?'} ({made})")
        size = f' {amount}ББ' if amount else ''
        parts.append(f'решение: {action}{size}')
        parts.append(f'причина: {reason}')
        return ' | '.join(parts)

    def record(self, entry):
        try:
            with open(self.history_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError as e:
            self.log(f'не записал историю: {e}')

    def opponent_profile(self):
        """Профиль оппонента из players.json (кроме героя), если он один.

        Статистика копится по ВСЕМ местам за столом, а вот подстраиваться под
        неё можно только когда оппонент один: иначе непонятно, чьи цифры
        применять к решению.
        """
        if not self.opponent_memory:
            return None
        others = [v for k, v in self.players_db.items()
                  if isinstance(v, dict) and k != config.HERO_NAME
                  and not v.get('merged_into')]
        return others[0] if len(others) == 1 else None

    # ---------- память оппонентов ----------
    def observe(self, state):
        """Учесть кадр в наблюдениях за оппонентами; на границе раздачи — записать.

        Зовётся на КАЖДОМ кадре, в том числе когда ход не наш: оппоненты как раз
        тогда и играют. Наблюдение не должно ронять игровой цикл — любая ошибка
        разбора кадра только пишется в лог.
        """
        if not self.opponent_memory:
            return None
        try:
            finished = self.observer.observe(state)
            return self.save_profiles(finished) if finished else None
        except Exception as e:                    # цикл важнее статистики
            self.log(f'память оппонентов: кадр не учтён ({type(e).__name__}: {e})')
            return None

    def save_profiles(self, observed):
        """Итог раздачи -> players.json. Возвращает имена обновлённых профилей."""
        names = self.profiles.update_all(observed, nicks=self.nicks)
        dropped = self.profiles.dropped
        if not names and not dropped:
            return names          # никто не проявился (пустые плашки) — файл не трогаем
        if not self.profiles.save():
            self.log('память оппонентов: players.json не записан')
        for name in dropped:
            self.log(f'память оппонентов: «{name}» — пустое место, запись стёрта')
        for name in names:
            self.log('память оппонентов: '
                     + opponents.summary_line(name, self.players_db[name]))
        return names

    # ---------- ники оппонентов ----------
    def update_nicks(self, img, state=None):
        """Прочитать ники занятых мест. {место по кругу от героя: ник}.

        Зовётся РАЗ В РАЗДАЧУ (там же, где update_stack): каждый вызов — запуск
        tesseract на каждое занятое место, на кадр этого не напасёшься, а внутри
        раздачи ники не меняются.

        Место, ник которого в этот раз не прочитался (смайлик закрыл плашку,
        всплыл ярлык действия), берёт ник из прошлой раздачи. Кэш живёт, пока
        место занято: опустело — забываем, там сядет уже другой человек.

        Тот же кэш решает и спор написаний. Пока место не пустело, за ним сидит
        ТОТ ЖЕ человек, как бы OCR ни прочитал его ник в этот раз: «INeedAHero»
        и «МеедАНего» — одна плашка, прочитанная то латиницей, то кириллицей, и
        по буквам они не похожи совсем. Такое написание уходит в aliases профиля
        из кэша, а нового профиля не заводится.
        """
        if not self.read_nicks or img is None:
            self.nicks = {}
            return self.nicks
        seats = (state or {}).get('seats') or []
        try:
            fresh = nick_reader.read_nicks(img, seats, tesseract=self.tesseract)
        except Exception as e:                   # чтение кадра не должно ронять ход
            self.log(f'ники не прочитаны ({type(e).__name__}: {e}) — играю по местам')
            return self.nicks
        occupied = range(1, sum(1 for s in seats if not s.get('hero')) + 1)
        nicks, merged = {}, False
        for seat in occupied:
            nick = fresh.get(seat) or self.nicks.get(seat)
            name, note = self.same_player(seat, nick)
            if name:
                nicks[seat] = name
            if self._nick_seen.get(seat) == (nick or ''):
                continue                         # об этом месте уже говорили
            self._nick_seen[seat] = nick or ''
            if not nick:
                self.log(f'ник на месте {seat} не прочитался — '
                         f'{opponents.seat_name(seat)} (место)')
                continue
            moved = (self.profiles.merge(opponents.seat_name(seat), name)
                     if self.opponent_memory else 0)
            # алиас дописан в профиль — базу тоже надо сохранить
            merged = merged or bool(moved) or bool(note and self.opponent_memory)
            tail = (f' (статистика перенесена: {moved} {opponents.hands_word(moved)})'
                    if moved else '')
            self.log((note or f'оппонент на месте {seat} → ник "{name}"') + tail)
        for seat in [s for s in self._nick_seen if s not in occupied]:
            self._nick_seen.pop(seat, None)      # место опустело — говорим заново
        if merged and not self.profiles.save():
            self.log('память оппонентов: players.json не записан')
        self.nicks = nicks
        return nicks

    def same_player(self, seat, nick):
        """Ник с плашки -> (имя профиля, строка в лог о переклейке имени).

        Имя профиля — не всегда сам ник: OCR путает буквы, и «TNeedAHero» должен
        попасть в статистику «INeedAHero», а не завести второго. Кто это,
        решается по двум признакам, кэш места сильнее:

        * место не пустело с прошлой раздачи — там тот же человек, как бы ни
          прочиталась плашка в этот раз (кириллица вместо латиницы — ники не
          похожи вовсе, а игрок один);
        * иначе — нестрогое сравнение с уже известными никами (Profiles.resolve).
        """
        if not nick:
            return None, ''
        name, score = self.profiles.resolve(nick)
        prev = self.nicks.get(seat)
        if prev and prev != name:
            name = self.profiles.canonical(prev)
            added = self.profiles.add_alias(name, nick)
            return name, (f'место {seat}: ник "{nick}" → тот же игрок (кэш места), '
                          f'статистика в "{name}"'
                          + (' (алиас добавлен)' if added else ''))
        if score and name != nick:
            return name, (f'ник "{opponents.norm_nick(nick)}" похож на "{name}" '
                          f'({opponents.ratio_str(score)}) — статистика туда')
        return name, ''

    def dedupe_profiles(self):
        """Слить в players.json разные написания одного ника (старт сессии).

        Дубли накопились, пока бот сравнивал ники буква в букву; сливаем их один
        раз при чтении базы, чтобы дальше играть против одного профиля.
        """
        if not self.opponent_memory:
            return []
        moves = self.profiles.merge_duplicates()
        for src, dst, moved, _score in moves:
            self.log(f'слиты дубли: "{src}" -> "{dst}" '
                     f'({moved} {opponents.hands_word(moved)})')
        if moves and not self.profiles.save():
            self.log('память оппонентов: players.json не записан')
        return moves

    def log_memory(self):
        """Что бот помнит об оппонентах — строкой на старте сессии."""
        if not self.opponent_memory:
            self.log('память оппонентов: выключена (флаг opponent_memory)')
            return []
        self.dedupe_profiles()
        rows = self.profiles.opponents()
        if not rows:
            self.log('память оппонентов: профилей пока нет — копим с первой раздачи')
        for row in rows:
            self.log('память оппонентов: ' + opponents.summary_line(row['name'], row))
        return rows

    # ---------- человечные тайминги ----------
    def human_delay(self, action, elapsed=0.0):
        """Сколько «думать» перед тапом, секунд. 0 — тапаем сразу.

        Запас важнее правдоподобия: ClubGG даёт на ход около 20-30 секунд, и
        если кнопки висят давно (раскрывали столбец, перечитывали карты, ход
        вернулся после долгих раздумий оппонента), задержка не добавляется —
        проиграть ход по таймауту хуже, чем выглядеть роботом.
        """
        if not self.human_timing:
            return 0.0
        lo, hi = self.timing[TIMING_KEYS.get(action, 'timing_fold')]
        delay = random.uniform(lo, hi) + random.uniform(-config.TIMING_JITTER,
                                                        config.TIMING_JITTER)
        delay = min(max(delay, 0.0), config.TIMING_MAX)
        budget = config.TURN_BUDGET - config.TURN_RESERVE - max(0.0, elapsed)
        return round(max(0.0, min(delay, budget)), 2)

    def think(self, action):
        """Пауза перед тапом. Возвращает, сколько реально прождали."""
        elapsed = time.time() - self._turn_seen if self._turn_seen else 0.0
        delay = self.human_delay(action, elapsed)
        if self.human_timing and not delay and elapsed > 1.0:
            self.log(f'ход на экране уже {elapsed:.0f}с — тапаю сразу, без паузы')
        if delay:
            time.sleep(delay)
        return delay

    def bet_point(self, state, pot_frac=None):
        """Куда тапать ставку/рейз: центр ЖИВОГО пресета правого столбца. None — некуда.

        Размер ставки в ClubGG задаётся выбором пресета (33/50/75/100% банка либо
        «Рейз до X ББ»), а не отдельным подтверждением: тап по пресету и есть
        ставка. Пресет, который меньше минимальной ставки, клиент гасит — тап по
        нему не делает ничего, и ход сгорает по таймауту. Раньше бот всегда бил в
        эталонную точку (881,2319) — нижний, самый мелкий пресет — и молчал 34
        секунды, когда тот был погашен (живые кадры 15:48:41, 15:49:25).

        pot_frac — доля банка, которую хочет стратегия; берём ближайший к ней
        живой пресет. Без неё (префлоп, чужая раскладка пресетов) — самый мелкий
        живой, как и раньше.
        """
        presets = state.get('raise_presets') or []
        live = [p for p in presets if p['enabled']]
        if not live:                         # см. can_raise
            return None
        best = live[0]                       # самый мелкий живой — как было раньше
        if pot_frac and not state['has_bet']:
            # доли банка подписаны только у пресетов ставки; против ставки они
            # подписаны абсолютным «Рейз до X ББ» — сравнивать не с чем
            fr = config.PRESET_POT_FRAC
            best = min(live, key=lambda p: abs(fr[min(p['i'], len(fr) - 1)] - pot_frac))
        if not presets[0]['enabled']:
            self.log(f'нижний пресет ставки погашен (мельче минимума) — '
                     f'жму пресет #{best["i"]} в ({best["x"]},{best["y"]})')
        return best['x'], best['y']

    def can_raise(self, state):
        """Можно ли вообще поднять: есть ли в столбце хоть один живой пресет.

        Ровно то условие, при котором bet_point находит точку тапа, но без его
        побочного лога — проверять доступность рейза надо до решения.
        """
        return any(p['enabled'] for p in (state.get('raise_presets') or []))

    # ---------- настройки на лету ----------
    def apply_config(self, cfg, quiet=False):
        """Применить запись устройства: стиль -> отдельные ключи -> ползунки.

        Всегда собирается от base_settings, поэтому повторное применение той же
        записи ничего не меняет, а снятая галочка возвращается к чарту.
        """
        settings = strategy.device_settings(self.base_settings, cfg)
        changed = [k for k, v in settings.items() if self.chart.settings.get(k) != v]
        self.chart.settings = settings
        self.style = str(cfg.get('style') or strategy.DEFAULT_STYLE).lower()
        self.live_stack = bool(cfg.get('live_stack', True))
        self.opponent_memory = bool(cfg.get('opponent_memory', True))
        self.human_timing = bool(cfg.get('human_timing', True))
        self.read_nicks = bool(cfg.get('read_nicks', True))
        self.tesseract = cfg.get('tesseract') or config.TESSERACT
        self.timing = timing_ranges(cfg)
        stack = cfg.get('stack')
        if stack:
            try:
                self.stack_bb = float(stack)
                self.stack_auto = bool(cfg.get('stack_auto'))
                if not self.stack_auto:
                    # стек задан человеком (панель/ключ запуска) — от него и
                    # считаем границы здравого чтения с экрана
                    self.start_stack_bb = self.stack_bb
            except (TypeError, ValueError):
                pass
        if changed and not quiet:
            shown = ', '.join(f'{k}={settings[k]}' for k in sorted(changed)[:8])
            self.log(f'настройки обновлены (стиль {cfg.get("style") or strategy.DEFAULT_STYLE}): '
                     f'{shown}' + (' …' if len(changed) > 8 else ''))
        return changed

    def refresh_settings(self):
        """Перечитать devices.json, если файл изменился (сверка по mtime — дёшево).

        Вызывается перед каждым РЕШЕНИЕМ, а не на каждом кадре: панель сохраняет
        настройки — бот подхватывает их со следующего хода, без перезапуска.
        """
        if not self.serial:
            return False
        try:
            mtime = os.path.getmtime(self.devices_path)
        except OSError:
            return False
        if mtime == self._cfg_mtime:
            return False
        self._cfg_mtime = mtime
        try:
            with open(self.devices_path, encoding='utf-8') as f:
                devices = json.load(f)
        except (OSError, ValueError) as e:
            self.log(f'devices.json не прочитан ({e}) — играю на прежних настройках')
            return False
        record = next((d for d in devices if isinstance(d, dict)
                       and d.get('serial') == self.serial), None)
        if record is None:
            return False
        return bool(self.apply_config({**self.cli_cfg, **record}))

    # ---------- живой стек ----------
    def stack_sane(self, value):
        """Похоже ли прочитанное с экрана число на наш стек.

        Верхняя граница — STACK_SANE_MULT от самого большого стека, который мы
        видели (заданного человеком или уже прочитанного): выиграть за одну
        раздачу больше десяти своих стеков нельзя, а вот потерянная десятичная
        точка («118.4» -> 1184) — это ровно 10x и отсекается именно так.
        """
        base = max(self.start_stack_bb or 0.0, self.stack_bb or 0.0)
        return bool(value and value > 0
                    and (not base or value < self.STACK_SANE_MULT * base))

    def update_stack(self, img):
        """Прочитать свой стек с экрана и запомнить его. Возвращает ББ или None.

        Вызывается РАЗ В РАЗДАЧУ (см. step): чтение стоит распознавания глифов,
        а внутри раздачи наш стек меняется только на нашу же ставку, которую
        стратегия и так учитывает как долю стека.
        """
        if not self.live_stack or img is None:
            return None
        try:
            value = ts.read_own_stack(img, tpl_dir=self.tpl_dir)
        except Exception as e:                   # чтение кадра не должно ронять ход
            self.log(f'стек не прочитан ({type(e).__name__}: {e}) — '
                     f'использую {self.stack_bb:.1f} ББ')
            return None
        if value is None or not self.stack_sane(value):
            self.log(f'стек не прочитан{"" if value is None else f" (мусор {value})"} — '
                     f'использую {self.stack_bb:.1f} ББ')
            return None
        if abs(value - self.stack_bb) < self.STACK_EPS and self.stack_auto:
            return value                         # то же значение — файл не трогаем
        self.stack_bb = value
        self.stack_auto = True
        self.log(f'стек: {value:.1f} ББ (с экрана)')
        self.save_stack(value)
        return value

    def save_stack(self, value):
        """Записать свой стек в devices.json: только поля stack/stack_auto своей записи.

        Файл читается целиком и пишется целиком (панель формата не знает иного),
        поэтому чужие устройства и любые чужие ключи переносятся как есть, а
        подменяются ровно два поля. Записи с нашим серийником нет — не выдумываем
        её: значит, панель этим телефоном не управляет.
        """
        if not self.serial:
            return False
        try:
            with open(self.devices_path, encoding='utf-8') as f:
                devices = json.load(f)
        except (OSError, ValueError) as e:
            self.log(f'стек не записан в devices.json ({e})')
            return False
        if not isinstance(devices, list):
            self.log('стек не записан: devices.json — не список устройств')
            return False
        record = next((d for d in devices if isinstance(d, dict)
                       and d.get('serial') == self.serial), None)
        if record is None:
            return False
        record['stack'] = round(float(value), 1)
        record['stack_auto'] = True
        tmp = self.devices_path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(devices, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.devices_path)   # панель не должна прочитать полфайла
            # свою же запись перечитывать незачем: mtime сдвинули мы сами
            self._cfg_mtime = os.path.getmtime(self.devices_path)
        except OSError as e:
            self.log(f'стек не записан в devices.json ({e})')
            return False
        return True

    def bluff_ok(self, state):
        """Разрешён ли блеф с блокером в этой ситуации: не чаще одной из N.

        Ситуация считается один раз на раздачу и улицу: за один ход стратегию
        могут спросить дважды (раскрытие столбца, недоступный рейз).
        """
        key = (self.hand_id, state['street'], tuple(state['board']))
        if key != self._bluff_key:
            every = max(1, int(self.chart.settings['blocker_bluff_every']))
            self._bluff_key = key
            self._bluff_last = self._blocker_spots % every == 0
            self._blocker_spots += 1
        return self._bluff_last

    def decide(self, state):
        self.refresh_settings()
        if strategy.blocker_bluff_spot(state, self.chart, self.stack_bb):
            state = {**state, 'bluff_ok': self.bluff_ok(state)}
        return strategy.decide(state, profile=self.opponent_profile(),
                               stack_bb=self.stack_bb, chart=self.chart)

    def wants_expand(self, state, pot_frac=None):
        """Надо ли раскрывать свёрнутый столбец ставки перед рейзом/бетом.

        Свёрнутый столбец — это ОДНА кнопка «Бет»/«Рейз до» вместо четырёх.
        Раскрываем в двух случаях: ставить нечем (единственная кнопка погашена —
        её размер меньше минимальной ставки, тап по ней не делает ничего) или
        доступный размер далёк от того, который просит стратегия.
        """
        if not state.get('presets_collapsed'):
            return False
        live = [p for p in (state.get('raise_presets') or []) if p['enabled']]
        if not live:
            return True
        if pot_frac and not state['has_bet']:
            # доли банка подписаны только у пресетов ставки (33/50/75/100%);
            # против ставки строки подписаны абсолютным «Рейз до X ББ»
            fr = config.PRESET_POT_FRAC
            have = fr[min(live[0]['i'], len(fr) - 1)]
            return abs(have - pot_frac) > self.PRESET_FRAC_TOL
        return False

    def expand_presets(self, state, img):
        """Тапнуть шеврон и перечитать кадр. Возвращает (состояние, кадр).

        Второй шаг взаимодействия: клиент перерисовывает столбец не мгновенно,
        поэтому после тапа ждём EXPAND_WAIT и снимаем кадр заново (до
        EXPAND_TRIES раз — тап при этом ровно один, повторный свернул бы
        столбец обратно). Не получилось (шеврона нет, кадр не изменился, ход
        ушёл) — возвращаем прежнее состояние, и вызывающий сыграет пассивно.
        """
        point = state.get('chevron')
        if point is None:
            self.log('столбец ставки свёрнут, но шеврона не видно — играю пассивно')
            return state, img
        if self.dry_run:
            self.log(f'столбец ставки свёрнут — тапнул бы шеврон {point} (dry-run)')
            return state, img
        self.log(f'столбец ставки свёрнут — тапаю шеврон {point}, перечитываю кадр')
        self.screen.tap(*point)
        hole = [c for c in state['hole'] if c]
        for _ in range(self.EXPAND_TRIES):
            time.sleep(self.EXPAND_WAIT)
            img2 = self.screen.grab()
            if img2 is None:
                break
            state2 = ts.read_state(img2, tpl_dir=self.tpl_dir)
            if not (state2['my_turn'] and state2['in_hand']):
                break                    # ход ушёл — решать по этому кадру нельзя
            if [c for c in state2['hole'] if c] != hole:
                break                    # другая раздача — старое решение не годится
            if len(state2['raise_presets']) > len(state['raise_presets']):
                live = sum(1 for p in state2['raise_presets'] if p['enabled'])
                self.log(f'столбец раскрылся: строк {len(state2["raise_presets"])}, '
                         f'из них живых {live}')
                return state2, img2
        self.log('столбец не раскрылся — играю пассивно (чек/колл)')
        return state, img

    def resolve_tap(self, state, action, pot_frac=None):
        """Действие -> (действие, точка).

        Рейз без живой кнопки бета сюда доходить не должен: этот случай
        разбирает step — переспрашивает стратегию с no_raise, и она решает
        колл/фолд по пот-оддсам. Подмена ниже осталась последней страховкой:
        тапать в пустоту или в погашенную кнопку хуже, чем сыграть пассивно
        (такой тап не проходит и ход сгорает по таймауту).
        """
        if action == 'raise':
            point = self.bet_point(state, pot_frac)
            if point is not None:
                return action, point
            action = 'call' if state['has_bet'] else 'check'
            self.log('живой кнопки ставки нет (погашена или её не видно) -> ' + action)
            return action, state['taps'].get('call')
        return action, state['taps'].get('call' if action in ('call', 'check') else action)

    # ---------- один шаг ----------
    def step(self, img=None, state=None):
        """Один проход. Возвращает запись раздачи (dict) или None, если ход не наш.

        state можно передать готовым (run читает его для своих проверок) — тогда
        повторно кадр не снимается и состояние не перечитывается.
        """
        if state is None:
            img = self.screen.grab() if img is None else img
            if img is None:
                self.log('ERR: не получен скриншот (adb)')
                return None
            state = ts.read_state(img, tpl_dir=self.tpl_dir)
        self.last_state = state
        self.observe(state)          # кадр в память оппонентов (в run — на каждом)
        if not state['my_turn']:
            self.stable = 0
            return None
        # КРИТИЧНО: карт у героя нет -> он не в раздаче, кнопки чужие/анимация. Не тапаем.
        if not state['in_hand']:
            self.stable = 0
            self.log('кнопки есть, но карманных карт нет — не моя раздача, пропускаю')
            return None

        hole = [c for c in state['hole'] if c]
        # новая раздача: карманные карты сменились
        if hole and hole != self.last_hole:
            self.hand_id += 1
            self.last_hole = hole
            # стек читаем на первом ходе раздачи: короткий стек, «колл ≥ доли
            # стека = алл-ин» и имплайд-оддсы должны считаться от правды, а не
            # от константы, записанной в панели один раз
            self.update_stack(img)
            # и ники: профиль должен держаться за человека, а не за место —
            # иначе пересевший игрок заводит себе второй профиль
            self.update_nicks(img, state)

        decision = self.decide(state)
        # свёрнутый столбец ставки: сначала раскрыть его шевроном, потом решать
        # заново по перерисованному кадру — одно «действие» бота из двух тапов
        if decision['action'] == 'raise' and self.wants_expand(state, decision.get('pot_frac')):
            expanded, img = self.expand_presets(state, img)
            if expanded is not state:
                state = self.last_state = expanded
                decision = self.decide(state)
        # рейз некуда тапнуть (оппонент в алл-ине, живых пресетов нет): раньше
        # он молча становился коллом, и «76s: 3-бет на велью» превращался в колл
        # 23.7ББ против алл-ина (живая раздача 19.08 09:52). Теперь спрашиваем
        # стратегию заново, зная, что рейз недоступен, — она решает колл/фолд.
        if decision['action'] == 'raise' and not self.can_raise(state):
            blocked = self.decide({**state, 'no_raise': True})
            self.log(f"рейз недоступен ({decision['reason']}) -> "
                     f"{blocked['action'].upper()}: {blocked['reason']}")
            decision = blocked
        action, point = self.resolve_tap(state, decision['action'],
                                         decision.get('pot_frac'))
        reason = decision['reason']
        amount = decision['amount_bb']
        if action != decision['action']:
            reason = f"{reason} [{decision['action']} невозможен -> {action}]"
            amount = None

        note = strategy.hand_note(state['hole'], state['board'], state['street'],
                                  state['to_call_bb'], state['pot_bb'])
        entry = {
            'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
            'hand_id': self.hand_id,
            'street': state['street'],
            'hole': state['hole'],
            'board': state['board'],
            'players': state['players'],
            'players_seated': state['players_seated'],
            'position': state['position'],
            'dealer': state['dealer'],
            'first_to_act': state['first_to_act'],
            'pot_bb': state['pot_bb'],
            'to_call_bb': state['to_call_bb'],
            'has_bet': state['has_bet'],
            'action': action,
            'amount_bb': amount,
            'reason': reason,
            'tap': point,
            'dry_run': self.dry_run,
            # --- контекст решения (формат расширен, старые поля не тронуты) ---
            'made': note['made'],
            'made_note': note['made_note'],
            'pot_odds_pct': None if note['pot_odds'] is None else round(note['pot_odds'] * 100),
            'equity_pct': None if note['equity'] is None else round(note['equity'] * 100),
            'stack_bb': round(self.stack_bb, 1) if self.stack_bb else None,
            'style': self.style,
            'flags': self.active_flags(),
            'think_s': 0.0,
        }
        self.log(self.decision_line(state, note, action, reason, amount))
        if len(hole) < 2:
            self._save_frame(img, f'{action}_badcards')
        elif self.save_frames:
            self._save_frame(img, action)
        if not self.dry_run and point:
            entry['think_s'] = self.think(action)
            self.screen.tap(*point)
        self.observer.note_action(action, state['street'])
        self.record(entry)
        self.actions += 1
        self.stats[action] = self.stats.get(action, 0) + 1
        self._last_action = action
        self.last_action_ts = time.time()
        return entry

    def flush_memory(self):
        """Дописать профили по незакрытой раздаче (бота останавливают посреди игры)."""
        if not self.opponent_memory:
            return None
        finished = self.observer.finish()
        return self.save_profiles(finished) if finished else None

    def summary(self):
        """Итоги сессии в лог: сколько раздач, решений и каких."""
        self.flush_memory()
        mins = (time.time() - self.started) / 60
        counts = ' '.join(f'{k}={v}' for k, v in self.stats.items() if v)
        self.log(f'ИТОГИ: раздач={self.hand_id} решений={self.actions} '
                 f'[{counts or "нет"}] за {mins:.1f} мин')

    def wait_until_idle(self, interval=1.2, tries=8):
        """Дождаться, пока кнопки исчезнут (ход принят), но не дольше tries кадров.

        Без этого следующий кадр может застать те же кнопки и мы сыграем дважды.
        """
        for _ in range(tries):
            img = self.screen.grab()
            if img is None or not ts.is_my_turn(img):
                return True
            time.sleep(interval)
        self.log('кнопки не исчезли после хода — жду следующий кадр')
        return False

    # ---------- цикл ----------
    def run(self, interval=0.3, settle=2.5, stable_frames=1, max_actions=None,
            fail_limit=30, retry_after=25.0, card_confirm=2):
        """Игровой цикл, реактивный по состоянию.

        После своего хода НЕ ждём исчезновения кнопок (в ClubGG панель остаётся
        на экране, пока думает оппонент): следующее действие наступает, когда
        состояние стола ИЗМЕНИЛОСЬ (новая улица/доска/ставка/раздача) — сигнатура
        (карты, улица, доска, ставка) не совпадает с той, где мы уже сыграли.
        Это убирает многоминутные паузы и защищает от двойного тапа.

        Если состояние не меняется дольше retry_after секунд — считаем, что тап
        не прошёл, и повторяем решение (не чаще 2 раз подряд, потом пауза 30с).

        idle-поллинг с паузой interval; на ходу кадры снимаются подряд (без паузы),
        два стабильных кадра подряд = ход действительно наш (не анимация).
        """
        self.log('=== БОТ ЗАПУЩЕН (без ИИ) ===' + (' dry-run' if self.dry_run else ''))
        self.log_memory()
        fails = 0
        acted_sig = None
        acted_ts = 0.0
        opp_acted = False     # после нашего хода ход уходил к оппоненту и вернулся
        try:
            while max_actions is None or self.actions < max_actions:
                try:
                    img = self.screen.grab()
                    if img is None:
                        fails += 1
                        if fails >= fail_limit:
                            self.log(f'ERR: {fails} кадров подряд не получено — выхожу '
                                     '(телефон отключён?)')
                            return
                        self.log('ERR: скриншот не получен, пауза 2с')
                        time.sleep(2)
                        continue
                    fails = 0
                    state = ts.read_state(img, tpl_dir=self.tpl_dir)
                    self.observe(state)     # оппоненты играют как раз не на нашем ходу
                    if not (state['my_turn'] and state['in_hand']):
                        opp_acted = True    # ход не наш: оппонент думает/сыграл
                        self.stable = 0
                        self._turn_sig = self._turn_seen = None
                        if interval:
                            time.sleep(interval)
                        continue
                    now = time.time()
                    sig = self._sig(state)
                    if sig != self._turn_sig:
                        # новый ход на экране: с этого мгновения тикает время до
                        # таймаута, из которого «человечная» пауза берёт запас
                        self._turn_sig, self._turn_seen = sig, now
                    if sig == acted_sig and now - acted_ts < retry_after:
                        if opp_acted:
                            # после нашего хода ход вернулся, а сигнатура «та же»:
                            # сумма колла не читается (нет эталонов цифр), значит
                            # это ререйз/переставка оппонента — НОВОЕ решение.
                            # Без этого бот молчит до таймаута (живой тест: QQ).
                            self.log('ход вернулся после оппонента (ререйз/переставка) — '
                                     'решаю заново')
                            acted_sig = None
                            opp_acted = False
                        else:
                            self.stable = 0      # тот же ход, где мы уже сыграли
                            if interval:
                                time.sleep(interval)
                            continue
                    if sig == acted_sig:     # состояние не меняется — тап мог не пройти
                        # Повторяем ТОЛЬКО чек/фолд: повторный тап по «Бет»/«Колл» =
                        # двойная ставка (рейз/колл того же размера второй раз).
                        if self._last_action not in ('check', 'fold') or self._retries >= 2:
                            self.log('состояние не изменилось после хода — жду изменения '
                                     '(повтор опасен для этого действия)')
                            acted_ts = now + 30
                            self._retries = 0
                            if interval:
                                time.sleep(interval)
                            continue
                        self._retries += 1
                        self.log('состояние не изменилось после хода — повторяю решение')
                    else:
                        self._retries = 0
                    if now - acted_ts < settle:   # кулдаун после своего тапа
                        if interval:
                            time.sleep(interval)
                        continue
                    # два кадра подряд с кнопками и картами = ход действительно наш
                    self.stable += 1
                    if self.stable < stable_frames:
                        continue             # без паузы: добираем подтверждающий кадр
                    self.stable = 0
                    # подтверждение КАРТ: действуем только когда обе карманные карты
                    # распознаны и уверенно (живые тесты: [None,2c] и 7d->2d ломали
                    # решения). Перечитываем кадр до card_confirm раз; если так и не
                    # вышло — действуем безопасно (step сам выберет чек/фолд).
                    for _ in range(card_confirm):
                        if self._cards_ok(state):
                            break
                        img = self.screen.grab()
                        if img is None:
                            break
                        state = ts.read_state(img, tpl_dir=self.tpl_dir)
                        self.last_state = state
                        if not (state['my_turn'] and state['in_hand']):
                            break
                    if not (state['my_turn'] and state['in_hand']):
                        if interval:
                            time.sleep(interval)
                        continue
                    entry = self.step(img, state)
                    if entry is None:
                        if interval:
                            time.sleep(interval)
                        continue
                    acted_ts = time.time()
                    opp_acted = False    # свежий ход: ждём новый сигнал от оппонента
                    # сигнатура по состоянию, на котором РЕАЛЬНО сыграли (после
                    # перечитывания карт и раскрытия столбца оно могло измениться —
                    # иначе сыграем дважды)
                    acted_sig = self._sig(self.last_state or state)
                except subprocess.TimeoutExpired:
                    self.log('adb не ответил, пауза 3с')
                    time.sleep(3)
                except Exception as e:                   # цикл не должен падать
                    self.log(f'ОШИБКА: {type(e).__name__}: {e}')
                    time.sleep(2)
            self.log(f'достигнут лимит решений ({max_actions})')
        except KeyboardInterrupt:
            self.log('=== БОТ ОСТАНОВЛЕН ===')
        finally:
            self.summary()


def load_players_db(path=None):
    return opponents.load(path or config.PLAYERS_FILE)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Автономный бот ClubGG (без ИИ)')
    ap.add_argument('--dry-run', action='store_true', help='не тапать, только логировать')
    ap.add_argument('--once', action='store_true', help='один проход и выход')
    ap.add_argument('--image', help='разобрать кадр из файла вместо телефона')
    ap.add_argument('--interval', type=float, default=0.3, help='пауза между кадрами, с (простой; на ходу — подряд)')
    ap.add_argument('--settle', type=float, default=2.5, help='пауза после своего хода, с')
    ap.add_argument('--stack', type=float, default=69.6, help='стек в ББ')
    ap.add_argument('--no-live-stack', action='store_true',
                    help='не читать свой стек с экрана (играть по --stack)')
    ap.add_argument('--no-memory', action='store_true',
                    help='не копить статистику оппонентов и не подстраиваться под неё')
    ap.add_argument('--no-human-timing', action='store_true',
                    help='без человечных пауз перед тапом (действовать сразу)')
    ap.add_argument('--no-nicks', action='store_true',
                    help='не читать ники с экрана (звать оппонентов по местам)')
    ap.add_argument('--tesseract', help=f'путь к tesseract.exe (по умолчанию {config.TESSERACT})')
    ap.add_argument('--max-actions', type=int, help='сыграть N решений и выйти')
    ap.add_argument('--adb', help=f'путь к adb (по умолчанию {config.ADB})')
    ap.add_argument('--serial', help=f'серийник телефона (по умолчанию {config.SERIAL})')
    ap.add_argument('--templates', help=f'папка эталонов (по умолчанию {config.TEMPLATES_DIR})')
    ap.add_argument('--chart', help='файл чарта стратегии (charts/6max_standard.json)')
    ap.add_argument('--aggression', type=float, default=1.0,
                    help='множитель агрессивности (размеры ставок; 0.5-2.0)')
    ap.add_argument('--defense', type=float, default=1.0,
                    help='множитель защиты (готовность коллить; 0.5-2.0)')
    ap.add_argument('--style', default=strategy.DEFAULT_STYLE,
                    choices=sorted(strategy.STYLE_PRESETS),
                    help='пресет стиля (перекрывается записью в devices.json)')
    ap.add_argument('--devices', help=f'файл настроек панели (по умолчанию {config.DEVICES_FILE})')
    ap.add_argument('--name', help='имя бота (для логов панели)')
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, 'reconfigure'):    # русский лог в консоли Windows (cp866)
        sys.stdout.reconfigure(errors='replace')

    chart = None
    if args.chart:
        try:
            chart = strategy.load_chart(args.chart)
        except (OSError, ValueError) as e:
            print(f'ERR: чарт не загружен: {e}')
            return 2
        print(f'чарт: {chart.name} ({args.chart})')
    # Ключи запуска — только запасные значения: живые настройки бот берёт из
    # devices.json по своему серийнику и перечитывает их перед каждым решением.
    cli_cfg = {'style': args.style, 'aggression': args.aggression,
               'defense': args.defense, 'stack': args.stack,
               'live_stack': not args.no_live_stack,
               'opponent_memory': not args.no_memory,
               'human_timing': not args.no_human_timing,
               'read_nicks': not args.no_nicks,
               'tesseract': args.tesseract or config.TESSERACT}
    print(f'настройки: стиль {args.style}, агрессия x{args.aggression}, '
          f'защита x{args.defense}')

    screen = (FileScreen(args.image) if args.image
              else AdbScreen(adb=args.adb, serial=args.serial))
    bot = Bot(screen, dry_run=args.dry_run or bool(args.image), stack_bb=args.stack,
              players_db=load_players_db(), tpl_dir=args.templates, chart=chart,
              serial=args.serial or config.SERIAL, devices_path=args.devices, cfg=cli_cfg)
    if args.image or args.once:
        entry = bot.step()
        if entry is not None:
            print(json.dumps(entry, ensure_ascii=False, indent=2))
        elif bot.last_state is None:
            print('кадр не получен')
            return 1
        else:
            print('не мой ход;', json.dumps(
                {k: bot.last_state[k] for k in ('my_turn', 'in_hand', 'n_buttons', 'hole',
                                                'board', 'street', 'players', 'dealer',
                                                'position')},
                ensure_ascii=False))
        return 0
    bot.run(interval=args.interval, settle=args.settle, max_actions=args.max_actions)
    return 0


if __name__ == '__main__':
    sys.exit(main())
