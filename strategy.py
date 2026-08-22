#!/usr/bin/env python3
"""Табличная стратегия NLH без ИИ: префлоп по позиции, постфлоп по силе руки.

Вход — состояние стола (table_state.read_state), выход — решение:
    {'action': 'fold'|'check'|'call'|'raise', 'amount_bb': float|None, 'reason': str,
     'pot_frac': float|None}   # доля банка у ставки — по ней бот выбирает пресет

Префлоп: диапазоны стартовых рук по позициям (стиль ТАГ из strategy.md),
плюс формула Чена как запасная оценка, когда позиция не определена.
Постфлоп: категория руки (hand_evaluator.hand_class) + пот-оддсы и ауты. Дро
считается по одной карте (за вторую на следующей улице придётся заплатить
снова), а неявные пот-оддсы — доборные ставки — учитываются в цене колла.

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
# Короткий стек: с чем идём в алл-ин вместо мин-рейза (пересекается с диапазоном
# открытия по позиции — с BTN пушим шире, чем с UTG).
PUSH_RANGE = '22+, A2s+, A7o+, K9s+, KJo+, QTs+, JTs, T9s'

OPEN_SIZE_BB = 2.5          # открытие рейзом
THREE_BET_MULT = 3.0        # 3-бет = 3x предыдущего рейза
CBET_POT = 0.6              # ставка на велью, доля банка
NUTS_POT = 0.75             # с натсами берём дороже: заряжаем дро и вторую руку
SEMI_BLUFF_POT = 0.45       # полу-блеф с дро

# Сколько раздач нужно метрике профиля, чтобы ей верить. Пороги разные, потому
# что метрики копятся с разной скоростью: VPIP виден в КАЖДОЙ раздаче (оппонент
# либо доехал до флопа, либо нет), PFR — только когда кто-то поднял, а 3-бет и
# агрессия считаются по спотам, которых за сотню рук набирается меньше десятка
# (живой профиль HerGlinoMes: 103 руки, 9 спотов на 3-бет). Общий порог 20 рук
# на все метрики означал бы, что 3-бет применяется по девяти наблюдениям.
MIN_PROFILE_HANDS = 20      # VPIP
MIN_PROFILE_PFR = 40        # PFR
MIN_PROFILE_STATS = 80      # 3-бет и агрессия

# Настройки розыгрыша (их переопределяет секция "postflop" чарта, а поверх —
# стиль и переключатели устройства из devices.json, см. device_settings).
DEFAULT_SETTINGS = {
    'open_size_bb': OPEN_SIZE_BB,
    'three_bet_mult': THREE_BET_MULT,
    'cbet_pot': CBET_POT,
    'nuts_pot': NUTS_POT,
    'semi_bluff_pot': SEMI_BLUFF_POT,
    'aggression': 1.0,          # общий множитель размеров ставок
    'medium_max_price': 0.40,   # дороже — средняя рука пасует
    'draw_min_equity': 0.33,    # эквити дро, при котором коллим вслепую
    'cheap_price': 0.12,        # цена, за которую смотрим следующую карту с чем угодно
    'max_call_stack_frac': 0.20,  # префлоп-колл дороже доли стека — только с премиумом
    'preflop_max_price': 0.45,  # префлоп: цена колла в долях банка
    'big_bet_price': 0.25,      # префлоп: цена, при которой крупную ставку коллят и без премиума
    'implied_pot_mult': 1.0,    # сколько банков доберём на следующей улице, когда дро зайдёт
    'all_in_frac': 0.50,        # колл дороже доли стека = алл-ин: ставок больше не будет
    # --- размеры ставок по силе руки и улице (работают при bet_sizing) ---
    'bet_sizing': False,        # выкл = старое поведение (cbet_pot/nuts_pot/semi_bluff_pot)
    'bet_nuts': 0.75,           # натс — забираем банк целиком
    'bet_strong': 0.60,
    'bet_medium': 0.50,
    'bet_draw': 0.45,
    'street_factor_flop': 1.0,  # множители размера по улицам
    'street_factor_turn': 1.0,
    'street_factor_river': 1.0,
    # --- мультипот (3+ игрока в раздаче) ---
    'multiway_tight': True,
    'multiway_value_mult': 0.8,   # ставка на велью в мультипоте меньше
    'multiway_price_mult': 0.75,  # и коллим только по чётким пот-оддсам
    # --- короткий стек ---
    'short_stack_mode': True,
    'short_stack_bb': 30.0,       # ниже этого — push/fold на префлопе
    'short_stack_price_mult': 1.35,   # коллим шире: ставок дальше почти не будет
    'short_stack_bet_mult': 1.3,      # и ставим крупнее — на стек
    # --- блеф с блокерами (ривер) ---
    'blocker_bluff': False,
    'blocker_bluff_pot': 0.6,
    'blocker_bluff_every': 3,     # не чаще одной такой ситуации из трёх
    # --- игра без позиции ---
    'position_aware': False,
    # --- сколько рук нужно метрике профиля, чтобы её применять (metric_ready) ---
    'min_hands_vpip': MIN_PROFILE_HANDS,
    'min_hands_pfr': MIN_PROFILE_PFR,
    'min_hands_three_bet': MIN_PROFILE_STATS,
    'min_hands_agg': MIN_PROFILE_STATS,
}

# Пресеты стиля: готовые наборы настроек под кнопку в панели. Ползунки агрессии
# и защиты применяются ПОВЕРХ выбранного стиля (см. apply_aggression_defense).
# Диапазоны рук стиль не трогает — они берутся из чарта (charts/*.json).
STYLE_PRESETS = {
    'tighty': {
        'open_size_bb': 3.0, 'cbet_pot': 0.55, 'nuts_pot': 0.75, 'semi_bluff_pot': 0.35,
        'aggression': 0.9,
        'medium_max_price': 0.30, 'draw_min_equity': 0.38, 'cheap_price': 0.08,
        'max_call_stack_frac': 0.15, 'preflop_max_price': 0.36, 'big_bet_price': 0.20,
        'implied_pot_mult': 0.8,
        'bet_nuts': 0.75, 'bet_strong': 0.55, 'bet_medium': 0.40, 'bet_draw': 0.35,
        'street_factor_flop': 0.9, 'street_factor_turn': 1.0, 'street_factor_river': 1.0,
        'short_stack_bb': 25.0,
    },
    'standard': {},                                  # DEFAULT_SETTINGS как есть
    'aggressive': {
        'open_size_bb': 2.8, 'three_bet_mult': 3.5, 'cbet_pot': 0.70, 'nuts_pot': 0.90,
        'semi_bluff_pot': 0.60,
        'aggression': 1.2,
        # коллит как стандарт: агрессия — про ставки, а не про рыхлые коллы
        'medium_max_price': 0.40, 'draw_min_equity': 0.33, 'cheap_price': 0.12,
        'max_call_stack_frac': 0.20, 'preflop_max_price': 0.45, 'big_bet_price': 0.25,
        'bet_nuts': 0.90, 'bet_strong': 0.70, 'bet_medium': 0.55, 'bet_draw': 0.55,
        'street_factor_flop': 1.1, 'street_factor_turn': 1.0, 'street_factor_river': 1.05,
    },
    'loose': {
        'open_size_bb': 2.2, 'cbet_pot': 0.60, 'nuts_pot': 0.75, 'semi_bluff_pot': 0.50,
        'aggression': 1.05,
        'medium_max_price': 0.50, 'draw_min_equity': 0.28, 'cheap_price': 0.18,
        'max_call_stack_frac': 0.28, 'preflop_max_price': 0.55, 'big_bet_price': 0.32,
        'implied_pot_mult': 1.3,
        'bet_nuts': 0.75, 'bet_strong': 0.60, 'bet_medium': 0.50, 'bet_draw': 0.50,
        'short_stack_bb': 35.0,
    },
}
DEFAULT_STYLE = 'standard'
# подписи для панели: ключ -> что увидит человек
STYLE_TITLES = {'tighty': 'Тайтовый', 'standard': 'Стандарт',
                'aggressive': 'Агрессивный', 'loose': 'Лузовый'}
# Переключатели «вкл/выкл» — панель пишет их в devices.json как true/false.
FLAG_KEYS = ('bet_sizing', 'multiway_tight', 'short_stack_mode', 'blocker_bluff',
             'position_aware')
# Ключи настроек, которые панель может класть прямо в запись устройства.
# 'aggression' исключён: в devices.json это ползунок-множитель, а не сама настройка.
DEVICE_SETTING_KEYS = tuple(k for k in DEFAULT_SETTINGS if k != 'aggression')

# Примерное эквити готовой руки против крупной ставки/алл-ина — по классу силы.
# Крупная ставка почти всегда значит, что у оппонента тоже что-то есть, поэтому
# слабый флеш (наша карта масти 6) выигрывает у алл-ина примерно в трети случаев.
SHOWDOWN_EQUITY = {'nuts': 0.85, 'strong': 0.65, 'medium': 0.45, 'weak': 0.30}
CALL_MARGIN = 0.05          # запас: коллим, когда цена заметно ниже эквити
BLIND_PRICE = 0.33          # цена колла, когда чисел банка нет (ставка ~полбанка)

# Метрика профиля -> ключ настройки с её порогом по рукам (см. metric_ready).
PROFILE_MIN_HANDS = {'vpip': 'min_hands_vpip', 'pfr': 'min_hands_pfr',
                     'three_bet': 'min_hands_three_bet', 'agg': 'min_hands_agg'}
# И её знаменатель: пока он пуст, сама доля ничего не значит. «3-бет 0%» при
# нуле спотов — это не пассивный оппонент, а отсутствие наблюдений.
PROFILE_DENOM = {'vpip': ('hands',), 'pfr': ('hands',),
                 'three_bet': ('three_bet_spots',), 'agg': ('agg_bets', 'agg_calls')}

# Блайнды за столом (ББ) — по ним считается, сколько в банке чужих денег.
BLINDS_BB = 1.5
# Сколько из них наши — по позиции героя (в хедз-апе позиции те же SB/BB).
HERO_BLIND_BB = {'SB': 0.5, 'BB': 1.0}
# Ставка от стольких открытий — уже поставлена ПОВЕРХ чужих денег, а не открытие.
RERAISE_OPEN_MULT = 1.6
# Запас к оценке лимпов (ББ): чужой блайнд и лимпер, успевший сбросить карты.
LIMP_SLACK_BB = 1.5


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
        self.push = data.get('push', PUSH_RANGE)
        self.settings = {**DEFAULT_SETTINGS, **(data.get('postflop') or {})}
        for spec in (self.three_bet, self.three_bet_hu, self.four_bet, self.premium,
                     self.push):
            parse_range(spec)

    def copy(self):
        """Отдельный экземпляр с теми же правилами: настройки можно менять на лету,
        не задевая чарт по умолчанию (он один на процесс)."""
        import copy as _copy
        other = _copy.copy(self)
        other.settings = dict(self.settings)
        return other

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

    # какими ключами меряется ставка: старым способом и по силе руки (bet_sizing)
    _LEGACY_SIZE = {'nuts': 'nuts_pot', 'strong': 'cbet_pot', 'medium': 'cbet_pot',
                    'draw': 'semi_bluff_pot'}
    _STRENGTH_SIZE = {'nuts': 'bet_nuts', 'strong': 'bet_strong', 'medium': 'bet_medium',
                      'draw': 'bet_draw'}

    def bet_frac(self, kind, street=None, multiway=False, short=False, mult=1.0):
        """Доля банка для ставки рукой силы kind ('nuts'/'strong'/'medium'/'draw').

        При bet_sizing размер зависит от силы руки и улицы (натс на ривере —
        максимум), иначе работают старые ключи cbet_pot/nuts_pot/semi_bluff_pot
        и поведение бота не меняется. Больше банка не ставим: крупнее пресета
        «100% банка» в клиенте всё равно ничего нет.
        """
        st = self.settings
        if st['bet_sizing']:
            frac = st[self._STRENGTH_SIZE[kind]]
            if street:
                frac *= st.get(f'street_factor_{street}', 1.0)
        else:
            frac = st[self._LEGACY_SIZE[kind]]
            if kind == 'medium':
                frac *= 0.8                       # конт-бет средней рукой — поменьше
        frac *= st['aggression'] * mult
        if multiway:
            frac *= st['multiway_value_mult']     # в мультипоте велью-ставка меньше
        if short:
            frac *= st['short_stack_bet_mult']    # с коротким стеком ставим на стек
        return round(min(frac, 1.0), 3)

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
# настройки устройства: стиль + переключатели + ползунки
# --------------------------------------------------------------------------
def style_settings(style, base=None):
    """Настройки выбранного стиля поверх базовых (обычно — настроек чарта).

    Неизвестный стиль = 'standard': панель не должна ронять бота опечаткой.
    """
    out = dict(base if base is not None else DEFAULT_SETTINGS)
    out.update(STYLE_PRESETS.get(str(style or DEFAULT_STYLE).lower().strip(),
                                 STYLE_PRESETS[DEFAULT_STYLE]))
    return out


def apply_aggression_defense(settings, aggression=1.0, defense=1.0):
    """Ползунки панели поверх готовых настроек. Возвращает НОВЫЙ словарь.

    Агрессия — общий множитель размеров ставок, защита — готовность коллить
    (выше пороги цены, ниже нужное эквити дро). Функция идемпотентна
    относительно исходного словаря: её всегда применяют к базе, а не к
    результату прошлого применения, поэтому настройки не «уползают» при каждом
    перечитывании devices.json.
    """
    out = dict(settings)
    aggression = float(aggression or 1.0)
    defense = float(defense or 1.0)
    out['aggression'] = round(out.get('aggression', 1.0) * aggression, 3)
    for key in ('medium_max_price', 'preflop_max_price', 'cheap_price', 'big_bet_price',
                'max_call_stack_frac'):
        out[key] = round(out[key] * defense, 3)
    out['draw_min_equity'] = round(out['draw_min_equity'] / defense, 3)
    return out


def device_settings(base, cfg, sliders=True):
    """Настройки бота по записи устройства из devices.json.

    Порядок: настройки чарта -> пресет стиля -> отдельные ключи из записи
    (в том числе вложенная секция "settings") -> ползунки агрессии/защиты.
    Ползунки идут последними, поэтому смена стиля их не отменяет.

    sliders=False — без агрессии и защиты: панель показывает пороги такими,
    какими их сохранит обратно, иначе множители накручивались бы при каждом
    открытии страницы.
    """
    cfg = cfg or {}
    out = style_settings(cfg.get('style'), base)
    for key in DEVICE_SETTING_KEYS:
        if key in cfg and cfg[key] is not None:
            out[key] = bool(cfg[key]) if key in FLAG_KEYS else float(cfg[key])
    for key, value in (cfg.get('settings') or {}).items():
        if key in DEFAULT_SETTINGS and value is not None:
            out[key] = bool(value) if key in FLAG_KEYS else float(value)
    if not sliders:
        return out
    return apply_aggression_defense(out, cfg.get('aggression', 1.0),
                                    cfg.get('defense', 1.0))


# --------------------------------------------------------------------------
# помощники
# --------------------------------------------------------------------------
def pot_odds(to_call_bb, pot_bb):
    """Цена колла: доля банка после колла. None, если чисел нет."""
    if not to_call_bb or pot_bb is None:
        return None
    total = pot_bb + to_call_bb
    return to_call_bb / total if total > 0 else None


def implied_price(to_call_bb, pot_bb, stack_bb, mult):
    """Цена колла с учётом неявных пот-оддсов — денег, которые доберём, когда дро зайдёт.

    Обычные пот-оддсы сравнивают цену только с тем, что лежит в банке сейчас.
    Дро же окупается будущими ставками: собрав флеш, мы выигрываем ещё примерно
    банк. Добор ограничен остатком стека (больше него никто не доплатит).
    """
    if not to_call_bb or not pot_bb:
        return None
    extra = mult * pot_bb
    if stack_bb:
        extra = max(0.0, min(extra, stack_bb - to_call_bb))
    return to_call_bb / (pot_bb + extra + to_call_bb)


def _d(action, reason, amount=None, pot_frac=None):
    return {'action': action, 'amount_bb': amount, 'reason': reason, 'pot_frac': pot_frac}


def is_short(stack_bb, settings):
    """Короткий стек: ставок впереди почти не осталось, играем на весь стек."""
    return bool(settings['short_stack_mode'] and stack_bb
                and stack_bb < settings['short_stack_bb'])


def nut_blocker(hole, board):
    """Чем наша рука мешает оппоненту иметь натс. Пустая строка — ничем.

    Считаются две вещи: туз/король в масти, которой на доске 3+ карты (натс-флеша
    у оппонента быть не может — старшая карта масти у нас), и карта, которой
    собирается старший возможный стрит.
    """
    try:
        hv, bv = he.parse_cards(hole), he.parse_cards(board)
    except (he.BadCard, ValueError):
        return ''
    suits = {}
    for _, s in bv:
        suits[s] = suits.get(s, 0) + 1
    for v, s in hv:
        if v >= RANK_VALUE['K'] and suits.get(s, 0) >= 3:
            return f'{VALUE_RANK[v]}{s} — нет натс-флеша'
    high = he._best_possible_straight(board)
    if high and any(v == high for v, _ in hv):
        return f'{VALUE_RANK[high]} — нет старшего стрита'
    return ''


def blocker_bluff_spot(state, chart=None, stack_bb=100.0):
    """Ривер, готовой руки нет, но у нас блокер натса — та самая ситуация для блефа.

    Отдельная функция нужна главному циклу: частоту блефа считает бот (счётчик
    «не чаще одной из N»), а распознаёт ситуацию стратегия.
    """
    chart = chart or _ACTIVE
    if not chart.settings['blocker_bluff'] or state.get('has_bet') or state.get('no_raise'):
        return False
    if state.get('street') != 'river':
        return False
    players = state.get('players')
    if chart.settings['multiway_tight'] and players is not None and players >= 3:
        return False
    hole = [c for c in (state.get('hole') or []) if c]
    board = [c for c in (state.get('board') or []) if c]
    if len(hole) != 2 or len(board) != 5:
        return False
    try:
        # на ривере «дро» — тот же воздух: доборной карты больше нет
        if he.hand_class(hole, board)['made'] not in ('air', 'draw'):
            return False
    except (he.BadCard, ValueError):
        return False
    return bool(nut_blocker(hole, board))


def hand_note(hole, board, street, to_call_bb=None, pot_bb=None):
    """Контекст решения одной строкой для лога: что собрано, цена, эквити.

    Ничего не решает — только называет то, на что смотрит decide: класс руки
    (made) с человеческим названием, пот-оддсы и грубую оценку эквити (по аутам
    для дро, по классу — для готовой руки). Нужен главному циклу: живой лог
    должен показывать не только причину, но и её исходные данные.
    """
    out = {'made': 'preflop', 'made_note': '', 'name': '',
           'pot_odds': pot_odds(to_call_bb, pot_bb), 'equity': None}
    hole = [c for c in (hole or []) if c]
    board = [c for c in (board or []) if c]
    if len(hole) != 2:
        out['made'] = 'unknown'
        return out
    if not board:
        try:
            out['name'] = hand_code(hole)
        except (he.BadCard, ValueError):
            out['made'] = 'unknown'
        return out
    try:
        cls = he.hand_class(hole, board)
    except (he.BadCard, ValueError):
        out['made'] = 'unknown'
        return out
    out['made'] = cls['made']
    out['made_note'] = cls['made_note'] or ''
    out['name'] = cls['name'] or ''
    if cls['made'] == 'draw' or cls['draws']:
        out['equity'] = he.equity_from_outs(cls['outs'], street)
    else:
        out['equity'] = SHOWDOWN_EQUITY.get(cls['made'])
    return out


def _odds_note(price, equity):
    """«пот-оддсы 9%, эквити ~30%» — по этой строке видно, как принято решение."""
    shown = f'{price:.0%}' if price is not None else f'~{BLIND_PRICE:.0%} (банк неизвестен)'
    return f'пот-оддсы {shown}, эквити ~{equity:.0%}'


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
def preflop_dead_bb(pot_bb, to_call_bb, position=None):
    """Чужие деньги, лежащие в банке ПОД текущей ставкой (ББ).

    Чужих кнопок бот не видит, чужих ставок по отдельности — тоже: в кадре есть
    только банк и сумма колла. Но из них считается, сколько вложили ДО этой
    ставки —

        банк = блайнды + все вложения, текущая ставка = колл + наш блайнд,

    и всё, что в банке сверх блайндов и текущей ставки, положено до неё:
    лимперами или предыдущим рейзером с его коллерами.

    None — банка в кадре нет (эталонов цифр может не быть), считать не из чего.
    """
    if pot_bb is None or to_call_bb is None:
        return None
    hero_in = HERO_BLIND_BB.get(position or '', 0.0)
    return max(0.0, round(pot_bb - BLINDS_BB - (to_call_bb + hero_in), 1))


def preflop_investors(pot_bb, to_call_bb, position=None):
    """Сколько вложений сделано на префлопе до нашего хода (ставка плюс лимпы).

    1 — перед нами голое открытие; 2 и больше — под ставкой лежат чужие деньги.
    Лимп стоит ББ, поэтому лишние деньги в банке и есть счётчик вложивших.
    """
    dead = preflop_dead_bb(pot_bb, to_call_bb, position)
    return None if dead is None else 1 + min(5, int(round(dead)))


def is_squeeze(pot_bb, to_call_bb, position=None, live_players=None):
    """Поставлена ли ставка ПОВЕРХ чужого рейза: сквиз или холодный 3-бет.

    Само число вложивших ререйз от изо-рейза не отличает: и «2 лимпа + рейз до
    5ББ», и «открытие + коллер + сквиз» — это 2-3 вложения, но первое ещё
    открытие (см. BigOpenTest), а второе уже ререйз. Отличает СКОЛЬКО вложено:
    лимп стоит ровно ББ, поэтому если под ставкой лежит больше, чем могли
    налимпить оставшиеся в раздаче, — значит до неё уже поднимали, и наша рука
    играет против ререйзного диапазона.

    live_players — сколько игроков ещё в раздаче (герой в том числе). Без него
    верхней границы лимпов нет и решать не из чего — False. В хедз-апе — тоже
    False: класть деньги под чужую ставку там просто некому, а банк в кадре
    читается с ошибками, и лишний ББ не должен превращать открытие в 3-бет.
    """
    dead = preflop_dead_bb(pot_bb, to_call_bb, position)
    if dead is None or not live_players or int(live_players) < 3:
        return False
    # оппоненты в раздаче минус тот, кто и поставил, по ББ на лимп; запас 1.5ББ —
    # на чужой блайнд и на лимпера, который уже успел сбросить карты
    limps = max(0, int(live_players) - 2) + LIMP_SLACK_BB
    return dead > limps


def _versus(no_raise, to_call_bb, stack_bb):
    """Как назвать ставку, на которую отвечаем: алл-ин или просто крупная."""
    if no_raise:
        return 'против алл-ина'
    part = f' ({to_call_bb / stack_bb:.0%} стека)' if to_call_bb and stack_bb else ''
    return f'против крупной ставки {to_call_bb}ББ{part}'


def decide_preflop(hole, position, players, has_bet, to_call_bb, pot_bb, stack_bb, chart=None,
                   no_raise=False, hero_raised=False, live_players=None):
    """Решение на префлопе. no_raise — рейз недоступен (оппонент в алл-ине).

    Когда рейзить нечем или ставка перед нами размером с полстека, чарты 3-бета
    и 4-бета не применяются: вопрос уже не «повышать ли», а окупается ли колл.

    hero_raised — мы на этом префлопе уже поднимали. По этому признаку ставка
    перед нами отличается от обычного открытия: см. is_3bet ниже.

    players — сколько СИДИТ за столом (по ним берутся диапазоны), live_players —
    сколько ещё в раздаче (по ним видно, сколько денег под ставкой чужие).
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
    # Короткий стек: мин-рейз здесь только раздувает банк, которым потом нечем
    # играть, — заходим сразу на весь стек, а коллим шире (ставок дальше не будет).
    short = is_short(stack_bb, st)
    note = f' [короткий стек {stack_bb:.0f}ББ — push/fold]' if short else ''
    price_mult = st['short_stack_price_mult'] if short else 1.0

    if not has_bet:
        # никто не поставил: открываемся рейзом или чекаем (мы в ББ)
        if no_raise:
            return _d('check', f'{code}: ставить нечем (живого пресета нет) — чек')
        if short:
            # пушим только тем, что и открывали бы с этой позиции: с BTN шире, с UTG уже
            push_rng = parse_range(chart.push) & parse_range(open_rng or chart.push)
            if hand_code(hole) in push_rng:
                return _d('raise', f'{code}: алл-ин {stack_bb:.0f}ББ вместо рейза{note}',
                          stack_bb, pot_frac=1.0)
            return _d('check', f'{code}: вне пуш-диапазона{note}')
        if in_range(hole, open_rng):
            return _d('raise', f'{code}: открытие с {position or "?"} '
                               f'(диапазон {"HU" if hu else "6-max"}, {chart.name})', open_size)
        return _d('check', f'{code}: вне диапазона открытия, но чек бесплатный')

    # перед нами ставка
    cap = st['max_call_stack_frac'] * price_mult
    premium = in_range(hole, chart.premium)
    price = pot_odds(to_call_bb, pot_bb)
    # Ставка больше cap стека — это уже алл-ин или 4-бет: поднимать её «на велью»
    # по чарту нельзя. Живая раздача 19.08 09:52: 76s ответила 3-бетом на
    # алл-ин, рейзить было нечем, и тап выродился в колл 23.7ББ (34% стека).
    big = to_call_bb is not None and stack_bb and to_call_bb > cap * stack_bb
    # Перед нами 3-бет или крупнее? Тогда диапазон 3-бета НЕ применяется:
    # 5-бетить AQ нельзя — у оппонента, дошедшего до 4-бета, диапазон QQ+/AK.
    # Живой кейс: AQ «на велью» против 4-бета доходил до алл-ина против
    # карманных тузов. Против 3-бета+ играем только монстрами (4-бет QQ+/AK) и
    # премиумом (колл JJ+/AQs+), остальное — фолд.
    #
    # Ставка «поверх» видна по двум признакам. Первый — наш собственный прошлый
    # ход: если мы уже поднимали, любая доплата от RERAISE_OPEN_MULT открытий —
    # это ставка ПОВЕРХ нашего рейза. Второй — деньги под ставкой (is_squeeze):
    # сквиз и холодный 3-бет тоже поставлены поверх, хотя мы и не поднимали, и
    # по одному лишь размеру не отличались от крупного открытия — «открытие +
    # коллер + сквиз до 7ББ» проходил как открытие и получал колл премиумом.
    #
    # Когда же под ставкой одни лимпы и мы не поднимали, столько стоит и обычный
    # изо-рейз: на живых столах открывают и в 4.5-5ББ, и по порогу 1.6 бот
    # пасовал TT, 99, AJo, KQs — весь диапазон колла. Тогда порогом служит
    # размер НАСТОЯЩЕГО 3-бета (открытие x three_bet_mult).
    open_bb = st.get('open_size_bb') or 2.5
    over = hero_raised or is_squeeze(pot_bb, to_call_bb, position, live_players)
    is_3bet = (to_call_bb is not None
               and to_call_bb > open_bb * (RERAISE_OPEN_MULT if over else mult))
    # Короткий стек против ререйза: пуш-диапазон здесь не годится — он собран
    # под борьбу за блайнды с ОТКРЫТИЕМ, а не против руки, которая уже
    # поставила поверх. Без этого AQ уходила в алл-ин против 4-бета (QQ+/AK).
    if is_3bet and short and not in_range(hole, chart.four_bet):
        if not premium:
            return _d('fold', f'{code}: фолд против 3-бета+{note}'
                      + (f' — {_odds_note(price, 0.25)}' if price is not None else ''))
        if no_raise:
            return _d('call', f'{code}: колл 3-бета+ с премиум-рукой '
                              f'{_versus(True, to_call_bb, stack_bb)}{note}')
        return _d('raise', f'{code}: премиум против 3-бета+ — алл-ин {stack_bb:.0f}ББ{note}',
                  stack_bb, pot_frac=1.0)
    if not no_raise and not (big and not premium):
        if in_range(hole, chart.four_bet):
            if short:
                return _d('raise', f'{code}: премиум — алл-ин {stack_bb:.0f}ББ{note}',
                          stack_bb, pot_frac=1.0)
            return _d('raise', f'{code}: премиум — 4-бет/олл-ин',
                      max(open_size * 3, (to_call_bb or open_size) * mult))
        if is_3bet and not short:
            if premium:
                # против 4-бета (ставка от ~4x открытия) премиум без монстра пасует:
                # 4-беттер держит QQ+/AK, AQs там ~30% эквити
                if to_call_bb > open_bb * 4:
                    return _d('fold', f'{code}: фолд против 4-бета — '
                                      f'{_odds_note(price, 0.30)}')
                if big and price is not None and price >= st['big_bet_price']:
                    return _d('fold', f'{code}: премиум, но цена {price:.0%} против '
                                      f'3-бета+ — фолд')
                return _d('call', f'{code}: колл 3-бета+ с премиум-рукой'
                          + (f' — {_odds_note(price, 0.40)}' if price is not None else ''))
            return _d('fold', f'{code}: фолд против 3-бета+'
                      + (f' — {_odds_note(price, 0.25)}' if price is not None else ''))
        if in_range(hole, value3bet):
            if short:
                return _d('raise', f'{code}: алл-ин {stack_bb:.0f}ББ вместо 3-бета{note}',
                          stack_bb, pot_frac=1.0)
            return _d('raise', f'{code}: 3-бет на велью', (to_call_bb or open_size) * mult)
    else:
        # Рейза не будет — руки, которыми мы бы повысили, уходят в колл: они
        # сильнее тех, которыми мы просто уравниваем. Без этого AKo пасовал бы
        # против алл-ина: в GTO-чарте диапазон колла на BTN — «77, 88, ATs»,
        # всё остальное сильное лежит в 3-бете и 4-бете.
        call_rng = (parse_range(call_rng) | parse_range(value3bet)
                    | parse_range(chart.four_bet))

    versus = _versus(no_raise, to_call_bb, stack_bb) if (big or no_raise) else ''
    big_price = st['big_bet_price'] * price_mult
    max_price = st['preflop_max_price'] * price_mult
    if in_range(hole, call_rng):
        if big and not premium:
            # Цена решает и здесь: против алл-ина в раздутый банк рука из
            # диапазона колла окупается и без премиума (25% банка — это 3:1,
            # столько эквити есть у любой руки, которой мы вообще играем).
            if price is not None and price < big_price:
                return _d('call', f'{code}: колл {to_call_bb}ББ {versus} — пот-оддсы '
                                  f'{price:.0%} ниже порога {big_price:.0%}{note}')
            return _d('fold', f'{code}: фолд {versus} — колл {to_call_bb}ББ больше '
                              f'{cap:.0%} стека, без премиума'
                      + (f' (пот-оддсы {price:.0%})' if price is not None else '') + note)
        # префлоп цена колла обычно 30-40% банка — это нормально (имплайд-оддсы),
        # отказываемся только от совсем безнадёжной цены
        if price is not None and price > max_price and not premium:
            return _d('fold', f'{code}: цена {price:.0%} банка слишком высока{note}')
        if versus:
            odds = f' по пот-оддсам {price:.0%}' if price is not None else ''
            return _d('call', f'{code}: колл {to_call_bb}ББ {versus}{odds}{note}')
        return _d('call', f'{code}: колл по диапазону {position or "?"}')
    return _d('fold', f'{code}: вне диапазона на {position or "?"}'
              + (f' — фолд {versus}' if versus else '') + note)


# --------------------------------------------------------------------------
# постфлоп
# --------------------------------------------------------------------------
def decide_postflop(hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb, players,
                    chart=None, no_raise=False, oop=None, bluff_ok=True):
    """Решение после флопа с пометкой режима в причине (мультипот, короткий стек).

    Сам розыгрыш считает _postflop, здесь к причине дописывается, почему бот
    сыграл не как обычно, — чтобы это было видно в логе панели.
    """
    chart = chart or _ACTIVE
    st = chart.settings
    multiway = bool(st['multiway_tight'] and players is not None and players >= 3)
    short = is_short(stack_bb, st)
    decision = _postflop(hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb,
                         players, chart, no_raise, multiway, short, oop, bluff_ok)
    notes = []
    if multiway:
        notes.append(f'мультипот {players} игроков — играем тайтовее')
    if short:
        notes.append(f'короткий стек {stack_bb:.0f}ББ — играем на стек')
    if notes:
        decision = dict(decision, reason=decision['reason'] + ' | ' + '; '.join(notes))
    return decision


def _postflop(hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb, players,
              chart, no_raise, multiway, short, oop, bluff_ok):
    """Розыгрыш после флопа. no_raise — рейз/ставка недоступны (живого пресета нет).

    Тогда сильная рука коллит вместо рейза, а полу-блеф отменяется: блефовать
    коллом нельзя, и решение остаётся за пот-оддсами.
    """
    spot = Spot(hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb, players,
                chart, no_raise, multiway, short, oop, bluff_ok)
    return _answer_bet(spot) if has_bet else _lead(spot)


# --------------------------------------------------------------------------
# матрица постфлопа
# --------------------------------------------------------------------------
# Решение после флопа — функция пяти вещей: СИЛА РУКИ (made + класс комбинации),
# ОПАСНОСТЬ ДОСКИ, АКТИВНОСТЬ ОППОНЕНТА (профиль и его линия в этой раздаче),
# ЧИСЛО ИГРОКОВ и УЛИЦА. Раньше все пять читались вперемешку по ходу розыгрыша,
# и каждое новое правило дописывалось очередным `if` в середину. Теперь они
# считаются один раз в Spot, а сама матрица — две функции ниже: строка «против
# ставки» (_answer_bet) и строка «нам чекнули / мы первые» (_lead). Порядок
# проверок внутри них — тот же, что был, поведение не менялось.
class Spot:
    """Ситуация после флопа: всё, от чего зависит решение, посчитанное один раз.

    Считается по одному разу на решение и дальше только читается — поэтому
    матрица не пересчитывает силу руки и опасность доски в каждой ветке.
    """

    __slots__ = ('chart', 'st', 'hole', 'board', 'street', 'has_bet', 'to_call_bb',
                 'pot_bb', 'stack_bb', 'players', 'no_raise', 'multiway', 'short',
                 'oop', 'bluff_ok', 'hu', 'cls', 'made', 'name', 'outs', 'draws',
                 'category', 'big_made', 'price', 'price_mult', 'showdown',
                 'all_in', 'semi')

    def __init__(self, hole, board, street, has_bet, to_call_bb, pot_bb, stack_bb,
                 players, chart, no_raise, multiway, short, oop, bluff_ok):
        st = chart.settings
        self.chart, self.st = chart, st
        self.hole, self.board, self.street = hole, board, street
        self.has_bet, self.to_call_bb, self.pot_bb = has_bet, to_call_bb, pot_bb
        self.stack_bb, self.players = stack_bb, players
        self.no_raise, self.multiway, self.short = no_raise, multiway, short
        self.oop, self.bluff_ok = oop, bluff_ok
        self.hu = players is not None and players <= 2

        # --- сила руки ---
        cls = he.hand_class(hole, board)
        self.cls = cls
        self.made, self.outs, self.draws = cls['made'], cls['outs'], cls['draws']
        self.category = cls['category']
        name = cls['name'] or '?'
        if cls['made_note']:
            name = f'{name} ({cls["made_note"]})'
        self.name = name
        # Флеш/стрит/фулл: рука сильная по номиналу, но её ранг уже учтён в made —
        # младшим флешем банк не растят, а против крупной ставки считают шансы.
        self.big_made = cls['category'] is not None and cls['category'] >= he.STRAIGHT
        self.showdown = SHOWDOWN_EQUITY.get(cls['made'], 0.0)

        # --- цена ---
        self.price = pot_odds(to_call_bb, pot_bb)
        # Колл размером с полстека и больше — фактически алл-ин: ставок дальше не
        # будет, значит дро увидит ОБЕ карты (правило 4x) и добирать нечего.
        self.all_in = bool(no_raise or (to_call_bb and stack_bb
                                        and to_call_bb >= st['all_in_frac'] * stack_bb))
        # Цена колла: в мультипоте нужны чёткие пот-оддсы, с коротким стеком —
        # наоборот шире (ставок дальше почти не будет, дро увидит обе карты).
        price_mult = 1.0
        if multiway and street in ('turn', 'river'):
            # на флопе дро в мультипоте окупается добором (играют трое), а вот на
            # терне и ривере против нескольких ставок нужны чёткие пот-оддсы
            price_mult *= st['multiway_price_mult']
        if short:
            price_mult *= st['short_stack_price_mult']
        self.price_mult = price_mult
        self.semi = chart.bet_frac('draw', street, multiway=multiway, short=short)

    def bet_frac(self, kind, mult=1.0, short=None):
        """Доля банка для ставки этой рукой — с режимами ситуации (см. Chart.bet_frac)."""
        return self.chart.bet_frac(kind, self.street, multiway=self.multiway,
                                   short=self.short if short is None else short,
                                   mult=mult)

    def odds(self, equity=None):
        return _odds_note(self.price, self.showdown if equity is None else equity)


def _answer_bet(spot):
    """Строка матрицы «перед нами ставка»: колл, фолд или рейз."""
    st, name, made, price = spot.st, spot.name, spot.made, spot.price
    if spot.big_made and made in ('medium', 'weak'):
        # 19.08 15:49 #21: флеш с шестёркой отвечал коллом на алл-ин как натс.
        # Против алл-ина нас бьёт любая старшая карта масти — решает цена.
        if ((price if price is not None else BLIND_PRICE)
                < (spot.showdown - CALL_MARGIN) * spot.price_mult):
            return _d('call', f'{name}: колл — {spot.odds()}')
        return _d('fold', f'{name}: фолд — {spot.odds()}')
    if made in ('nuts', 'strong'):
        if spot.no_raise:
            return _d('call', f'{name}: колл против алл-ина — '
                              f'{spot.odds()} (рейз недоступен)')
        # при bet_sizing рейз натсом крупнее, без него — как раньше, cbet*1.5
        kind = made if st['bet_sizing'] else 'strong'
        return _raise_pot(f'{name}: рейз на велью', spot.pot_bb, spot.bet_frac(kind, mult=1.5))
    if made == 'medium':
        cap = st['medium_max_price'] * spot.price_mult
        if price is not None and price > cap:
            return _d('fold', f'{name}: средняя рука, {spot.odds()} — фолд')
        return _d('call', f'{name}: колл со средней рукой, {spot.odds()}')
    if made == 'draw' or spot.draws:
        return _answer_bet_draw(spot)
    if (price is not None and price < st['cheap_price'] * spot.price_mult
            and spot.street != 'river'):
        return _d('call', f'{name}: дёшево ({price:.0%}) — смотрим следующую карту')
    return _d('fold', f'{name}: нечем продолжать')


def _answer_bet_draw(spot):
    """Та же строка матрицы, клетка «дро»: считаем ауты против цены."""
    st, draws, outs, price = spot.st, spot.draws, spot.outs, spot.price
    street = spot.street
    if price is None:
        # чисел нет: считаем ставку полубанком и рассчитываем дойти до ривера
        blind_eq = he.equity_from_outs(outs, street)
        return (_d('call', f'дро {draws}, {outs} аутов (~{blind_eq:.0%}) — колл по аутам')
                if blind_eq >= st['draw_min_equity'] / spot.price_mult
                else _d('fold', f'дро {draws}: {outs} аутов мало'))
    # На флопе колл покупает ОДНУ карту, а не две: на терне за вторую
    # придётся заплатить снова. Правило 4x тут завышало эквити почти
    # вдвое (флеш-дро «36%» против цены 33% — на деле 19%). Недобор
    # закрывают неявные пот-оддсы: когда дро заходит, оппонент доплачивает.
    one_card = street == 'flop' and not spot.all_in
    equity = he.equity_from_outs(outs, 'turn' if one_card else street)
    eff = price if spot.all_in else implied_price(spot.to_call_bb, spot.pot_bb,
                                                  spot.stack_bb, st['implied_pot_mult'])
    eff = price if eff is None else eff
    note = '' if eff == price else f' (с имплайд-оддсами, чистая цена {price:.0%})'
    need = eff / spot.price_mult
    if equity > need:
        return _d('call', f'дро {draws}: {outs} аутов ~{equity:.0%} > цены {need:.0%}{note}')
    if street == 'flop' and 'flush' in draws and spot.hu and not spot.no_raise:
        return _raise_pot(f'сильное дро {draws}: полу-блеф', spot.pot_bb, spot.semi)
    return _d('fold', f'дро {draws}: {equity:.0%} < цены {need:.0%}{note}')


def _lead(spot):
    """Строка матрицы «нам чекнули / мы первые»: ставка или чек."""
    st, name, made, street = spot.st, spot.name, spot.made, spot.street
    if spot.no_raise:
        return _d('check', f'{name}: ставить нечем (живого пресета нет) — чек')
    if spot.big_made and made == 'weak':
        # младший флеш/стрит: ставкой мы соберём велью только от того, кто бьёт
        return _d('check', f'{name}: младшая рука — чек, идём на шоудаун')
    if made in ('nuts', 'strong'):
        # с непобиваемой рукой ставим крупнее: пресет 75% банка вместо 50%
        return _raise_pot(f'{name}: ставка на велью', spot.pot_bb, spot.bet_frac(made))
    if made == 'medium':
        return _lead_medium(spot)
    if street == 'river' and st['blocker_bluff'] and not spot.multiway and made in ('air', 'draw'):
        # Блеф с блокером: натс-флеша/старшего стрита у оппонента быть не может.
        # Идёт до разбора дро: на ривере доборной карты нет, «флеш-дро» с тузом
        # масти — это и есть воздух с блокером. Частоту ограничивает главный
        # цикл (bluff_ok), иначе бот блефовал бы в каждой такой раздаче.
        blocker = nut_blocker(spot.hole, spot.board)
        if blocker and spot.bluff_ok:
            # больше банка не ставим — как и в bet_frac: крупнее пресета «100%»
            # в клиенте ничего нет, а в лог и историю уходил бы выдуманный размер
            return _raise_pot(f'блеф с блокером ({blocker})', spot.pot_bb,
                              round(min(st['blocker_bluff_pot'] * st['aggression'], 1.0), 3))
        if blocker:
            return _d('check', f'{name}: блокер {blocker}, но блеф слишком часто — чек')
    if made == 'draw' or spot.draws:
        if street in ('flop', 'turn') and spot.hu:
            return _raise_pot(f'дро {spot.draws} ({spot.outs} аутов): полу-блеф',
                              spot.pot_bb, spot.semi)
        return _d('check', f'дро {spot.draws}: смотрим карту бесплатно')
    if street == 'flop' and spot.hu:
        return _raise_pot('воздух: конт-бет один раз в хедз-апе', spot.pot_bb, spot.semi)
    return _d('check', f'{name}: чек')


def _lead_medium(spot):
    """Та же строка матрицы, клетка «средняя рука»: тонкое велью или контроль банка."""
    st, name, street = spot.st, spot.name, spot.street
    # тонкое велью — только там, где его заплатят: не против троих и не без позиции
    if spot.multiway:
        return _d('check', f'{name}: не ставим тонко против {spot.players} игроков')
    if st['position_aware'] and spot.oop and street == 'flop':
        return _d('check', f'{name}: без позиции — чек вместо конт-бета')
    if street == 'flop' or (spot.short and street == 'turn'):
        return _raise_pot(f'{name}: конт-бет', spot.pot_bb,
                          spot.bet_frac('medium', short=spot.short))
    return _d('check', f'{name}: контроль банка на {street}')


def _bet_size(pot_bb, fraction):
    if pot_bb is None:
        return None
    return round(pot_bb * fraction, 1)


# --------------------------------------------------------------------------
# адаптация под оппонента и общий вход
# --------------------------------------------------------------------------
def metric_ready(profile, metric, settings=None):
    """Набралось ли на эту метрику профиля столько наблюдений, чтобы ей верить.

    Порог у каждой метрики свой (PROFILE_MIN_HANDS): VPIP осмысленен уже через
    пару десятков раздач, PFR — вдвое позже, а 3-бет и агрессия копятся спотами
    и требуют сотни рук. Кроме рук проверяется знаменатель самой метрики
    (PROFILE_DENOM): «3-бет 0%» при нуле спотов — это не пассивный оппонент, а
    отсутствие наблюдений, и подстраиваться под такой ноль нельзя.
    """
    if not isinstance(profile, dict):
        return False
    key = PROFILE_MIN_HANDS.get(metric)
    if key is None:
        return False
    st = settings if settings is not None else DEFAULT_SETTINGS
    if int(profile.get('hands') or 0) < int(st.get(key, DEFAULT_SETTINGS[key])):
        return False
    return any(int(profile.get(k) or 0) for k in PROFILE_DENOM[metric])


def adjust_for_opponent(decision, profile, made, settings=None):
    """Правки по players.json: против лузово-пассивных не блефуем, против тайтовых — чаще.

    Каждая метрика применяется отдельно и только по своему порогу (metric_ready):
    после одной раздачи, в которой оппонент просто заплатил блайнд, VPIP у него
    100%, и бот перестал бы блефовать против кого угодно с первой же руки; а
    агрессии на те же 20 рук набирается пара наблюдений, и верить ей рано даже
    тогда, когда VPIP уже честный.
    """
    if not profile:
        return decision
    vpip = (profile.get('vpip') or 0) if metric_ready(profile, 'vpip', settings) else 0
    agg = (profile.get('agg') or 0) if metric_ready(profile, 'agg', settings) else 0
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
                                      stack_bb, chart, no_raise=no_raise,
                                      hero_raised=bool(state.get('hero_raised')),
                                      live_players=players)
            made = 'preflop'
        else:
            # first_to_act == 'me' — говорим первыми, то есть играем без позиции
            oop = state.get('first_to_act') == 'me'
            decision = decide_postflop(hole, board, street, has_bet, to_call, pot,
                                       stack_bb, players, chart, no_raise=no_raise,
                                       oop=oop, bluff_ok=state.get('bluff_ok', True))
            made = he.hand_class(hole, board)['made']
    except (he.BadCard, ValueError) as e:
        return _d('check' if not has_bet else 'fold', f'ошибка разбора карт: {e}')

    decision = adjust_for_opponent(decision, profile, made, chart.settings)
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
