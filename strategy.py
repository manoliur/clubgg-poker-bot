#!/usr/bin/env python3
"""Табличная стратегия NLH без ИИ: префлоп по позиции, постфлоп по силе руки.

Вход — состояние стола (table_state.read_state), выход — решение:
    {'action': 'fold'|'check'|'call'|'raise', 'amount_bb': float|None, 'reason': str,
     'pot_frac': float|None}   # доля банка у ставки — по ней бот выбирает пресет

Префлоп: диапазоны стартовых рук по позициям (стиль ТАГ из strategy.md),
плюс формула Чена как запасная оценка, когда позиция не определена.
Постфлоп: категория руки (hand_evaluator.hand_class) + пот-оддсы и ауты.

Записи диапазонов: 'AA', 'AKs', 'AKo', '22+', 'ATs+', 'A5o+', '76s+'.
Знак '+' у коннекторов (разрыв 1) поднимает обе карты (76s+ = 76s,87s,...,KQs),
у остальных — только младшую (K9s+ = K9s,KTs,KJs,KQs).

Чарты
-----
Диапазоны и размеры ставок можно не править в коде, а подгружать файлом:
    python strategy.py --load-chart charts/6max_standard.json AhKd
    strategy.use_chart(strategy.load_chart('charts/6max_tight.json'))
Формат — JSON или CSV, см. README.md. Встроенные таблицы ниже остаются чартом
по умолчанию, файл переопределяет только те ключи, что в нём есть.
"""
import json
import os

import hand_evaluator as he
from hand_evaluator import RANK_VALUE, VALUE_RANK

# --------------------------------------------------------------------------
# диапазоны
# --------------------------------------------------------------------------
OPEN_RANGES = {
    'UTG':  '22+, ATs+, AJo+, KQs, QJs',
    'UTG+1': '22+, ATs+, AJo+, KQs, QJs, JTs',
    'MP':   '22+, A9s+, ATo+, KTs+, QTs+, JTs',
    'MP+1': '22+, A9s+, ATo+, KTs+, QTs+, JTs, T9s',
    'HJ':   '22+, A8s+, ATo+, KTs+, QTs+, JTs, T9s, 98s',
    'CO':   '22+, A7s+, A9o+, K9s+, KJo+, Q9s+, QJo, J9s+, T9s, 98s',
    'BTN':  '22+, A2s+, A8o+, K8s+, KTo+, Q8s+, QTo+, J8s+, JTo, T8s+, 97s+, 87s, 76s, 65s',
    'SB':   '22+, A2s+, A7o+, K7s+, K9o+, Q8s+, QTo+, J8s+, JTo, T8s+, 97s+, 87s, 76s, 65s',
    'BB':   '22+, A2s+, A7o+, K7s+, K9o+, Q8s+, QTo+, J8s+, T8s+, 97s+, 87s, 76s',
}

# Хедз-ап: играем заметно шире (см. strategy.md — «против одного оппонента»)
OPEN_RANGES['HU_SB'] = ('22+, A2s+, A2o+, K2s+, K5o+, Q4s+, Q8o+, J6s+, J8o+, '
                        'T6s+, T8o+, 96s+, 98o, 85s+, 75s+, 64s+, 54s, 43s')
OPEN_RANGES['HU_BB'] = ('22+, A2s+, A2o+, K2s+, K4o+, Q4s+, Q7o+, J5s+, J8o+, '
                        'T6s+, T8o+, 95s+, 97o+, 85s+, 74s+, 64s+, 53s+, 43s')

# Коллы против рейза (без 3-бета)
CALL_RANGES = {
    'UTG':  '22+, AQs+, AKo, KQs',
    'UTG+1': '22+, AJs+, AQo+, KQs',
    'MP':   '22+, ATs+, AQo+, KJs+, QJs',
    'MP+1': '22+, ATs+, AQo+, KJs+, QJs',
    'HJ':   '22+, A9s+, AJo+, KTs+, QJs, JTs',
    'CO':   '22+, A8s+, AJo+, KTs+, QTs+, JTs, T9s',
    'BTN':  '22+, A5s+, ATo+, K9s+, KQo, Q9s+, J9s+, T9s, 98s, 87s',
    'SB':   '22+, A8s+, ATo+, KTs+, KQo, QTs+, JTs, T9s',
    'BB':   '22+, A2s+, A9o+, K8s+, KTo+, Q9s+, QJo, J9s+, T9s, 98s, 87s, 76s, 65s',
    'HU_SB': '22+, A2s+, A5o+, K5s+, K8o+, Q7s+, QTo+, J8s+, JTo, T8s+, 98s, 87s, 76s',
    'HU_BB': ('22+, A2s+, A2o+, K3s+, K7o+, Q6s+, Q9o+, J7s+, J9o+, T7s+, T9o, '
              '96s+, 86s+, 75s+, 65s, 54s'),
}

# 3-бет на велью
THREE_BET_VALUE = 'TT+, AQs+, AKo'
THREE_BET_VALUE_HU = '77+, ATs+, AQo+, KQs'
# 4-бет / олл-ин на префлопе
FOUR_BET = 'QQ+, AKs, AKo'

PREMIUM = 'JJ+, AQs+, AKo'

OPEN_SIZE_BB = 2.5          # открытие рейзом
THREE_BET_MULT = 3.0        # 3-бет = 3x предыдущего рейза
CBET_POT = 0.6              # ставка на велью, доля банка
SEMI_BLUFF_POT = 0.45       # полу-блеф с дро

# Настройки розыгрыша (их переопределяет секция "postflop" чарта).
DEFAULT_SETTINGS = {
    'open_size_bb': OPEN_SIZE_BB,
    'three_bet_mult': THREE_BET_MULT,
    'cbet_pot': CBET_POT,
    'semi_bluff_pot': SEMI_BLUFF_POT,
    'aggression': 1.0,          # общий множитель размеров ставок
    'medium_max_price': 0.40,   # дороже — средняя рука пасует
    'draw_min_equity': 0.33,    # эквити дро, при котором коллим вслепую
    'cheap_price': 0.12,        # цена, за которую смотрим следующую карту с чем угодно
    'max_call_stack_frac': 0.20,  # префлоп-колл дороже доли стека — только с премиумом
    'preflop_max_price': 0.45,  # префлоп: цена колла в долях банка
}


# --------------------------------------------------------------------------
# разбор рук и диапазонов
# --------------------------------------------------------------------------
def hand_code(hole):
    """['Ah','Kd'] -> 'AKo'; ['Ah','Kh'] -> 'AKs'; ['7h','7c'] -> '77'."""
    parsed = he.parse_cards(hole)
    if len(parsed) != 2:
        raise ValueError(f'нужно 2 карманные карты, а не {hole}')
    (v1, s1), (v2, s2) = parsed
    if v1 < v2:
        (v1, s1), (v2, s2) = (v2, s2), (v1, s1)
    if v1 == v2:
        return VALUE_RANK[v1] * 2
    return f'{VALUE_RANK[v1]}{VALUE_RANK[v2]}{"s" if s1 == s2 else "o"}'


def _expand_token(token):
    """Один элемент диапазона -> множество кодов рук."""
    t = token.strip()
    if not t:
        return set()
    plus = t.endswith('+')
    if plus:
        t = t[:-1]
    if any(c.upper() not in RANK_VALUE for c in t[:2]):
        raise ValueError(f'непонятная запись руки: {token!r}')
    if len(t) == 2 and t[0] == t[1]:                       # пара
        v = RANK_VALUE[t[0].upper()]
        vals = range(v, 15) if plus else [v]
        return {VALUE_RANK[x] * 2 for x in vals}
    if len(t) != 3 or t[2].lower() not in 'so':
        raise ValueError(f'непонятная запись руки: {token!r}')
    hi, lo, suf = RANK_VALUE[t[0].upper()], RANK_VALUE[t[1].upper()], t[2].lower()
    if hi < lo:
        hi, lo = lo, hi
    if not plus:
        return {f'{VALUE_RANK[hi]}{VALUE_RANK[lo]}{suf}'}
    out = set()
    if hi - lo == 1:                                        # коннекторы: растут обе карты
        while hi <= 14:
            out.add(f'{VALUE_RANK[hi]}{VALUE_RANK[lo]}{suf}')
            hi, lo = hi + 1, lo + 1
    else:                                                   # старшая фиксирована
        for v in range(lo, hi):
            out.add(f'{VALUE_RANK[hi]}{VALUE_RANK[v]}{suf}')
    return out


def parse_range(spec):
    """'22+, ATs+, KQs' или ['AA','AKs'] -> множество кодов рук."""
    if isinstance(spec, (set, frozenset)):
        return spec
    tokens = spec if isinstance(spec, (list, tuple)) else spec.split(',')
    out = set()
    for token in tokens:
        out |= _expand_token(token)
    return out


_RANGE_CACHE = {}


def _cache_key(spec):
    if isinstance(spec, str):
        return spec
    return frozenset(spec)


def in_range(hole, spec):
    """Рука входит в диапазон?"""
    if not spec:
        return False
    key = _cache_key(spec)
    rng = _RANGE_CACHE.get(key)
    if rng is None:
        rng = _RANGE_CACHE[key] = parse_range(spec)
    return hand_code(hole) in rng


def chen_score(hole):
    """Формула Чена — запасная оценка стартовой руки (когда позиция неизвестна)."""
    (v1, s1), (v2, s2) = he.parse_cards(hole)
    hi, lo = max(v1, v2), min(v1, v2)
    base = {14: 10, 13: 8, 12: 7, 11: 6}.get(hi, hi / 2)
    score = base
    if hi == lo:
        score = max(5, base * 2)
    if s1 == s2:
        score += 2
    gap = hi - lo - 1
    score -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
    if gap <= 1 and hi < 12 and hi != lo:
        score += 1                                          # бонус за возможность стрита
    return round(score * 2) / 2


# --------------------------------------------------------------------------
# чарты: загружаемые наборы диапазонов и настроек
# --------------------------------------------------------------------------
def _pos_key(name):
    """'UTG+1' -> 'utg1', 'HU_SB' -> 'husb' — ключ позиции, нечувствительный к записи."""
    return ''.join(c for c in str(name).lower() if c.isalnum())


def _pos_table(src):
    """{'UTG': '22+', ...} -> {'utg': '22+', ...} с проверкой записей."""
    out = {}
    for pos, spec in (src or {}).items():
        key = _pos_key(pos)
        parse_range(spec)                                   # упасть сразу на кривой записи
        out[key] = spec
    return out


class Chart:
    """Набор правил: диапазоны по позициям + размеры ставок.

    Всё, чего в файле нет, берётся из встроенных таблиц, поэтому чарт может
    переопределять хоть одну позицию.
    """

    def __init__(self, data=None, name=None, path=None):
        data = data or {}
        self.path = path
        self.name = name or data.get('name') or (os.path.basename(path) if path
                                                 else 'встроенный')
        self.open = {**_pos_table(OPEN_RANGES), **_pos_table(data.get('open'))}
        self.call = {**_pos_table(CALL_RANGES), **_pos_table(data.get('call'))}
        self.three_bet = data.get('three_bet', THREE_BET_VALUE)
        self.three_bet_hu = data.get('three_bet_hu', THREE_BET_VALUE_HU)
        self.four_bet = data.get('four_bet', FOUR_BET)
        self.premium = data.get('premium', PREMIUM)
        self.settings = {**DEFAULT_SETTINGS, **(data.get('postflop') or {})}
        for spec in (self.three_bet, self.three_bet_hu, self.four_bet, self.premium):
            parse_range(spec)

    def range_for(self, position, players, kind='open'):
        """Диапазон для позиции: хедз-ап отдельный, иначе 6-max таблицы.

        players здесь — число СИДЯЩИХ за столом (players_seated), а не число в
        раздаче: на столе 4-max с одним сфолдившим всё равно 6-max-таблица,
        а не HU (раньше бот играл HU_SB против 3-4-max столов — живой тест).
        """
        table = self.open if kind == 'open' else self.call
        if players is not None and players <= 2:
            key = 'husb' if position in (None, 'SB', 'BTN') else 'hubb'
            return table.get(key)
        return table.get(_pos_key(position)) or table.get('mp')   # неизвестная — средняя

    def size(self, key):
        """Размер ставки с учётом общей агрессии чарта."""
        return self.settings[key] * self.settings['aggression']

    def describe(self):
        lines = [f'чарт: {self.name}' + (f' ({self.path})' if self.path else '')]
        for kind, table in (('открытие', self.open), ('колл', self.call)):
            for pos in ('utg', 'utg1', 'mp', 'mp1', 'hj', 'co', 'btn', 'sb', 'bb',
                        'husb', 'hubb'):
                if pos in table:
                    lines.append(f'  {kind:9} {pos.upper():5} {table[pos]}')
        lines.append(f'  3-бет     {self.three_bet} | HU {self.three_bet_hu}')
        lines.append(f'  4-бет     {self.four_bet}')
        lines.append('  размеры   ' + ', '.join(f'{k}={v}' for k, v in self.settings.items()))
        return '\n'.join(lines)


DEFAULT_CHART = Chart()
_ACTIVE = DEFAULT_CHART


def active_chart():
    return _ACTIVE


def use_chart(chart):
    """Сделать чарт активным для всех последующих решений. Возвращает прежний."""
    global _ACTIVE
    prev, _ACTIVE = _ACTIVE, chart or DEFAULT_CHART
    return prev


def _chart_from_csv(text):
    """CSV: строки «позиция;рука рука рука» (разделитель , ; или таб).

    Секции open/call задаются первым столбцом «open»/«call»; без него всё
    считается диапазонами открытия.
    """
    data = {'open': {}, 'call': {}}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        parts = [p.strip() for p in line.replace('\t', ';').replace(',', ';').split(';')]
        parts = [p for p in parts if p]
        kind = 'open'
        if parts and parts[0].lower() in ('open', 'call'):
            kind, parts = parts[0].lower(), parts[1:]
        if len(parts) < 2:
            continue
        if parts[0].lower() in ('position', 'позиция'):
            continue
        data[kind][parts[0]] = parts[1:]
    return data


def load_chart(path):
    """Прочитать чарт из JSON или CSV. Кидает ValueError на кривом файле."""
    with open(path, encoding='utf-8') as f:
        text = f.read()
    if path.lower().endswith('.csv'):
        data = _chart_from_csv(text)
    else:
        try:
            data = json.loads(text)
        except ValueError as e:
            raise ValueError(f'{path}: не разбирается JSON: {e}') from e
    if not isinstance(data, dict):
        raise ValueError(f'{path}: ожидался объект с ключами open/call/postflop')
    # чарт без секций open/call — это плоская таблица только для открытия
    if not any(k in data for k in ('open', 'call', 'postflop', 'three_bet', 'four_bet')):
        data = {'open': data}
    try:
        return Chart(data, path=path)
    except ValueError as e:
        raise ValueError(f'{path}: {e}') from e


def range_for(position, players, kind='open', chart=None):
    """Диапазон для позиции по активному (или переданному) чарту."""
    return (chart or _ACTIVE).range_for(position, players, kind)


# --------------------------------------------------------------------------
# помощники
# --------------------------------------------------------------------------
def pot_odds(to_call_bb, pot_bb):
    """Цена колла: доля банка после колла. None, если чисел нет."""
    if not to_call_bb or pot_bb is None:
        return None
    total = pot_bb + to_call_bb
    return to_call_bb / total if total > 0 else None


def _d(action, reason, amount=None, pot_frac=None):
    return {'action': action, 'amount_bb': amount, 'reason': reason, 'pot_frac': pot_frac}


def _raise_pot(reason, pot_bb, fraction):
    """Ставка/рейз размером в долю банка: кроме суммы в ББ отдаём саму долю.

    Долю просит главный цикл: размер ставки в клиенте задаётся не числом, а
    выбором пресета в правом столбце (33/50/75/100% банка). Сумма в ББ считается
    только при известном банке (эталонов цифр может не быть вовсе), а доля
    известна всегда — по ней и выбирается пресет.
    """
    return _d('raise', reason, _bet_size(pot_bb, fraction), pot_frac=fraction)


# --------------------------------------------------------------------------
# префлоп
# --------------------------------------------------------------------------
def _versus(no_raise, to_call_bb, stack_bb):
    """Как назвать ставку, на которую отвечаем: алл-ин или просто крупная."""
    if no_raise:
        return 'против алл-ина'
    part = f' ({to_call_bb / stack_bb:.0%} стека)' if to_call_bb and stack_bb else ''
    return f'против крупной ставки {to_call_bb}ББ{part}'


def decide_preflop(hole, position, players, has_bet, to_call_bb, pot_bb, stack_bb, chart=None,
                   no_raise=False):
    """Решение на префлопе. no_raise — рейз недоступен (оппонент в алл-ине).

    Когда рейзить нечем или ставка перед нами размером с полстека, чарты 3-бета
    и 4-бета не применяются: вопрос уже не «повышать ли», а окупается ли колл.
    """
    chart = chart or _ACTIVE
    st = chart.settings
    code = hand_code(hole)
    hu = players is not None and players <= 2
    open_rng = chart.range_for(position, players, 'open')
    call_rng = chart.range_for(position, players, 'call')
    value3bet = chart.three_bet_hu if hu else chart.three_bet
    open_size = chart.size('open_size_bb')
    mult = st['three_bet_mult']

    if not has_bet:
        # никто не поставил: открываемся рейзом или чекаем (мы в ББ)
        if no_raise:
            return _d('check', f'{code}: ставить нечем (живого пресета нет) — чек')
        if in_range(hole, open_rng):
            return _d('raise', f'{code}: открытие с {position or "?"} '
                               f'(диапазон {"HU" if hu else "6-max"}, {chart.name})', open_size)
        return _d('check', f'{code}: вне диапазона открытия, но чек бесплатный')

    # перед нами ставка
    cap = st['max_call_stack_frac']
    premium = in_range(hole, chart.premium)
    price = pot_odds(to_call_bb, pot_bb)
    # Ставка больше cap стека — это уже алл-ин или 4-бет: поднимать её «на велью»
    # по чарту нельзя. Живая раздача 19.08 09:52: 76s ответила 3-бетом на
    # алл-ин, рейзить было нечем, и тап выродился в колл 23.7ББ (34% стека).
    big = to_call_bb is not None and stack_bb and to_call_bb > cap * stack_bb
    if not no_raise and not (big and not premium):
        if in_range(hole, chart.four_bet):
            return _d('raise', f'{code}: премиум — 4-бет/олл-ин',
                      max(open_size * 3, (to_call_bb or open_size) * mult))
        if in_range(hole, value3bet):
            return _d('raise', f'{code}: 3-бет на велью', (to_call_bb or open_size) * mult)
    else:
        # Рейза не будет — руки, которыми мы бы повысили, уходят в колл: они
        # сильнее тех, которыми мы просто уравниваем. Без этого AKo пасовал бы
        # против алл-ина: в GTO-чарте диапазон колла на BTN — «77, 88, ATs»,
        # всё остальное сильное лежит в 3-бете и 4-бете.
        call_rng = (parse_range(call_rng) | parse_range(value3bet)
                    | parse_range(chart.four_bet))

    versus = _versus(no_raise, to_call_bb, stack_bb) if (big or no_raise) else ''
    if in_range(hole, call_rng):
        if big and not premium:
            return _d('fold', f'{code}: фолд {versus} — колл {to_call_bb}ББ больше '
                              f'{cap:.0%} стека, без премиума')
        # префлоп цена колла обычно 30-40% банка — это нормально (имплайд-оддсы),
        # отказываемся только от совсем безнадёжной цены
        if price is not None and price > st['preflop_max_price'] and not premium:
            return _d('fold', f'{code}: цена {price:.0%} банка слишком высока')
        if versus:
            odds = f' по пот-оддсам {price:.0%}' if price is not None else ''
            return _d('call', f'{code}: колл {to_call_bb}ББ {versus}{odds}')
        return _d('call', f'{code}: колл по диапазону {position or "?"}')
    return _d('fold', f'{code}: вне диапазона на {position or "?"}'
              + (f' — фолд {versus}' if versus else ''))


# --------------------------------------------------------------------------
# постфлоп
# --------------------------------------------------------------------------
def decide_postflop(hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb, players,
                    chart=None, no_raise=False):
    """Решение после флопа. no_raise — рейз/ставка недоступны (живого пресета нет).

    Тогда сильная рука коллит вместо рейза, а полу-блеф отменяется: блефовать
    коллом нельзя, и решение остаётся за пот-оддсами.
    """
    chart = chart or _ACTIVE
    st = chart.settings
    cls = he.hand_class(hole, board)
    made, outs, draws = cls['made'], cls['outs'], cls['draws']
    name = cls['name'] or '?'
    price = pot_odds(to_call_bb, pot_bb)
    equity = he.equity_from_outs(outs, street)
    hu = players is not None and players <= 2
    cbet, semi = chart.size('cbet_pot'), chart.size('semi_bluff_pot')

    if has_bet:
        if made in ('nuts', 'strong'):
            if no_raise:
                price_txt = f' по пот-оддсам {price:.0%}' if price is not None else ''
                return _d('call', f'{name}: колл против алл-ина{price_txt} '
                                  '(рейз недоступен)')
            return _raise_pot(f'{name}: рейз на велью', pot_bb, cbet * 1.5)
        if made == 'medium':
            if price is not None and price > st['medium_max_price']:
                return _d('fold', f'{name}: цена {price:.0%} банка слишком высока для средней руки')
            return _d('call', f'{name}: колл со средней рукой')
        if made == 'draw' or draws:
            if price is None:
                # чисел нет: считаем ставку полубанком (цена ~33%)
                return (_d('call', f'дро {draws}, {outs} аутов (~{equity:.0%}) — колл по аутам')
                        if equity >= st['draw_min_equity']
                        else _d('fold', f'дро {draws}: {outs} аутов мало'))
            if equity > price:
                return _d('call', f'дро {draws}: {outs} аутов ~{equity:.0%} > цены {price:.0%}')
            if street == 'flop' and 'flush' in draws and hu and not no_raise:
                return _raise_pot(f'сильное дро {draws}: полу-блеф', pot_bb, semi)
            return _d('fold', f'дро {draws}: {equity:.0%} < цены {price:.0%}')
        if price is not None and price < st['cheap_price'] and street != 'river':
            return _d('call', f'{name}: дёшево ({price:.0%}) — смотрим следующую карту')
        return _d('fold', f'{name}: нечем продолжать')

    # нам чекнули / мы первые
    if no_raise:
        return _d('check', f'{name}: ставить нечем (живого пресета нет) — чек')
    if made in ('nuts', 'strong'):
        return _raise_pot(f'{name}: ставка на велью', pot_bb, cbet)
    if made == 'medium':
        if street == 'flop':
            return _raise_pot(f'{name}: конт-бет', pot_bb, cbet * 0.8)
        return _d('check', f'{name}: контроль банка на {street}')
    if made == 'draw' or draws:
        if street in ('flop', 'turn') and hu:
            return _raise_pot(f'дро {draws} ({outs} аутов): полу-блеф', pot_bb, semi)
        return _d('check', f'дро {draws}: смотрим карту бесплатно')
    if street == 'flop' and hu:
        return _raise_pot('воздух: конт-бет один раз в хедз-апе', pot_bb, semi)
    return _d('check', f'{name}: чек')


def _bet_size(pot_bb, fraction):
    if pot_bb is None:
        return None
    return round(pot_bb * fraction, 1)


# --------------------------------------------------------------------------
# адаптация под оппонента и общий вход
# --------------------------------------------------------------------------
def adjust_for_opponent(decision, profile, made):
    """Правки по players.json: против лузово-пассивных не блефуем, против тайтовых — чаще."""
    if not profile:
        return decision
    vpip = profile.get('vpip') or 0
    agg = profile.get('agg') or 0
    if decision['action'] == 'raise' and made in ('air', 'draw') and vpip > 0.40:
        return _d('check', decision['reason'] + ' -> оппонент лузовый, блеф не проходит',
                  None)
    if decision['action'] == 'fold' and made == 'medium' and agg > 2.5:
        return _d('call', decision['reason'] + ' -> оппонент агрессивный, коллим шире')
    return decision


def decide(state, profile=None, stack_bb=100.0, chart=None):
    """Главная функция: состояние стола -> решение.

    Ожидает ключи state: hole, board, street, has_bet, to_call_bb, pot_bb,
    position, players. Неизвестные числа (None) допустимы. chart — загруженный
    набор правил (по умолчанию активный, см. use_chart).

    state['no_raise'] — рейз в клиенте недоступен (оппонент в алл-ине, живых
    пресетов ставки нет). Тогда решение принимается только между коллом и
    фолдом: раньше главный цикл молча подменял несостоявшийся рейз коллом, и
    76s «3-бет на велью» превращался в колл 23.7ББ против алл-ина (19.08 09:52).
    """
    chart = chart or _ACTIVE
    hole = [c for c in (state.get('hole') or []) if c]
    board = [c for c in (state.get('board') or []) if c]
    has_bet = bool(state.get('has_bet'))
    if len(hole) != 2:
        return _d('check' if not has_bet else 'fold', 'карты не распознаны — безопасное действие')
    # В холдеме доска — 0/3/4/5 карт. 1-2 или >5 значит, что карту не прочитали:
    # считать силу руки по неполной доске опасно (можно не увидеть флеш/стрит).
    if len(board) in (1, 2) or len(board) > 5:
        return _d('check' if not has_bet else 'fold',
                  f'доска прочитана не полностью ({len(board)} карт) — безопасное действие')

    street = state.get('street') or ('preflop' if not board else 'unknown')
    to_call, pot = state.get('to_call_bb'), state.get('pot_bb')
    players = state.get('players')                       # в раздаче (постфлоп)
    seated = state.get('players_seated') or players      # сидят за столом (префлоп)
    position = state.get('position')

    no_raise = bool(state.get('no_raise'))

    try:
        if street == 'preflop' or not board:
            decision = decide_preflop(hole, position, seated, has_bet, to_call, pot,
                                      stack_bb, chart, no_raise=no_raise)
            made = 'preflop'
        else:
            decision = decide_postflop(hole, board, street, has_bet, to_call, pot,
                                       stack_bb, players, chart, no_raise=no_raise)
            made = he.hand_class(hole, board)['made']
    except (he.BadCard, ValueError) as e:
        return _d('check' if not has_bet else 'fold', f'ошибка разбора карт: {e}')

    decision = adjust_for_opponent(decision, profile, made)
    # чек невозможен, когда перед нами ставка, и наоборот
    if has_bet and decision['action'] == 'check':
        decision = _d('fold', decision['reason'] + ' (чек невозможен — есть ставка)')
    if not has_bet and decision['action'] == 'call':
        decision = _d('check', decision['reason'] + ' (ставки нет — чек)')
    if not has_bet and decision['action'] == 'fold':
        # без ставки кнопки «Фолд» нет: на её месте «Чек/Фолд», и тап туда
        # выбросил бы руку там, где ход бесплатный
        decision = _d('check', decision['reason'] + ' (ставки нет — фолдить незачем)')
    return decision


def main(argv=None):
    """CLI: загрузить чарт и/или принять решение по руке.

        python strategy.py --load-chart charts/6max_standard.json
        python strategy.py --load-chart charts/6max_tight.json Ah Kd Qc 7s 2d
        python strategy.py --position BTN --players 6 Ah Kd
    """
    import argparse
    ap = argparse.ArgumentParser(description='Табличная стратегия NLH (без ИИ)')
    ap.add_argument('--load-chart', help='файл чарта (JSON/CSV) из папки charts/')
    ap.add_argument('--position', default='SB', help='позиция героя (BTN/SB/BB/UTG/MP/CO)')
    ap.add_argument('--players', type=int, default=2, help='игроков в раздаче (2..6)')
    ap.add_argument('--pot', type=float, default=3.0, help='банк в ББ')
    ap.add_argument('--to-call', type=float, help='сумма колла в ББ (есть ставка)')
    ap.add_argument('cards', nargs='*', help='2 карманные карты, затем доска: Ah Kd 7c 2s 9d')
    args = ap.parse_args(argv)

    if args.load_chart:
        chart = load_chart(args.load_chart)
        use_chart(chart)
        print(chart.describe())
        if not args.cards:
            return 0

    cards = args.cards or ['Ah', 'Kd']
    demo = {'hole': cards[:2], 'board': cards[2:], 'players': args.players,
            'position': args.position, 'has_bet': args.to_call is not None,
            'pot_bb': args.pot, 'to_call_bb': args.to_call}
    demo['street'] = {0: 'preflop', 3: 'flop', 4: 'turn', 5: 'river'}.get(len(demo['board']),
                                                                         'preflop')
    print(json.dumps(decide(demo), ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
