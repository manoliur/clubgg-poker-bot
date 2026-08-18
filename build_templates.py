#!/usr/bin/env python3
"""Сбор эталонов рангов/мастей из размеченных скриншотов.

Эталон = нормализованный глиф (см. card_reader.extract_glyphs), а не кусок угла
по фиксированным долям — поэтому эталоны с доски годятся и для моих карт.

Разметка берётся из labels.json (если есть) либо из встроенного списка KNOWN:
    [{"file": "shots/turn_191709.png", "zone": "board", "cards": ["4h","3s","Kc"]}, ...]

Цифры (для чтения банка и суммы колла) собираются из записей вида:
    {"file": "shots/x.png", "rect": [0.35,0.86,0.65,0.99], "text": "2.5", "ink": "yellow"}
Глифы в прямоугольнике сортируются слева направо и сопоставляются символам text.

Запуск:  python build_templates.py [labels.json]
"""
import os
import sys
import json
import cv2
import numpy as np

import config
from card_reader import (my_index_rects, find_board_cards, corner_crop, index_crop,
                         extract_glyphs, save_template, RANK_ORDER, SUITS)

KNOWN = [
    {'file': 'shots/turn_191709.png', 'zone': 'board', 'cards': ['4h', '3s', 'Kc', '5c', 'Ad']},
    {'file': 'shots/turn_191709.png', 'zone': 'my',    'cards': ['Jh', '2d']},
    {'file': 'shots/now2.png',        'zone': 'my',    'cards': ['8h', '2h']},
    {'file': 'shots/chk2.png',        'zone': 'my',    'cards': ['7h', '6s']},
    {'file': 'shots/turn_184049.png', 'zone': 'my',    'cards': ['Kc', '8c']},
    {'file': 'shots/after_call.png',  'zone': 'board', 'cards': ['6d', '5h', 'As']},
    {'file': 'shots/after_call.png',  'zone': 'my',    'cards': ['Kc', '8c']},
    {'file': 'shots/fast1.png',       'zone': 'board', 'cards': ['6d', '5h', 'As', '9c']},
    {'file': 'shots/fast1.png',       'zone': 'my',    'cards': ['Kc', '8c']},
    {'file': 'shots/fast2.png',       'zone': 'board', 'cards': ['6d', '5h', 'As', '9c', 'Qc']},
    {'file': 'shots/fast2.png',       'zone': 'my',    'cards': ['Kc', '8c']},
]


def collect(labels, base=None, tpl_dir=None, verbose=True):
    """Собрать эталоны. Возвращает (сколько_рангов, сколько_мастей, пропуски)."""
    base = base or config.BASE
    acc_rank, acc_suit, acc_digit = {}, {}, {}
    skipped = []
    for item in labels:
        path = item['file']
        if not os.path.isabs(path):
            path = os.path.join(base, path)
        img = cv2.imread(path)
        if img is None:
            skipped.append(f'{item["file"]}: не читается')
            continue
        if 'text' in item:
            err = _collect_digits(img, item, acc_digit)
            if err:
                skipped.append(f'{item["file"]}: {err}')
            continue
        board = item['zone'] == 'board'
        boxes = find_board_cards(img) if board else my_index_rects(img)
        if len(boxes) < len(item['cards']):
            skipped.append(f'{item["file"]}: найдено {len(boxes)} карт < {len(item["cards"])}')
            continue
        for box, label in zip(boxes, item['cards']):
            g = extract_glyphs(corner_crop(img, box) if board else index_crop(img, box))
            if g is None:
                skipped.append(f'{item["file"]} {label}: глифы не найдены')
                continue
            rank, suit = label[0], label[1]
            # «10» — тоже обычный эталон: правило «двух глифов и широкого бокса»
            # не работает на живых кадрах ClubGG (компактный шрифт, wide=0.7<0.9),
            # без эталона любая десятка читалась как мусор и фолдились AQ/KQ
            acc_rank.setdefault(rank, []).append(g['rank_img'])
            if g['suit_img'] is not None:
                acc_suit.setdefault(suit, []).append(g['suit_img'])
            if verbose:
                print(f'{item["file"]} {label}: box={box} color={g["color"]}')

    for rank, imgs in acc_rank.items():
        save_template(f'rank_{rank}', _average(imgs), tpl_dir)
    for suit, imgs in acc_suit.items():
        save_template(f'suit_{suit}', _average(imgs), tpl_dir)
    for ch, imgs in acc_digit.items():
        save_template(f'digit_{ch}', _average(imgs), tpl_dir)

    if acc_digit and verbose:
        print('цифры:', sorted(acc_digit))
    if verbose:
        print(f'\nЭталоны: ранги {sorted(acc_rank)} ({len(acc_rank)}), '
              f'масти {sorted(acc_suit)} ({len(acc_suit)})')
        missing = [r for r in RANK_ORDER if r not in acc_rank and r != 'T']
        if missing:
            print('НЕТ эталонов рангов:', missing)
        missing_s = [s for s in SUITS if s not in acc_suit]
        if missing_s:
            print('НЕТ эталонов мастей:', missing_s)
        for s in skipped:
            print('пропуск:', s)
    return len(acc_rank), len(acc_suit), skipped


def _collect_digits(img, item, acc):
    """Эталоны цифр из прямоугольника с известным текстом ('2.5', '15 BB')."""
    from table_state import segment_text_glyphs   # локальный импорт: избегаем цикла
    glyphs = segment_text_glyphs(img, item['rect'], ink=item.get('ink', 'yellow'))
    text = [c for c in item['text'] if not c.isspace()]
    if len(glyphs) != len(text):
        return f'глифов {len(glyphs)} != символов {len(text)} в "{item["text"]}"'
    for (_, gimg), ch in zip(glyphs, text):
        key = 'dot' if ch in '.,' else ch
        acc.setdefault(key, []).append(gimg)
    return None


def _average(imgs):
    """Усреднить бинарные глифы и снова бинаризовать."""
    stack = np.stack([i.astype(np.float32) for i in imgs])
    mean = stack.mean(axis=0)
    return (mean > 96).astype(np.uint8) * 255


def main():
    labels = KNOWN
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(config.BASE, 'labels.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            labels = json.load(f)
        print('разметка из', path)
    collect(labels)


if __name__ == '__main__':
    main()
