#!/usr/bin/env python3
"""Конвертер чартов DEEPFOLD-SOLVER (UPI-частоты) в наш формат charts/*.json.

Источник: https://github.com/a9876543245/DEEPFOLD-SOLVER
  gto_output/cash/6max_100bb_2_5x_500rake/{RFI,vs_Open,vs_3B}/...

Достоверность (проверено 2026-08-18):
  - RFI монотонно растёт по позициям: UTG 17.6% -> SB 42.7% комбо (взвешенно),
    близко к публичным GTO-чартам (GTO Wizard 6-max 100bb 2.5x).
  - Защита BB против BTN: 3-бет ~20% + колл ~37% = защита ~56% — стандарт GTO.
  - Состав 3-бета BB линейный (AA-88, AKo/AQo, AQs-ATs, KQs-K9s, QJs-Q9s,
    JTs-J9s, T9s-T8s, 98s, 87s, 76s, 65s, 54s) — типичный современный GTO.
  - Формат UPI с частотами — стандарт солверов (PioSOLVER/GTO Wizard).

Порог частоты >= 0.5: рука в диапазоне, если солвер играет её не реже
половины случаев (детерминированная стратегия для табличного бота).
"""
import json
import os
import sys

RANK_ORDER = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
SUFFIX = {'s': 4, 'o': 12}          # вес комбо для взвешенной частоты
PAIR_W = 6

BASE = os.path.dirname(os.path.abspath(__file__))
DL = os.path.join(BASE, 'gto_download')
THRESHOLD = 0.5


def load(name):
    with open(os.path.join(DL, name), encoding='utf-8') as f:
        return json.load(f)


def parse_upi(spec):
    """'AA:1.000,A2s:0.778' -> [(hand, freq)] с весом комбо."""
    out = []
    for item in spec.split(','):
        hand, freq = item.rsplit(':', 1)
        f = float(freq)
        if f <= 0:
            continue
        a, b = hand[0], hand[1]
        if a == b:
            w = PAIR_W
        else:
            w = SUFFIX.get(hand[2:], 6)
        out.append((hand, f, w))
    return out


def hands_above(spec, threshold=THRESHOLD):
    """Руки с частотой >= threshold, отсортированы."""
    hs = [h for h, f, _ in parse_upi(spec) if f >= threshold]
    return sorted(hs, key=lambda h: (RANK_ORDER.index(h[0]), h))


def best_action_ranges(data):
    """Каждая рука -> играемое действие по GTO-частотам.

    Детерминизация смешанной стратегии: рука играется, если солвер играет её
    (raise+call) не реже 50%; действие — то, у которого частота выше. Руки,
    которые солвер в основном фолдит, в диапазоны не попадают.
    """
    freqs = {}
    for a in data['strategy']['actions']:
        name = a['name']
        for h, f, _ in parse_upi(data['strategy']['upi_ranges'][name]):
            freqs.setdefault(h, {})[name] = f
    out = {}
    for h, acts in freqs.items():
        play = sum(f for n, f in acts.items() if 'fold' not in n.lower())
        if play < THRESHOLD:
            continue
        best = max((n for n, f in acts.items() if 'fold' not in n.lower()),
                   key=lambda n: acts[n])
        out.setdefault(best, []).append(h)
    for act in out:
        out[act].sort(key=lambda h: (RANK_ORDER.index(h[0]), h))
    return out


def action_of(data, action_contains='Raise'):
    for a in data['strategy']['actions']:
        if action_contains.lower() in a['name'].lower():
            return data['strategy']['upi_ranges'][a['name']]
    return None


def main():
    out = {'name': '6-max GTO (DEEPFOLD 100bb 2.5x)', 'open': {}, 'call': {}}

    # --- open (RFI) ---
    pos_map = {'UTG': 'utg', 'MP': 'mp', 'CO': 'co', 'BTN': 'btn', 'SB': 'sb'}
    for src, dst in pos_map.items():
        d = load(f'rfi_{src}.json')
        rng = action_of(d, 'Raise')
        out['open'][dst] = hands_above(rng)
        # проверим: сколько это в % комбо
        total = sum(f * w for _, f, w in parse_upi(rng))
        print(f'open[{dst}] = {len(out["open"][dst])} рук, {total/1326*100:.1f}% комбо')

    # --- call / three_bet (vs_Open) ---
    # BB защищается против BTN (самый частый сценарий, в т.ч. HU)
    d = load('open_bb_BTN_2.json')
    br = best_action_ranges(d)
    out['call']['bb'] = br.get('Call', [])
    out['three_bet'] = [h for a, hs in br.items() if 'Raise' in a for h in hs]
    print(f'call[bb] vs BTN = {len(out["call"]["bb"])} рук')
    print(f'three_bet (BB vs BTN) = {len(out["three_bet"])} рук')

    # BTN против CO, CO против MP, MP против UTG, SB против BTN — коллы
    for src, dst, villain in [('open_btn_CO_2.json', 'btn', 'CO'),
                              ('open_co_MP_2.json', 'co', 'MP'),
                              ('open_mp_UTG_2.json', 'mp', 'UTG'),
                              ('open_sb_BTN_2.json', 'sb', 'BTN')]:
        d = load(src)
        br = best_action_ranges(d)
        out['call'][dst] = br.get('Call', [])
        print(f'call[{dst}] vs {villain} = {len(out["call"][dst])} рук')

    # --- three_bet_hu / four_bet / premium / postflop: из стандартного чарта ---
    std = json.load(open(os.path.join(BASE, 'charts', '6max_standard.json'), encoding='utf-8'))
    for key in ('three_bet_hu', 'four_bet', 'premium', 'postflop'):
        out[key] = std.get(key)
    # HU-диапазоны тоже из стандартного (DEEPFOLD даёт только 6-max)
    out['open']['husb'] = std['open'].get('HU_SB')
    out['open']['hubb'] = std['open'].get('HU_BB')
    out['call']['husb'] = std['call'].get('HU_SB')
    out['call']['hubb'] = std['call'].get('HU_BB')

    dest = os.path.join(BASE, 'charts', 'gto_6max.json')
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print('сохранено:', dest)


if __name__ == '__main__':
    main()
