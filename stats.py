#!/usr/bin/env python3
"""Статистика раздач: сколько бот выиграл и сколько проиграл.

Считаем по ОДНОМУ источнику — hand_history.jsonl, который бот пишет сам. В
каждой записи есть `stack_bb`: сколько больших блайндов было у бота, когда он
принимал это решение. Стек бот читает с экрана один раз за раздачу — на своём
первом ходе, — поэтому внутри одной раздачи во всех записях стоит одно и то же
число, а РЕЗУЛЬТАТ раздачи виден только на следующей: стек, прочитанный в начале
раздачи N+1, — это и есть стек «на выходе» из раздачи N.

    дельта(N) = стек_входа(N+1) − стек_входа(N)

Блайнды, наши ставки и выигранный банк в эту разницу уже входят — это чистый
результат. Дельта > 0 — победа, < 0 — поражение, около нуля — раздача без
вложений (сбросили на баттоне). Последняя раздача в файле — ещё не сыгранная до
конца: у неё нет «следующего» стека, и в подсчёт побед она не идёт.

Всё, что показывает панель, живёт в фишках: фишки = ББ × bb_value (по умолчанию
20 — стол на 1000 фишек это 50 ББ). Настройка `bb_value` лежит в devices.json.

Функции здесь чистые: на вход список записей, на выход — числа. Файл читает
класс History и только когда он изменился (сверка по mtime и размеру).
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config                       # noqa: E402

BB_VALUE_DEFAULT = 20.0      # фишек в одном большом блайнде
BB_VALUE_MIN = 0.01
BB_VALUE_MAX = 100000.0

# Меньше этого считаем «ноль»: раздача, в которую мы не вложились.
EPS_BB = 0.05
# За одну раздачу нельзя выиграть больше, чем лежит у пяти оппонентов (6-max).
# Такой скачок означает докупку фишек или переход за другой стол; это не
# результат раздачи, и в статистику он не идёт. Обратной проверки («проиграл
# больше своего стека») не нужно: ниже нуля стек не опускается, а ноль мы уже
# считаем нечитаемым — см. _stack.
MAX_WIN_MULT = 5.0

PERIODS = [
    ('session', 'За игру'),
    ('today', 'Сегодня'),
    ('week', 'Неделя'),
    ('month', 'Месяц'),
    ('all', 'Всё время'),
]
PERIOD_NOTES = {
    'session': 'с последнего нажатия «Старт»',
    'today': 'с полуночи',
    'week': 'последние 7 дней',
    'month': 'последние 30 дней',
    'all': 'вся история раздач',
}


# ---------------------------------------------------------------------------
# чтение файла
# ---------------------------------------------------------------------------
def parse_ts(value):
    """'2026-08-22 11:02:03' (или ISO с 'T') -> datetime. Мусор -> None."""
    if not value:
        return None
    text = str(value)[:19].replace('T', ' ')
    try:
        return datetime.datetime.strptime(text, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def read_records(path):
    """Записи из jsonl. Битую строку пропускаем: половина файла лучше, чем ошибка."""
    out = []
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    out.append(rec)
    except OSError:
        return []
    return out


def _stack(rec):
    """stack_bb записи как число (или None, если бот его не знал).

    Ноль — это тоже «не знал»: за столом с нулём фишек не сидят, значит бот не
    сумел прочитать стек с экрана. Раздача с нечитаемым стеком остаётся без
    результата — лучше не посчитать её, чем записать выдуманный проигрыш.
    """
    try:
        value = float(rec.get('stack_bb'))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def group_hands(records):
    """Записи -> раздачи. Раздача — подряд идущие записи с одним hand_id.

    Именно подряд, а не «все с одинаковым номером»: после перезапуска бота
    нумерация начинается заново, и раздача №1 сегодня не продолжение раздачи №1
    вчерашней.
    """
    hands = []
    current = None
    for rec in records:
        hand_id = rec.get('hand_id')
        if current is None or hand_id != current['hand_id']:
            current = {'hand_id': hand_id, 'records': []}
            hands.append(current)
        current['records'].append(rec)
    for hand in hands:
        stacks = [s for s in (_stack(r) for r in hand['records']) if s is not None]
        first, last = hand['records'][0], hand['records'][-1]
        hand['stack_in'] = stacks[0] if stacks else None
        hand['stack_last'] = stacks[-1] if stacks else None
        hand['ts'] = first.get('ts')
        hand['end_ts'] = last.get('ts')
        hand['time'] = parse_ts(hand['ts'])
        hand['last'] = last
    return hands


def fill_results(hands):
    """Проставить каждой раздаче результат: stack_out, delta_bb, result.

    result: 'win' | 'loss' | 'push' (сыграна вничью, без вложений) |
            'open' (результат ещё не известен — это последняя раздача) |
            'skip' (докупка/смена стола: разница не могла получиться из игры).
    """
    for i, hand in enumerate(hands):
        hand['stack_out'] = None
        hand['delta_bb'] = None
        hand['result'] = 'open'
        start = hand['stack_in']
        nxt = hands[i + 1] if i + 1 < len(hands) else None
        end = nxt['stack_in'] if nxt else None
        if start is None or end is None:
            continue
        delta = end - start
        if delta > MAX_WIN_MULT * start:
            hand['result'] = 'skip'
            continue
        hand['stack_out'] = end
        hand['delta_bb'] = round(delta, 2)
        hand['result'] = ('win' if delta > EPS_BB else
                          'loss' if delta < -EPS_BB else 'push')
    return hands


def load_hands(path):
    """Файл -> список раздач с результатами. Нет файла — пустой список."""
    return fill_results(group_hands(read_records(path)))


# ---------------------------------------------------------------------------
# агрегация
# ---------------------------------------------------------------------------
def to_chips(bb, bb_value=BB_VALUE_DEFAULT):
    """ББ -> фишки, целым числом (человек видит фишки, а не десятые доли ББ)."""
    if bb is None:
        return None
    return int(round(float(bb) * float(bb_value)))


def clean_bb_value(value, default=BB_VALUE_DEFAULT):
    """Сколько фишек в одном ББ. Кривое значение -> умолчание."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not (BB_VALUE_MIN <= value <= BB_VALUE_MAX):
        return float(default)
    return value


def counted(hands):
    """Раздачи с известным результатом (без последней незакрытой и без докупок)."""
    return [h for h in hands if h.get('result') in ('win', 'loss', 'push')]


def streak(hands):
    """Серия: сколько побед (или поражений) подряд идёт в конце.

    Считаем от последней сыгранной раздачи назад, пока результат тот же. Раздача
    «в ноль» серию обрывает — не победа же.
    """
    rows = counted(hands)
    if not rows:
        return {'kind': None, 'count': 0}
    kind = rows[-1]['result']
    if kind == 'push':
        return {'kind': None, 'count': 0}
    count = 0
    for hand in reversed(rows):
        if hand['result'] != kind:
            break
        count += 1
    return {'kind': kind, 'count': count}


def aggregate(hands, since=None, bb_value=BB_VALUE_DEFAULT):
    """Сводка по раздачам: сколько сыграно, выиграно, проиграно и на сколько фишек.

    since — datetime, с которого считаем (None = за всё время).
    """
    rows = counted(hands)
    if since is not None:
        rows = [h for h in rows if h['time'] is not None and h['time'] >= since]
    wins = [h for h in rows if h['result'] == 'win']
    losses = [h for h in rows if h['result'] == 'loss']
    total = len(rows)
    pl_bb = round(sum(h['delta_bb'] for h in rows), 2) if rows else 0.0
    best = max((h['delta_bb'] for h in wins), default=0.0)
    worst = min((h['delta_bb'] for h in losses), default=0.0)
    return {
        'hands': total,
        'wins': len(wins),
        'losses': len(losses),
        'pushes': total - len(wins) - len(losses),
        'win_pct': round(100.0 * len(wins) / total) if total else 0,
        'loss_pct': round(100.0 * len(losses) / total) if total else 0,
        'pl_bb': pl_bb,
        'pl_chips': to_chips(pl_bb, bb_value),
        'best_bb': best,
        'best_chips': to_chips(best, bb_value),
        'worst_bb': worst,
        'worst_chips': to_chips(worst, bb_value),
        'streak': streak(rows),
    }


def period_since(key, now=None, session_start=None):
    """Начало периода: с какого момента считать раздачи. None = без границы."""
    now = now or datetime.datetime.now()
    if key == 'session':
        return session_start
    if key == 'today':
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if key == 'week':
        return now - datetime.timedelta(days=7)
    if key == 'month':
        return now - datetime.timedelta(days=30)
    return None


def chart_points(hands, limit=100, bb_value=BB_VALUE_DEFAULT):
    """Точки графика стека: последние `limit` раздач, у которых стек известен."""
    rows = [h for h in hands if h.get('stack_in') is not None]
    if limit:
        rows = rows[-limit:]
    return [{
        'hand_id': h['hand_id'],
        'ts': h['ts'],
        'stack_bb': round(h['stack_in'], 1),
        'stack': to_chips(h['stack_in'], bb_value),
        'delta_bb': h['delta_bb'],
        'delta': to_chips(h['delta_bb'], bb_value),
        'result': h['result'],
    } for h in rows]


# карты решения, которые нужны панели для блока «живая раздача»
LIVE_KEYS = ('ts', 'hand_id', 'street', 'hole', 'board', 'position', 'players',
             'pot_bb', 'to_call_bb', 'stack_bb', 'action', 'amount_bb', 'reason',
             'made', 'made_note', 'pot_odds_pct', 'equity_pct')


def live_hand(hands, bb_value=BB_VALUE_DEFAULT):
    """Последнее решение бота — то, что показывает вкладка «Игра». Нет — None."""
    if not hands:
        return None
    last = hands[-1]['last']
    out = {k: last.get(k) for k in LIVE_KEYS}
    out['pot_chips'] = to_chips(last.get('pot_bb'), bb_value)
    out['to_call_chips'] = to_chips(last.get('to_call_bb'), bb_value)
    out['stack_chips'] = to_chips(last.get('stack_bb'), bb_value)
    out['amount_chips'] = to_chips(last.get('amount_bb'), bb_value)
    return out


def summary(hands, bb_value=BB_VALUE_DEFAULT, now=None, session_start=None,
            limit=100):
    """Всё, что нужно вкладке «Статистика»: периоды, график, последняя раздача."""
    bb_value = clean_bb_value(bb_value)
    now = now or datetime.datetime.now()
    periods = []
    for key, title in PERIODS:
        if key == 'session' and session_start is None:
            row = aggregate([], bb_value=bb_value)
            row['unknown'] = True        # панель ещё не запускала бота
        else:
            row = aggregate(hands, since=period_since(key, now, session_start),
                            bb_value=bb_value)
            row['unknown'] = False
        row.update(key=key, title=title, note=PERIOD_NOTES.get(key, ''))
        periods.append(row)
    return {
        'bb_value': bb_value,
        'periods': periods,
        'chart': chart_points(hands, limit, bb_value),
        'live': live_hand(hands, bb_value),
        'hands_total': len(hands),
        'hands_counted': len(counted(hands)),
    }


# ---------------------------------------------------------------------------
# кэш файла
# ---------------------------------------------------------------------------
class History:
    """hand_history.jsonl с кэшем: перечитываем, только если файл изменился.

    Панель опрашивается раз в 3 секунды, а история за месяц — это десятки тысяч
    строк; разбирать её на каждый запрос незачем. Ключ кэша — (mtime, размер):
    бот дописывает файл в конец, размер меняется всегда, даже когда таймер
    файловой системы грубоват.
    """

    def __init__(self, path=None):
        self.path = path or config.HAND_HISTORY
        self._key = 0                # заведомо не равен ни stat, ни None
        self._hands = []

    def _stamp(self):
        try:
            st = os.stat(self.path)
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def hands(self):
        stamp = self._stamp()
        if stamp != self._key:
            self._key = stamp
            self._hands = load_hands(self.path)
        return self._hands

    def summary(self, **kw):
        return summary(self.hands(), **kw)
