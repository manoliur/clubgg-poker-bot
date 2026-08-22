#!/usr/bin/env python3
"""Оценка силы покерной руки (Texas Hold'em), без ИИ.

Из 5-7 карт находит лучшую пятикарточную комбинацию и возвращает сравнимый
кортеж: (категория, тайбрейкеры...). Больше — лучше, кортежи сравниваются
напрямую, поэтому руки можно сортировать и сравнивать через >, ==, max().

    >>> evaluate(['Ah', 'Kh', 'Qh', 'Jh', 'Th'])[0] == STRAIGHT_FLUSH
    True
    >>> evaluate(['As','Ad','Kc','Kd','2h']) > evaluate(['As','Ad','Qc','Qd','Jh'])
    True

Дополнительно считает дро (флеш-дро, стрит-дро) и аутсы — это нужно стратегии.
"""
from itertools import combinations

RANKS = '23456789TJQKA'
SUITS = 'hdcs'
RANK_VALUE = {r: i + 2 for i, r in enumerate(RANKS)}   # '2'->2 ... 'A'->14
VALUE_RANK = {v: r for r, v in RANK_VALUE.items()}

HIGH_CARD, PAIR, TWO_PAIR, TRIPS, STRAIGHT, FLUSH, FULL_HOUSE, QUADS, STRAIGHT_FLUSH = range(9)

CATEGORY_NAMES = {
    HIGH_CARD: 'старшая карта',
    PAIR: 'пара',
    TWO_PAIR: 'две пары',
    TRIPS: 'сет/трипс',
    STRAIGHT: 'стрит',
    FLUSH: 'флеш',
    FULL_HOUSE: 'фулл-хаус',
    QUADS: 'каре',
    STRAIGHT_FLUSH: 'стрит-флеш',
}


class BadCard(ValueError):
    pass


def parse_card(card):
    """'Ah' -> (14, 'h'). Регистр ранга не важен, 't'/'10' = десятка."""
    if not isinstance(card, str):
        raise BadCard(f'не карта: {card!r}')
    c = card.strip()
    if len(c) == 3 and c[:2] == '10':
        c = 'T' + c[2]
    if len(c) != 2:
        raise BadCard(f'не карта: {card!r}')
    r, s = c[0].upper(), c[1].lower()
    if r not in RANK_VALUE or s not in SUITS:
        raise BadCard(f'не карта: {card!r}')
    return RANK_VALUE[r], s


def parse_cards(cards):
    return [parse_card(c) for c in cards if c]


def card_str(value, suit):
    return f'{VALUE_RANK[value]}{suit}'


def _straight_high(values):
    """Старшая карта стрита из набора значений (или None). Учитывает колесо A-2-3-4-5."""
    uniq = set(values)
    if 14 in uniq:
        uniq.add(1)
    best = None
    for high in range(14, 4, -1):
        if all(high - i in uniq for i in range(5)):
            best = high
            break
    return best


def evaluate5(cards):
    """Оценить ровно 5 карт. Возвращает кортеж (категория, тайбрейкеры...)."""
    parsed = cards if cards and isinstance(cards[0], tuple) else parse_cards(cards)
    if len(parsed) != 5:
        raise ValueError('нужно ровно 5 карт')
    values = sorted((v for v, _ in parsed), reverse=True)
    suits = [s for _, s in parsed]
    is_flush = len(set(suits)) == 1
    st_high = _straight_high(values)

    counts = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    # группы: сначала по количеству, потом по старшинству
    groups = sorted(counts.items(), key=lambda kv: (-kv[1], -kv[0]))
    shape = [c for _, c in groups]
    ordered = [v for v, _ in groups]

    if is_flush and st_high:
        return (STRAIGHT_FLUSH, st_high)
    if shape[0] == 4:
        return (QUADS, ordered[0], ordered[1])
    if shape[:2] == [3, 2]:
        return (FULL_HOUSE, ordered[0], ordered[1])
    if is_flush:
        return (FLUSH, *values)
    if st_high:
        return (STRAIGHT, st_high)
    if shape[0] == 3:
        return (TRIPS, ordered[0], *sorted(ordered[1:], reverse=True))
    if shape[:2] == [2, 2]:
        pair_hi, pair_lo = sorted(ordered[:2], reverse=True)
        return (TWO_PAIR, pair_hi, pair_lo, ordered[2])
    if shape[0] == 2:
        return (PAIR, ordered[0], *sorted(ordered[1:], reverse=True))
    return (HIGH_CARD, *values)


def evaluate(cards):
    """Лучшая комбинация из 5-7 карт: кортеж (категория, тайбрейкеры...)."""
    parsed = parse_cards(cards)
    if len(parsed) < 5:
        raise ValueError('нужно минимум 5 карт')
    if len(set(parsed)) != len(parsed):
        raise BadCard(f'дубликаты карт: {cards}')
    if len(parsed) == 5:
        return evaluate5(parsed)
    return max(evaluate5(list(combo)) for combo in combinations(parsed, 5))


def best_five(cards):
    """Лучшие 5 карт из 5-7. Возвращает (кортеж_оценки, [карты])."""
    parsed = parse_cards(cards)
    if len(parsed) < 5:
        raise ValueError('нужно минимум 5 карт')
    best, best_combo = None, None
    for combo in combinations(parsed, 5):
        sc = evaluate5(list(combo))
        if best is None or sc > best:
            best, best_combo = sc, combo
    return best, [card_str(v, s) for v, s in best_combo]


def describe(score):
    """Человеческое название комбинации по кортежу оценки."""
    cat = score[0]
    name = CATEGORY_NAMES[cat]
    if cat in (PAIR, TRIPS, QUADS, FULL_HOUSE, TWO_PAIR):
        name += ' ' + VALUE_RANK[score[1]]
        if cat in (FULL_HOUSE, TWO_PAIR):
            name += '/' + VALUE_RANK[score[2]]
    elif cat in (STRAIGHT, STRAIGHT_FLUSH):
        name += ' до ' + VALUE_RANK[score[1]]
    elif cat in (FLUSH, HIGH_CARD):
        name += ' ' + VALUE_RANK[score[1]]
    return name


def compare(hand_a, hand_b):
    """1 если a сильнее, -1 если b сильнее, 0 при равенстве."""
    sa, sb = evaluate(hand_a), evaluate(hand_b)
    return (sa > sb) - (sa < sb)


# --------------------------------------------------------------------------
# дро и аутсы — нужны стратегии на флопе/тёрне
# --------------------------------------------------------------------------
def flush_draw(cards, hole=None):
    """4 карты одной масти (без готового флеша) -> масть, иначе None.

    hole — наши карманные карты. Если они заданы, дро засчитывается только с
    нашей картой этой масти: четыре пики на доске без пики в руке — это дро
    оппонента, мы с него ничего не получаем (нам такая карта банк не выигрывает).
    """
    parsed = parse_cards(cards)
    ours = {s for _, s in parse_cards(hole)} if hole is not None else None
    by_suit = {}
    for v, s in parsed:
        by_suit.setdefault(s, []).append(v)
    for s, vs in by_suit.items():
        if len(vs) == 4 and (ours is None or s in ours):
            return s
    return None


def _straight_outs(values, board_values=None):
    """Ранги, закрывающие стрит. Пустое множество, если стрит уже собран.

    board_values — значения доски: карта, которая даёт стрит одной доске, есть
    у всех за столом, нашим аутом она не считается (доска 5-6-7-8 и наши A-2:
    девятка «закрывает стрит», но он общий).
    """
    values = set(values)
    if _straight_high(values):
        return set()
    outs = set()
    for card in range(2, 15):
        if card in values:
            continue
        ours = _straight_high(values | {card})
        if not ours:
            continue
        if board_values is not None:
            theirs = _straight_high(set(board_values) | {card})
            if theirs and theirs >= ours:
                continue
        outs.add(card)
    return outs


def straight_draw(cards, board=None):
    """'open' (двусторонний, 8 аутов), 'gutshot' (4 аута) или None."""
    values = {v for v, _ in parse_cards(cards)}
    bvals = {v for v, _ in parse_cards(board)} if board is not None else None
    outs = _straight_outs(values, bvals)
    if len(outs) >= 2:
        return 'open'
    if len(outs) == 1:
        return 'gutshot'
    return None


def count_outs(hole, board):
    """Ауты на улучшение: сколько карт КОЛОДЫ даёт нам флеш или стрит.

    Считаются сами карты, а не «9 за флеш-дро + 8 за стрит-дро»: у комбо-дро
    часть карт закрывает и то, и другое (флеш-дро + двусторонний — 15 аутов, а
    не 17), а часть уже лежит на столе. Карманной паре добавляем 2 аута на сет.
    """
    hp, bp = parse_cards(hole), parse_cards(board)
    known = set(hp + bp)
    values = {v for v, _ in known}
    board_vals = {v for v, _ in bp}
    suit = flush_draw(list(hole) + list(board), hole=hole)
    straight = _straight_outs(values, board_vals if bp else None)
    outs = 0
    for v in range(2, 15):
        for s in SUITS:
            if (v, s) in known:
                continue
            if (suit is not None and s == suit) or v in straight:
                outs += 1
    if len(hp) == 2 and hp[0][0] == hp[1][0] and hp[0][0] not in board_vals:
        outs += 2                           # карманная пара -> сет
    return outs


def equity_from_outs(outs, street):
    """Правило 2-4: примерная доля улучшения к риверу."""
    if street == 'flop':
        return min(0.95, outs * 4 / 100)
    if street == 'turn':
        return min(0.95, outs * 2 / 100)
    return 0.0


# --------------------------------------------------------------------------
# опасность доски
# --------------------------------------------------------------------------
# Насколько доска сама по себе угрожает нашей одной паре или двум парам. Считается
# по трём простым признакам — таблицами, без перебора чужих рук:
#
#   масть      3 карты одной масти -> флеш возможен, 4+ -> максимум риска;
#   связность  сколькими пятёрками подряд можно собрать стрит (окна рангов);
#   спаренность пара на доске бьёт нашу пару трипсом, две пары/трипс — тем более.
#
# Числа подобраны так, чтобы «сухая» доска (K-7-2 разномастная) давала 0, а
# мокрая (9-8-7 в одну масть) — почти единицу. Опасность зависит ТОЛЬКО от
# доски, поэтому считается раз на улицу и кэшируется.
DANGER_SAFE = 0.25          # ниже — доска сухая
DANGER_HIGH = 0.40          # выше или равно — опасная: столько стоят три карты одной масти
_FLUSH_DANGER = {3: 0.40, 4: 0.60, 5: 0.60}
_STRAIGHT_READY = 0.50      # 4 карты в одном окне: оппоненту хватит одной
_STRAIGHT_BASE = 0.20       # 3 карты в окне: стрит возможен, добрать надо две
_STRAIGHT_WAY = 0.05        # прибавка за каждое лишнее окно (доска связная)
_STRAIGHT_MAX = 0.40
_PAIR_DANGER = 0.25
_TWO_PAIR_DANGER = 0.50     # две пары или трипс на доске
_DANGER_CACHE = {}
_DANGER_CACHE_MAX = 512


def _straight_windows(values):
    """(в скольких пятёрках подряд лежит 3+ карт доски, максимум карт в одной).

    Окно — пять подряд идущих рангов (A-2-3-4-5 ... 10-J-Q-K-A). Три карты доски
    в одном окне значит, что оппонент собирает стрит двумя своими картами,
    четыре — что ему хватит одной.
    """
    uniq = set(values)
    if 14 in uniq:
        uniq.add(1)                      # колесо A-2-3-4-5
    ways = best = 0
    for high in range(5, 15):
        inside = sum(1 for i in range(5) if high - i in uniq)
        if inside >= 3:
            ways += 1
        if inside > best:
            best = inside
    return ways, best


def board_danger(board):
    """Опасность доски -> (score 0.0..1.0, 'safe'/'medium'/'danger', пояснение).

    Кэшируется по картам: внутри улицы доска не меняется, а спрашивают её на
    каждом кадре.
    """
    cards = [c for c in board if c]
    key = tuple(cards)
    hit = _DANGER_CACHE.get(key)
    if hit is not None:
        return hit
    score, notes = 0.0, []
    if cards:
        parsed = parse_cards(cards)
        suits, counts = {}, {}
        for v, s in parsed:
            suits[s] = suits.get(s, 0) + 1
            counts[v] = counts.get(v, 0) + 1

        top_suit = max(suits.values())
        if top_suit >= 3:
            score += _FLUSH_DANGER[min(top_suit, 5)]
            notes.append(f'{top_suit} в масть')

        ways, best = _straight_windows(counts)
        if best >= 4:
            score += _STRAIGHT_READY
            notes.append('4 к стриту')
        elif best == 3:
            score += min(_STRAIGHT_MAX, _STRAIGHT_BASE + _STRAIGHT_WAY * (ways - 1))
            notes.append('связка' + (f' ({ways} захода на стрит)' if ways > 1 else ''))

        pairs = sorted((v for v, c in counts.items() if c >= 2), reverse=True)
        if pairs and (len(pairs) >= 2 or max(counts.values()) >= 3):
            score += _TWO_PAIR_DANGER
            notes.append('трипс на доске' if max(counts.values()) >= 3 else 'две пары на доске')
        elif pairs:
            score += _PAIR_DANGER
            notes.append(f'пара {VALUE_RANK[pairs[0]]} на доске')

    score = round(min(score, 1.0), 3)
    level = 'safe' if score < DANGER_SAFE else ('danger' if score >= DANGER_HIGH else 'medium')
    out = (score, level, ', '.join(notes) or 'сухая доска')
    if len(_DANGER_CACHE) >= _DANGER_CACHE_MAX:
        _DANGER_CACHE.clear()
    _DANGER_CACHE[key] = out
    return out


def danger_score(board):
    """Опасность доски числом 0.0 (сухая) .. 1.0 (мокрая). См. board_danger."""
    return board_danger(board)[0]


def danger_level(board):
    """Опасность доски словом: 'safe' / 'medium' / 'danger'. См. board_danger."""
    return board_danger(board)[1]


# --------------------------------------------------------------------------
# сила готовой руки по рангу составляющих карт
# --------------------------------------------------------------------------
# Флеш и стрит — ещё не натс: значение имеет ранг НАШЕЙ карты в комбинации.
# Живая раздача 19.08 15:49 #21: 6s6c на 2s 8s Qs 4s — «флеш Q», но наша
# флеш-карта шестёрка, и любая пика 7,9,T,J,K,A у оппонента бьёт нас.
_STRONG_FLUSH_CARD = RANK_VALUE['K']       # K/A-высокая карта масти — сильный флеш
_MEDIUM_FLUSH_CARD = RANK_VALUE['7']       # 7..T — средний, ниже — слабый

_DOWNGRADE = {'nuts': 'strong', 'strong': 'medium', 'medium': 'weak', 'weak': 'weak'}


def _flush_grade(hole, board):
    """Сила готового флеша -> ('nuts'/'strong'/'medium'/'weak', пояснение).

    Натс — когда лучший флеш никому не собрать: наши пять карт масти
    сравниваем с лучшими, что остались в колоде оппоненту (две старшие
    свободные карты масти + флеш-карты доски). Иначе решает ранг нашей
    старшей карты этой масти.
    """
    hp, bp = parse_cards(hole), parse_cards(board)
    by_suit = {}
    for v, s in hp + bp:
        by_suit.setdefault(s, []).append(v)
    suit = next((s for s, vs in by_suit.items() if len(vs) >= 5), None)
    if suit is None:
        return 'strong', ''
    board_suited = sorted((v for v, s in bp if s == suit), reverse=True)
    our_suited = sorted((v for v, s in hp if s == suit), reverse=True)
    ours = sorted(our_suited + board_suited, reverse=True)[:5]
    used = set(board_suited) | set(our_suited)
    free = [v for v in range(14, 1, -1) if v not in used][:2]
    best = sorted(free + board_suited, reverse=True)[:5]
    if not our_suited:
        # флеш целиком на доске: нас бьёт любая карта масти старше младшей на доске
        note = f'флеш на доске, своей {suit} нет'
        return ('nuts' if ours >= best else 'weak'), note
    top = our_suited[0]
    note = f'наша карта масти {card_str(top, suit)}'
    if ours >= best:
        return 'nuts', note
    if top >= _STRONG_FLUSH_CARD:
        return 'strong', note
    if top >= _MEDIUM_FLUSH_CARD:
        return 'medium', note
    return 'weak', note


def _best_possible_straight(board):
    """Старшая карта стрита, который может собрать оппонент (добрав 2 карты)."""
    bv = {v for v, _ in parse_cards(board)}
    if 14 in bv:
        bv.add(1)
    for high in range(14, 4, -1):
        need = {high - i for i in range(5)}
        if len(need & bv) >= 3:                 # оппонент добирает не больше двух
            return high
    return None


def _straight_grade(board, high):
    """Сила готового стрита -> ('nuts'/'strong'/'medium', пояснение)."""
    best = _best_possible_straight(board)
    if best and best > high:
        return 'medium', f'возможен стрит до {VALUE_RANK[best]}'
    if high == 14:
        return 'nuts', 'старший стрит'
    if high >= RANK_VALUE['Q']:
        return 'strong', f'стрит до {VALUE_RANK[high]}'
    return 'medium', f'младший стрит до {VALUE_RANK[high]}'


def _two_pair_grade(hole, board, score):
    """Две пары -> сила. Пара доски есть у всех, значит своя пара у нас одна.

    Доска K K 5 и наши A5: «две пары KK55» — на деле пятёрка с тузом, любой
    король бьёт нас трипсом. Настоящие две пары — только когда обе собраны
    нашими картами, либо карманная пара выше пары доски. А если старшая пара
    целиком лежит на доске, наша вторая пара мельче и почти ничего не стоит:
    любая восьмёрка (трипс), пара выше (66, 77) или пара доски с лучшим
    кикером нас бьют (живая раздача 19.08 #58: Ac4c на 8s8d4s6hKd — «две
    пары 8/4», колл на ривере, у оппонента K8).
    """
    bv = [v for v, _ in parse_cards(board)]
    hv = sorted((v for v, _ in parse_cards(hole)), reverse=True)
    board_pairs = {v for v in set(bv) if bv.count(v) >= 2}
    if not board_pairs:
        return 'strong', ''
    top_board = max(board_pairs)
    ours = [v for v in (score[1], score[2]) if v not in board_pairs]
    if not ours:
        return 'weak', 'обе пары на доске — играем кикер'
    pocket = len(hv) == 2 and hv[0] == hv[1]
    if pocket and hv[0] in ours and hv[0] > top_board:
        return 'strong', ''
    if score[1] in board_pairs:
        return 'weak', (f'пара {VALUE_RANK[score[1]]} на доске — общая, '
                        f'наша пара {VALUE_RANK[max(ours)]} мельче')
    return 'medium', f'пара {VALUE_RANK[top_board]} на доске — вторая пара общая'


def _full_house_grade(board, score):
    """Фулл-хаус: strong, но medium, если на доске пара старше нашей тройки."""
    bv = [v for v, _ in parse_cards(board)]
    trips = score[1]
    higher = [v for v in set(bv) if bv.count(v) >= 2 and v > trips]
    if higher:
        return 'medium', f'на доске пара {VALUE_RANK[max(higher)]} — возможен фулл выше'
    return 'strong', ''


# Кикер топ-пары. «Туз с семёркой» и «туз с королём» на доске A-9-2 — одна и та
# же пара тузов, но разные руки: вторую платит любой туз с кикером ниже, первую
# бьёт почти любой туз оппонента. Классы силы (made) от кикера не зависят —
# градация идёт отдельным полем, чтобы стратегия чуть двигала пороги.
_KICKER_STRONG = RANK_VALUE['K']       # A/K — с таким кикером пара выдерживает больше
_KICKER_MEDIUM = RANK_VALUE['T']       # Q/J/T — обычный кикер, ниже — слабый


def _kicker_grade(hv, matched):
    """Сила кикера топ-пары: 'strong' (A/K), 'medium' (Q/J/T), 'weak' (ниже T)."""
    if len(hv) != 2:
        return None
    kicker = hv[1] if hv[0] == matched else hv[0]
    if kicker >= _KICKER_STRONG:
        return 'strong'
    if kicker >= _KICKER_MEDIUM:
        return 'medium'
    return 'weak'


def _board_flush_threat(hole, board):
    """На доске 4+ карты одной масти, а флеша у нас нет — у оппонента он возможен."""
    hp, bp = parse_cards(hole), parse_cards(board)
    by_suit = {}
    for v, s in bp:
        by_suit.setdefault(s, []).append(v)
    for s, vs in by_suit.items():
        if len(vs) >= 4 and len(vs) + sum(1 for _, hs in hp if hs == s) < 5:
            return s
    return None


# Кэш разбора руки. Разбор семи карт — самая дорогая часть решения (перебор 21
# пятёрки), а за один ход его просят два-три раза с одними и теми же картами:
# сама стратегия, повторный вопрос с no_raise (рейз недоступен) и hand_note для
# строки лога. Ключ — карты, результат от них не зависит больше ни от чего.
# ВАЖНО: словарь отдаётся БЕЗ копии, менять его на месте нельзя.
_CLASS_CACHE = {}
_CLASS_CACHE_MAX = 512          # больше не копим: за сессию рук тысячи


def hand_class(hole, board):
    """Классификация руки для постфлоп-стратегии.

    Возвращает dict: score, category, name, made ('nuts'/'strong'/'medium'/'weak'/'air'),
    made_note (чем определена сила), pair_type ('overpair'/'top'/'middle'/'bottom'/None),
    draws.

    Результат кэшируется по картам (см. _CLASS_CACHE) и отдаётся общим на всех —
    менять полученный словарь нельзя.
    """
    key = (tuple(hole), tuple(board))
    hit = _CLASS_CACHE.get(key)
    if hit is not None:
        return hit
    result = _classify(hole, board)
    if len(_CLASS_CACHE) >= _CLASS_CACHE_MAX:
        _CLASS_CACHE.clear()
    _CLASS_CACHE[key] = result
    return result


def _classify(hole, board):
    """Сам разбор руки (без кэша) — см. hand_class."""
    hole, board = list(hole), list(board)
    cards = hole + board
    result = {'draws': [], 'pair_type': None, 'made_note': '', 'kicker_grade': None}
    if len(cards) >= 5:
        score = evaluate(cards)
    else:
        score = None
    result['score'] = score
    result['category'] = score[0] if score else None
    result['name'] = describe(score) if score else None

    if flush_draw(cards, hole=hole):
        result['draws'].append('flush')
    sd = straight_draw(cards, board=board)
    if sd:
        result['draws'].append(sd)
    result['outs'] = count_outs(hole, board)

    hv = sorted((v for v, _ in parse_cards(hole)), reverse=True)
    bv = sorted((v for v, _ in parse_cards(board)), reverse=True)
    cat = result['category']
    if board and cat is not None:
        if cat == PAIR:
            if len(hv) == 2 and hv[0] == hv[1]:
                result['pair_type'] = 'overpair' if hv[0] > bv[0] else 'underpair'
            elif bv and any(v in bv for v in hv):
                matched = max(v for v in hv if v in bv)
                if matched == bv[0]:
                    result['pair_type'] = 'top'
                    result['kicker_grade'] = _kicker_grade(hv, matched)
                elif matched == bv[-1]:
                    result['pair_type'] = 'bottom'
                else:
                    result['pair_type'] = 'middle'
        elif cat == TRIPS and len(hv) == 2 and hv[0] == hv[1]:
            result['pair_type'] = 'set'

    if cat is None:
        result['made'] = 'unknown'
    elif cat in (STRAIGHT_FLUSH, QUADS):
        result['made'] = 'nuts'
    elif cat == FULL_HOUSE:
        result['made'], result['made_note'] = _full_house_grade(board, score)
    elif cat == FLUSH:
        result['made'], result['made_note'] = _flush_grade(hole, board)
    elif cat == STRAIGHT:
        result['made'], result['made_note'] = _straight_grade(board, score[1])
    elif cat >= TRIPS:
        result['made'] = 'strong'
    elif cat == TWO_PAIR:
        result['made'], result['made_note'] = _two_pair_grade(hole, board, score)
    elif cat == PAIR and result['pair_type'] in ('overpair', 'top', 'set'):
        result['made'] = 'medium'
    elif (cat == PAIR and result['pair_type'] == 'underpair'
          and sum(1 for v in set(bv) if v > hv[0]) == 1):
        # карманная пара под одной картой доски (QQ на K-7-2) — это вторая пара
        # с лучшим кикером, одну ставку она выдерживает, «weak» было слишком тайтово
        result['made'] = 'medium'
        result['made_note'] = f'пара {VALUE_RANK[hv[0]]} под одной картой доски'
    elif cat == PAIR:
        result['made'] = 'weak'
    elif result['draws']:
        result['made'] = 'draw'
    else:
        result['made'] = 'air'

    # Флеш у оппонента бьёт всё, что ниже флеша: на доске из 4 карт одной масти
    # наши стрит/сет/две пары — уже не та рука, с которой играют банк.
    if cat is not None and PAIR <= cat < FLUSH:
        threat = _board_flush_threat(hole, board)
        if threat:
            result['made'] = _DOWNGRADE.get(result['made'], result['made'])
            note = f'на доске 4 карты масти {threat} — возможен флеш'
            result['made_note'] = f'{result["made_note"]}, {note}'.lstrip(', ')

    # Играем доску: на ривере наши карты ничего не добавляют, лучшее — сплит.
    # Растить банк такой рукой нельзя, а коллить — только если доска непобиваема
    # (её натсовость уже оценили _flush_grade/_straight_grade выше).
    if len(board) == 5 and score is not None and result['made'] != 'nuts':
        if evaluate(board) == score:
            result['made'] = 'weak'
            note = 'играем доску — в лучшем случае сплит'
            result['made_note'] = f'{result["made_note"]}, {note}'.lstrip(', ')
    return result


if __name__ == '__main__':
    import sys
    cards = sys.argv[1:] or ['Ah', 'Kh', 'Qh', 'Jh', 'Th', '2c', '3d']
    score, five = best_five(cards)
    print(f'{cards} -> {describe(score)}  {five}  score={score}')
