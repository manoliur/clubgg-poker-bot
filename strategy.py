#!/usr/bin/env python3
"""Табличная стратегия NLH без ИИ: префлоп по позиции, постфлоп по силе руки.

Вход — состояние стола (table_state.read_state), выход — решение:
    {'action': 'fold'|'check'|'call'|'raise', 'amount_bb': float|None, 'reason': str}

Префлоп: диапазоны стартовых рук по позициям (стиль ТАГ из strategy.md),
плюс формула Чена как запасная оценка, когда позиция не определена.
Постфлоп: категория руки (hand_evaluator.hand_class) + пот-оддсы и ауты.

Записи диапазонов: 'AA', 'AKs', 'AKo', '22+', 'ATs+', 'A5o+', '76s+'.
Знак '+' у коннекторов (разрыв 1) поднимает обе карты (76s+ = 76s,87s,...,KQs),
у остальных — только младшую (K9s+ = K9s,KTs,KJs,KQs).
"""
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
    """'22+, ATs+, KQs' -> множество кодов рук."""
    if isinstance(spec, (set, frozenset)):
        return spec
    out = set()
    for token in spec.split(','):
        out |= _expand_token(token)
    return out


_RANGE_CACHE = {}


def in_range(hole, spec):
    """Рука входит в диапазон?"""
    key = spec if isinstance(spec, str) else id(spec)
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


def range_for(position, players, kind='open'):
    """Диапазон для позиции: хедз-ап отдельный, иначе 6-max таблицы."""
    table = OPEN_RANGES if kind == 'open' else CALL_RANGES
    if players is not None and players <= 2:
        key = 'HU_SB' if position in (None, 'SB', 'BTN') else 'HU_BB'
        return table.get(key)
    if position in table:
        return table[position]
    return table.get('MP')                                  # неизвестная позиция — средняя


# --------------------------------------------------------------------------
# помощники
# --------------------------------------------------------------------------
def pot_odds(to_call_bb, pot_bb):
    """Цена колла: доля банка после колла. None, если чисел нет."""
    if not to_call_bb or pot_bb is None:
        return None
    total = pot_bb + to_call_bb
    return to_call_bb / total if total > 0 else None


def _d(action, reason, amount=None):
    return {'action': action, 'amount_bb': amount, 'reason': reason}


# --------------------------------------------------------------------------
# префлоп
# --------------------------------------------------------------------------
def decide_preflop(hole, position, players, has_bet, to_call_bb, pot_bb, stack_bb):
    code = hand_code(hole)
    hu = players is not None and players <= 2
    open_rng = range_for(position, players, 'open')
    call_rng = range_for(position, players, 'call')
    value3bet = THREE_BET_VALUE_HU if hu else THREE_BET_VALUE

    if not has_bet:
        # никто не поставил: открываемся рейзом или чекаем (мы в ББ)
        if in_range(hole, open_rng):
            return _d('raise', f'{code}: открытие с {position or "?"} '
                               f'(диапазон {"HU" if hu else "6-max"})', OPEN_SIZE_BB)
        return _d('check', f'{code}: вне диапазона открытия, но чек бесплатный')

    # перед нами ставка
    if in_range(hole, FOUR_BET):
        return _d('raise', f'{code}: премиум — 4-бет/олл-ин',
                  max(OPEN_SIZE_BB * 3, (to_call_bb or OPEN_SIZE_BB) * THREE_BET_MULT))
    if in_range(hole, value3bet):
        return _d('raise', f'{code}: 3-бет на велью',
                  (to_call_bb or OPEN_SIZE_BB) * THREE_BET_MULT)

    price = pot_odds(to_call_bb, pot_bb)
    if in_range(hole, call_rng):
        if to_call_bb is not None and to_call_bb > 0.20 * stack_bb and not in_range(hole, PREMIUM):
            return _d('fold', f'{code}: колл {to_call_bb}ББ — больше 20% стека, без премиума')
        # префлоп цена колла обычно 30-40% банка — это нормально (имплайд-оддсы),
        # отказываемся только от совсем безнадёжной цены
        if price is not None and price > 0.45 and not in_range(hole, PREMIUM):
            return _d('fold', f'{code}: цена {price:.0%} банка слишком высока')
        return _d('call', f'{code}: колл по диапазону {position or "?"}')
    return _d('fold', f'{code}: вне диапазона на {position or "?"}')


# --------------------------------------------------------------------------
# постфлоп
# --------------------------------------------------------------------------
def decide_postflop(hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb, players):
    cls = he.hand_class(hole, board)
    made, outs, draws = cls['made'], cls['outs'], cls['draws']
    name = cls['name'] or '?'
    price = pot_odds(to_call_bb, pot_bb)
    equity = he.equity_from_outs(outs, street)
    hu = players is not None and players <= 2

    if has_bet:
        if made in ('nuts', 'strong'):
            return _d('raise', f'{name}: рейз на велью', _bet_size(pot_bb, CBET_POT * 1.5))
        if made == 'medium':
            if price is not None and price > 0.4:
                return _d('fold', f'{name}: цена {price:.0%} банка слишком высока для средней руки')
            return _d('call', f'{name}: колл со средней рукой')
        if made == 'draw' or draws:
            if price is None:
                # чисел нет: считаем ставку полубанком (цена ~33%)
                return (_d('call', f'дро {draws}, {outs} аутов (~{equity:.0%}) — колл по аутам')
                        if equity >= 0.33 else _d('fold', f'дро {draws}: {outs} аутов мало'))
            if equity > price:
                return _d('call', f'дро {draws}: {outs} аутов ~{equity:.0%} > цены {price:.0%}')
            if street == 'flop' and 'flush' in draws and hu:
                return _d('raise', f'сильное дро {draws}: полу-блеф',
                          _bet_size(pot_bb, SEMI_BLUFF_POT))
            return _d('fold', f'дро {draws}: {equity:.0%} < цены {price:.0%}')
        if price is not None and price < 0.12 and street != 'river':
            return _d('call', f'{name}: дёшево ({price:.0%}) — смотрим следующую карту')
        return _d('fold', f'{name}: нечем продолжать')

    # нам чекнули / мы первые
    if made in ('nuts', 'strong'):
        return _d('raise', f'{name}: ставка на велью', _bet_size(pot_bb, CBET_POT))
    if made == 'medium':
        if street == 'flop':
            return _d('raise', f'{name}: конт-бет', _bet_size(pot_bb, CBET_POT * 0.8))
        return _d('check', f'{name}: контроль банка на {street}')
    if made == 'draw' or draws:
        if street in ('flop', 'turn') and hu:
            return _d('raise', f'дро {draws} ({outs} аутов): полу-блеф',
                      _bet_size(pot_bb, SEMI_BLUFF_POT))
        return _d('check', f'дро {draws}: смотрим карту бесплатно')
    if street == 'flop' and hu:
        return _d('raise', 'воздух: конт-бет один раз в хедз-апе',
                  _bet_size(pot_bb, SEMI_BLUFF_POT))
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


def decide(state, profile=None, stack_bb=100.0):
    """Главная функция: состояние стола -> решение.

    Ожидает ключи state: hole, board, street, has_bet, to_call_bb, pot_bb,
    position, players. Неизвестные числа (None) допустимы.
    """
    hole = [c for c in (state.get('hole') or []) if c]
    board = [c for c in (state.get('board') or []) if c]
    has_bet = bool(state.get('has_bet'))
    if len(hole) != 2:
        return _d('check' if not has_bet else 'fold', 'карты не распознаны — безопасное действие')

    street = state.get('street') or ('preflop' if not board else 'unknown')
    to_call, pot = state.get('to_call_bb'), state.get('pot_bb')
    players, position = state.get('players'), state.get('position')

    try:
        if street == 'preflop' or not board:
            decision = decide_preflop(hole, position, players, has_bet, to_call, pot, stack_bb)
            made = 'preflop'
        else:
            decision = decide_postflop(hole, board, street, has_bet, to_call, pot,
                                       stack_bb, players)
            made = he.hand_class(hole, board)['made']
    except (he.BadCard, ValueError) as e:
        return _d('check' if not has_bet else 'fold', f'ошибка разбора карт: {e}')

    decision = adjust_for_opponent(decision, profile, made)
    # чек невозможен, когда перед нами ставка, и наоборот
    if has_bet and decision['action'] == 'check':
        decision = _d('fold', decision['reason'] + ' (чек невозможен — есть ставка)')
    if not has_bet and decision['action'] == 'call':
        decision = _d('check', decision['reason'] + ' (ставки нет — чек)')
    return decision


if __name__ == '__main__':
    import sys
    import json
    demo = {'hole': sys.argv[1:3] or ['Ah', 'Kd'], 'board': sys.argv[3:], 'players': 2,
            'position': 'SB', 'has_bet': False, 'pot_bb': 3.0, 'to_call_bb': None}
    demo['street'] = {0: 'preflop', 3: 'flop', 4: 'turn', 5: 'river'}.get(len(demo['board']),
                                                                         'preflop')
    print(json.dumps(decide(demo), ensure_ascii=False, indent=2))
