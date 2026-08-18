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
import subprocess
import sys
import time

import cv2
import numpy as np

import config
import table_state as ts
import strategy


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
    def __init__(self, screen, dry_run=False, stack_bb=69.6, players_db=None,
                 tpl_dir=None, log_path=None, history_path=None, chart=None):
        self.screen = screen
        self.dry_run = dry_run
        self.stack_bb = stack_bb
        self.players_db = players_db or {}
        self.chart = chart or strategy.active_chart()
        self.tpl_dir = tpl_dir
        self.log_path = log_path or config.LOG_FILE
        self.history_path = history_path or config.HAND_HISTORY
        self.hand_id = 0
        self.last_hole = None
        self.last_action_ts = 0.0
        self.last_state = None
        self.stable = 0
        self.actions = 0
        self.stats = {'fold': 0, 'check': 0, 'call': 0, 'raise': 0}
        self.started = time.time()

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

    def record(self, entry):
        try:
            with open(self.history_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError as e:
            self.log(f'не записал историю: {e}')

    def opponent_profile(self):
        """Профиль оппонента из players.json (кроме героя), если он один."""
        others = [v for k, v in self.players_db.items()
                  if isinstance(v, dict) and k != config.HERO_NAME]
        return others[0] if len(others) == 1 else None

    @staticmethod
    def _on_button(state, point):
        """Точка попадает в реально найденную кнопку? (эталонная координата — нет)"""
        return bool(point) and any(b['x0'] <= point[0] <= b['x1'] for b in state['buttons'])

    def resolve_tap(self, state, action):
        """Действие -> (действие, точка). Рейз без видимой кнопки бета заменяем
        на колл/чек: тапать в пустоту хуже, чем сыграть пассивно."""
        point = state['taps'].get('call' if action in ('call', 'check') else action)
        if action == 'raise' and not self._on_button(state, point):
            action = 'call' if state['has_bet'] else 'check'
            point = state['taps'].get('call')
            self.log('кнопки бета нет на экране -> ' + action)
        return action, point

    # ---------- один шаг ----------
    def step(self, img=None):
        """Один проход. Возвращает запись раздачи (dict) или None, если ход не наш."""
        img = self.screen.grab() if img is None else img
        if img is None:
            self.log('ERR: не получен скриншот (adb)')
            return None

        state = ts.read_state(img, tpl_dir=self.tpl_dir)
        self.last_state = state
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

        decision = strategy.decide(state, profile=self.opponent_profile(),
                                   stack_bb=self.stack_bb, chart=self.chart)
        action, point = self.resolve_tap(state, decision['action'])
        reason = decision['reason']
        amount = decision['amount_bb']
        if action != decision['action']:
            reason = f"{reason} [{decision['action']} невозможен -> {action}]"
            amount = None

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
        }
        self.log(f"#{self.hand_id} {state['street']} {state['hole']} доска={state['board']} "
                 f"поз={state['position']} игроков={state['players']} "
                 f"ставка={'да' if state['has_bet'] else 'нет'} -> {action.upper()} "
                 f"({reason})")
        if not self.dry_run and point:
            self.screen.tap(*point)
        self.record(entry)
        self.actions += 1
        self.stats[action] = self.stats.get(action, 0) + 1
        self.last_action_ts = time.time()
        return entry

    def summary(self):
        """Итоги сессии в лог: сколько раздач, решений и каких."""
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
    def run(self, interval=1.2, settle=2.5, stable_frames=2, max_actions=None,
            fail_limit=30):
        """Бесконечный игровой цикл. max_actions — сыграть N решений и выйти."""
        self.log('=== БОТ ЗАПУЩЕН (без ИИ) ===' + (' dry-run' if self.dry_run else ''))
        fails = 0
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
                    if not ts.is_my_turn(img):
                        self.stable = 0
                        time.sleep(interval)
                        continue
                    # два кадра подряд с кнопками = ход действительно наш (не анимация)
                    self.stable += 1
                    if self.stable < stable_frames:
                        time.sleep(interval)
                        continue
                    if time.time() - self.last_action_ts < settle:
                        time.sleep(interval)  # кнопки ещё не убрались после нашего тапа
                        continue
                    self.stable = 0
                    entry = self.step(img)
                    if entry is None:
                        time.sleep(interval)
                        continue
                    time.sleep(settle)
                    if not self.dry_run:
                        self.wait_until_idle(interval)
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
    path = path or os.path.join(config.BASE, 'players.json')
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Автономный бот ClubGG (без ИИ)')
    ap.add_argument('--dry-run', action='store_true', help='не тапать, только логировать')
    ap.add_argument('--once', action='store_true', help='один проход и выход')
    ap.add_argument('--image', help='разобрать кадр из файла вместо телефона')
    ap.add_argument('--interval', type=float, default=1.2, help='пауза между кадрами, с')
    ap.add_argument('--settle', type=float, default=2.5, help='пауза после своего хода, с')
    ap.add_argument('--stack', type=float, default=69.6, help='стек в ББ')
    ap.add_argument('--max-actions', type=int, help='сыграть N решений и выйти')
    ap.add_argument('--adb', help=f'путь к adb (по умолчанию {config.ADB})')
    ap.add_argument('--serial', help=f'серийник телефона (по умолчанию {config.SERIAL})')
    ap.add_argument('--templates', help=f'папка эталонов (по умолчанию {config.TEMPLATES_DIR})')
    ap.add_argument('--chart', help='файл чарта стратегии (charts/6max_standard.json)')
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

    screen = (FileScreen(args.image) if args.image
              else AdbScreen(adb=args.adb, serial=args.serial))
    bot = Bot(screen, dry_run=args.dry_run or bool(args.image), stack_bb=args.stack,
              players_db=load_players_db(), tpl_dir=args.templates, chart=chart)
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
