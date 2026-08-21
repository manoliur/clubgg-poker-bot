#!/usr/bin/env python3
"""Память оппонентов: наблюдения за раздачей -> профили в players.json.

Бот не видит ни чужих карт, ни чужих кнопок — только то, что рисует клиент:
кто сидит за столом, у кого остались карты, есть ли перед нами ставка и сколько
стоит колл. Поэтому статистика собирается ЧЕСТНО по наблюдаемому и заведомо
грубая:

* VPIP — оппонент остался в раздаче после префлопа (карты видны на флопе и
  дальше) либо сам поставил: значит, деньги он вложил добровольно;
* PFR — до нашего первого хода на префлопе колл стоит дороже большого блайнда,
  то есть кто-то поднял;
* three_bet — мы подняли на префлопе, и ход вернулся к нам со ставкой: это
  ререйз. Считается долей от «спотов» — раздач, где мы вообще открывали рейзом;
* agg — постфлоп: ставка/рейз оппонента против его же чеков и коллов (AF).

Кто именно сыграл, видно только когда в раздаче ОДИН оппонент: чужих кнопок в
кадре нет. При нескольких оппонентах копится лишь VPIP (он читается по их
картам на столе), а ставки не приписываются никому.

Профиль называется НИКОМ, прочитанным с плашки игрока (nick_reader). Ник не
прочитался (смайлик закрыл плашку, OCR выключен, мусор) — падаем на старое имя
по месту, «Оппонент 1» (следующий по часовой от героя). Место — плохой ключ:
стоит игроку встать, и места за ним сдвигаются, смешивая статистику разных
людей; поэтому как только ник на месте прочитан, накопленное «Оппонентом N»
переносится в профиль с ником (Profiles.merge).
"""
import datetime
import json
import os

import config

PLAYERS_FILE = os.path.join(config.BASE, 'players.json')

# Порядок улиц: по нему считается «оппонент доехал до следующей улицы» (колл).
STREETS = ('preflop', 'flop', 'turn', 'river')

# Колл дороже этого (в ББ) на префлопе = перед нами не блайнд, а рейз.
PFR_MIN_CALL = 1.05

# Пустой профиль: поля те же, что были в players.json, плюс счётчики, из которых
# доли пересчитываются заново (без накопления ошибки округления).
COUNTERS = ('vpip_hands', 'pfr_hands', 'three_bet_hands', 'three_bet_spots',
            'agg_bets', 'agg_calls')

# Из них — счётчики ДЕЙСТВИЙ оппонента. three_bet_spots сюда не входит: спот
# создаём мы своим рейзом, оппонент в нём мог не сделать ничего.
ACTION_COUNTERS = ('vpip_hands', 'pfr_hands', 'three_bet_hands',
                   'agg_bets', 'agg_calls')


SEAT_NOTE = 'место по кругу от героя (ник не прочитался)'
NICK_NOTE = 'ник прочитан с экрана'


def seat_name(i):
    """Имя оппонента по месту: i — номер по часовой стрелке от героя (1..5)."""
    return f'Оппонент {i}'


def player_name(seat, nick=None):
    """Имя профиля: ник, если он прочитался, иначе — место по кругу."""
    nick = (nick or '').strip()
    return nick or seat_name(seat)


def blank(notes=''):
    p = {'first_seen': datetime.date.today().isoformat(), 'hands': 0,
         'vpip': 0.0, 'pfr': 0.0, 'three_bet': 0.0, 'agg': 0.0,
         'fold_to_3bet': None, 'leaks': [], 'notes': notes}
    p.update({k: 0 for k in COUNTERS})
    return p


def _seed(p):
    """Дописать счётчики в запись, сделанную старой версией (доли -> руки)."""
    hands = int(p.get('hands') or 0)
    for key, share in (('vpip_hands', 'vpip'), ('pfr_hands', 'pfr')):
        if key not in p:
            p[key] = int(round((p.get(share) or 0) * hands))
    if 'three_bet_spots' not in p:
        p['three_bet_spots'] = hands
    if 'three_bet_hands' not in p:
        p['three_bet_hands'] = int(round((p.get('three_bet') or 0) * hands))
    for key in ('agg_bets', 'agg_calls'):
        p.setdefault(key, 0)
    return p


def _derive(p):
    """Пересчитать доли из счётчиков (то, что читают strategy и панель)."""
    hands = max(1, int(p.get('hands') or 0))
    p['vpip'] = round(p['vpip_hands'] / hands, 3)
    p['pfr'] = round(p['pfr_hands'] / hands, 3)
    spots = int(p.get('three_bet_spots') or 0)
    p['three_bet'] = round(p['three_bet_hands'] / spots, 3) if spots else 0.0
    p['agg'] = round(p['agg_bets'] / max(1, int(p.get('agg_calls') or 0)), 2)
    return p


def showed_up(obs):
    """Место «проявилось»: за раздачу от него было хоть одно наблюдаемое действие.

    Клиент рисует плашку и на пустом месте, бот считает такое место занятым —
    и в players.json заводился «Оппонент N» с нулями во всех графах. Честно
    наблюдаемое действие только одно из четырёх: остался в раздаче на флопе и
    дальше (vpip), поднял префлоп (pfr/3-бет), поставил или заколлировал
    постфлоп. Раздача, где не было ничего из этого, ничего об оппоненте не
    говорит — за пустым местом её вообще не было.
    """
    obs = obs or {}
    return bool(obs.get('vpip') or obs.get('pfr') or obs.get('three_bet')
                or int(obs.get('bets') or 0) or int(obs.get('passive') or 0))


def is_ghost(p):
    """Профиль без единого действия: руки копились, а играть было некому."""
    if not isinstance(p, dict):
        return True                  # профиля нет — место себя ещё не показало
    return not any(int(p.get(k) or 0) for k in ACTION_COUNTERS)


def has_manual(p):
    """В записи есть вписанное руками: лики, fold_to_3bet, своя заметка."""
    if p.get('leaks') or p.get('fold_to_3bet') is not None:
        return True
    return (p.get('notes') or '').strip() not in ('', SEAT_NOTE, NICK_NOTE)


def hands_word(n):
    """1 рука, 2 руки, 5 рук — иначе лог читается по-машинному."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return 'рука'
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return 'руки'
    return 'рук'


def summary_line(name, p):
    """«Оппонент 1 — 12 рук, VPIP 34%, PFR 18%, Agg 1.8» — строка для лога."""
    hands = int(p.get('hands') or 0)
    return (f'{name} — {hands} {hands_word(hands)}, '
            f'VPIP {(p.get("vpip") or 0):.0%}, PFR {(p.get("pfr") or 0):.0%}, '
            f'Agg {(p.get("agg") or 0):.1f}')


class Profiles:
    """players.json: чтение, накопление статистики, атомарная запись.

    db — тот самый словарь, который держит бот (main.Bot.players_db): профили
    правятся на месте, поэтому adjust_for_opponent сразу видит свежие цифры.
    """

    def __init__(self, path=None, db=None):
        self.path = path or PLAYERS_FILE
        self.db = db if db is not None else load(self.path)
        self.dropped = []             # пустышки, стёртые последним update_all

    def profile(self, name, create=False, notes=''):
        p = self.db.get(name)
        if not isinstance(p, dict):
            if not create:
                return None
            p = self.db[name] = blank(notes)
        return _seed(p)

    def update(self, name, obs, notes=''):
        """Учесть одну раздачу. obs — итог HandObserver для этого оппонента."""
        p = self.profile(name, create=True, notes=notes)
        p['hands'] = int(p.get('hands') or 0) + 1
        p['vpip_hands'] += bool(obs.get('vpip'))
        p['pfr_hands'] += bool(obs.get('pfr'))
        p['three_bet_spots'] += bool(obs.get('three_bet_spot'))
        p['three_bet_hands'] += bool(obs.get('three_bet'))
        p['agg_bets'] += int(obs.get('bets') or 0)
        p['agg_calls'] += int(obs.get('passive') or 0)
        p['last_seen'] = datetime.date.today().isoformat()
        return _derive(p)

    def update_all(self, observed, nicks=None):
        """Итог раздачи (место -> наблюдения) -> профили. Возвращает имена.

        nicks — {место: ник}, прочитанное с экрана в этой раздаче. Места, ника
        которых там нет, пишутся по-старому, под именем места.

        Место, которое НЕ проявилось (ни ника, ни действия) и по которому нечего
        вспомнить (профиля нет или в нём одни нули), пропускается совсем: пустая
        плашка не должна плодить «Оппонент N» с нулевой статистикой. Как только
        место хоть раз сыграло, его нулевые раздачи считаются как раньше — иначе
        у молчаливого фолдера VPIP уехал бы к 100%. Стёртые пустышки — в
        self.dropped.
        """
        nicks = nicks or {}
        names, self.dropped = [], []
        for seat, obs in sorted(observed.items()):
            nick = nicks.get(seat)
            name = player_name(seat, nick)
            known = self.profile(name)          # заодно дописывает счётчики
            if not nick and not showed_up(obs) and is_ghost(known):
                if self.forget_ghost(name):
                    self.dropped.append(name)
                continue
            self.update(name, obs, notes=NICK_NOTE if nick else SEAT_NOTE)
            names.append(name)
        return names

    def forget_ghost(self, name):
        """Стереть запись места, которое так и не проявилось. True — стёрли.

        Пустышки прошлых версий не удаляются скопом: вдруг за местом сидит живой
        фолдер с нечитаемым ником. Но если запись снова обновляется нулями —
        значит, там и правда никого нет, и она уходит. Проявится тот же игрок —
        профиль заведётся заново, терять в такой записи нечего.

        Не трогаем то, что вписано руками (лики, fold_to_3bet, своя заметка), и
        след переноса статистики (merged_into).
        """
        p = self.db.get(name)
        if not isinstance(p, dict) or p.get('merged_into'):
            return False
        if not is_ghost(p) or has_manual(p):
            return False
        del self.db[name]
        return True

    def merge(self, src, dst):
        """Перенести статистику профиля src в профиль dst. Возвращает: рук перенесено.

        Так «Оппонент 3», накопленный до того, как ник прочитался, достаётся
        человеку, а не стулу. Складываются счётчики (доли из них считаются
        заново), first_seen берётся ранний, last_seen — поздний.

        Запись src удаляется, только если в ней нет ничего, кроме перенесённого:
        заметки, найденные лики и fold_to_3bet вписаны руками и в счётчики не
        входят — такую запись оставляем, но обнуляем перенесённое, чтобы руки не
        посчитались дважды, и помечаем merged_into.
        """
        src_p = self.db.get(src)
        if src == dst or not isinstance(src_p, dict) or src_p.get('merged_into'):
            return 0
        _seed(src_p)
        moved = int(src_p.get('hands') or 0)
        p = self.profile(dst, create=True, notes=NICK_NOTE)
        p['hands'] = int(p.get('hands') or 0) + moved
        for key in COUNTERS:
            p[key] = int(p.get(key) or 0) + int(src_p.get(key) or 0)
        first = src_p.get('first_seen')
        if first and (not p.get('first_seen') or first < p['first_seen']):
            p['first_seen'] = first
        last = src_p.get('last_seen')
        if last and last > (p.get('last_seen') or ''):
            p['last_seen'] = last
        _derive(p)
        if src_p.get('leaks') or src_p.get('fold_to_3bet') is not None:
            src_p['hands'] = 0
            src_p.update({k: 0 for k in COUNTERS})
            src_p['merged_into'] = dst
            _derive(src_p)
        else:
            self.db.pop(src, None)
        return moved

    def opponents(self, hero=None):
        """Профили всех, кроме героя — для панели и стартового лога."""
        hero = hero or config.HERO_NAME
        out = []
        for name, p in self.db.items():
            if name == hero or not isinstance(p, dict) or p.get('merged_into'):
                continue
            out.append({'name': name, 'hands': int(p.get('hands') or 0),
                        'vpip': p.get('vpip') or 0.0, 'pfr': p.get('pfr') or 0.0,
                        'three_bet': p.get('three_bet') or 0.0,
                        'agg': p.get('agg') or 0.0})
        out.sort(key=lambda o: (-o['hands'], o['name']))
        return out

    def save(self):
        """Записать файл целиком (через .tmp: панель не должна прочитать половину)."""
        tmp = self.path + '.tmp'
        try:
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(self.db, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except OSError:
            return False
        return True


def load(path=None):
    path = path or PLAYERS_FILE
    try:
        with open(path, encoding='utf-8') as f:
            db = json.load(f)
    except (OSError, ValueError):
        return {}
    return db if isinstance(db, dict) else {}


class HandObserver:
    """Наблюдения за ТЕКУЩЕЙ раздачей по кадрам стола.

    observe() зовётся на каждом кадре (в том числе когда ход не наш — оппоненты
    как раз тогда и играют) и возвращает итог ПРЕДЫДУЩЕЙ раздачи, когда видит
    новые карманные карты. Пока своих карт мы не видели, наблюдать не за кем:
    без плашки героя круг мест не привязан к нему.
    """

    def __init__(self, confirm=2):
        # карманные карты читаются с ошибками (см. Bot._cards_ok), а лишняя
        # «раздача» из-за такой ошибки портит счётчик рук: новую открываем
        # только когда та же пара пришла confirm кадрами подряд
        self.confirm = max(1, int(confirm))
        self.hole = None
        self.opp = {}
        self.streets = {}
        self.hero_raised = False
        self.solo = None          # единственный оппонент в раздаче (или None)
        self._pending = None
        self._pending_seen = 0

    # ---------- жизненный цикл раздачи ----------
    def start(self, hole):
        self.hole = list(hole)
        self.opp = {}
        self.streets = {}
        self.hero_raised = False
        self.solo = None
        self._pending = None
        self._pending_seen = 0

    def finish(self):
        """Закрыть раздачу: место -> наблюдения. None, если наблюдать было не за кем."""
        seen = {i: r for i, r in self.opp.items() if r['seen']}
        self.hole = None
        self.opp = {}
        self.streets = {}
        self.hero_raised = False
        self.solo = None
        return seen or None

    def _slot(self, i):
        return self.opp.setdefault(i, {'seen': False, 'in_hand': False, 'vpip': False,
                                       'pfr': False, 'three_bet': False,
                                       'three_bet_spot': False, 'bets': 0, 'passive': 0})

    def _street(self, street):
        return self.streets.setdefault(street, {'bet': False, 'passive': False,
                                                'hero_bet': False})

    # ---------- наблюдение ----------
    def observe(self, state):
        finished = None
        hole = [c for c in (state.get('hole') or []) if c]
        if len(hole) == 2 and hole != self.hole:
            self._pending_seen = self._pending_seen + 1 if hole == self._pending else 1
            self._pending = hole
            if self._pending_seen < self.confirm:
                return None          # одна карта могла прочитаться неверно — ждём
            if self.hole is not None:
                finished = self.finish()
            self.start(hole)
        if self.hole is None:
            return finished
        live = self._see_seats(state)
        self._see_street(state, live)
        if state.get('my_turn'):
            self._see_action(state, live)
        return finished

    def _see_seats(self, state):
        """Отметить, кто сидит и у кого остались карты. Возвращает живые места."""
        seats = state.get('seats') or []
        if not any(s.get('hero') for s in seats):
            return []            # круг мест не привязан к герою — считать нельзя
        street = state.get('street') or 'preflop'
        live, i = [], 0
        for s in seats:
            if s.get('hero'):
                continue
            i += 1
            rec = self._slot(i)
            rec['seen'] = True
            if s.get('in_hand'):
                rec['in_hand'] = True
                live.append(i)
                if street in ('flop', 'turn', 'river'):
                    # доехал до флопа = вложил деньги на префлопе
                    rec['vpip'] = True
        self.solo = live[0] if len(live) == 1 else None
        return live

    def _see_street(self, state, live):
        """Новая улица: если мы ставили на прошлой, а оппонент остался — он коллировал."""
        street = state.get('street')
        if street not in STREETS or street in self.streets:
            return
        seen = [s for s in STREETS[:STREETS.index(street)] if s in self.streets]
        prev = self.streets[seen[-1]] if seen else None
        if prev and prev['hero_bet'] and len(live) == 1:
            self._slot(live[0])['passive'] += 1
        self._street(street)

    def _see_action(self, state, live):
        """Кадр НАШЕГО хода: что успел сделать оппонент до нас.

        Кнопки и сумма колла читаются только на своём ходу, поэтому вся
        агрессия оппонента видна именно здесь. Приписываем её, лишь когда
        оппонент в раздаче один: иначе непонятно, кто поставил.
        """
        if len(live) != 1:
            return
        who = self._slot(live[0])
        street = state.get('street') or 'preflop'
        has_bet = bool(state.get('has_bet'))
        to_call = state.get('to_call_bb')
        if street == 'preflop':
            if not has_bet:
                return
            if self.hero_raised:
                # мы подняли, а ход вернулся со ставкой — это ререйз
                who['three_bet'] = who['vpip'] = True
            elif to_call is not None and to_call > PFR_MIN_CALL:
                who['pfr'] = who['vpip'] = True
            return
        st = self._street(street)
        if has_bet:
            if not st['bet']:
                st['bet'] = True
                who['bets'] += 1
                who['vpip'] = True
        elif not st['passive'] and state.get('first_to_act') == 'opp':
            st['passive'] = True        # говорил первым и не поставил — чекнул нам
            who['passive'] += 1

    def note_action(self, action, street=None):
        """Наш собственный ход — от него считаются 3-бет-споты и коллы оппонента."""
        if self.hole is None:
            return
        street = street or 'preflop'
        if street == 'preflop':
            if action == 'raise':
                if not self.hero_raised and self.solo is not None:
                    self._slot(self.solo)['three_bet_spot'] = True
                self.hero_raised = True
            return
        if street in STREETS:
            self._street(street)['hero_bet'] = action == 'raise'


def main(argv=None):
    """CLI: показать накопленные профили (python opponents.py)."""
    import sys
    path = (argv or sys.argv[1:] or [PLAYERS_FILE])[0]
    profiles = Profiles(path)
    rows = profiles.opponents()
    if not rows:
        print(f'{path}: профилей оппонентов пока нет')
        return 0
    for row in rows:
        print(summary_line(row['name'], row))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
