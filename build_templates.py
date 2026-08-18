#!/usr/bin/env python3
"""Сбор эталонов рангов/мастей из размеченных скриншотов.

Эталон = нормализованный глиф (см. card_reader.extract_glyphs), а не кусок угла
по фиксированным долям — поэтому эталоны с доски годятся и для моих карт.

Разметка берётся из labels.json (если есть) либо из встроенного списка KNOWN:
    [{"file": "shots/turn_191709.png", "zone": "board", "cards": ["4h","3s","Kc"]}, ...]

Цифры (для чтения банка и суммы колла) собираются из записей вида:
    {"file": "shots/x.png", "rect": [0.35,0.86,0.65,0.99], "text": "2.5", "ink": "amber"}
Глифы в прямоугольнике сортируются слева направо и сопоставляются символам text.
Записи цифр не пишутся руками: их разворачивает digit_labels() из таблицы
DIGIT_FRAMES («что написано в банке / в столбце пресетов / на кнопке колла»),
размеченной глазами по живым кадрам shots_digits/.

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


# --------------------------------------------------------------------------
# разметка цифр по живым кадрам
# --------------------------------------------------------------------------
# Кадр -> (банк, столбец пресетов СВЕРХУ ВНИЗ, сумма колла). None — строки нет
# (столбец свёрнут/кнопки нет), '~' перед числом — строка ПОГАШЕНА (тусклый
# текст). Кадры сняты с телефона и сжаты до 540px, но цифры на них читаются
# глазами; шрифт тот же самый, что на 1080px, а эталон нормализуется по
# размеру — поэтому кадры годятся.
#
# Синтетика для цифр не годится в принципе: у ClubGG свой шрифт, и эталон,
# нарисованный cv2.putText, давал match 0.35 против 0.94 у эталона с живого
# кадра (тот же урок, что был с рангом «10»).
DIGIT_FRAMES = {
    '20260818_125126_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_125145_check.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_125153_raise.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_125221_check.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_125235_fold.jpg':  ('147', (), None),
    '20260818_125251_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_125321_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_125341_check.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_125348_raise.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_125422_call.jpg':  ('3', ('5', '4', '3', '2.3'), '1'),
    '20260818_125429_raise.jpg': ('4', ('4', '3', '2', '1.3'), None),
    '20260818_125446_raise.jpg': ('6.6', ('6.6', '4.9', '3.3', '2.1'), None),
    '20260818_125505_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_125525_raise.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_125539_raise.jpg': ('4', ('4', '3', '2', '1.3'), None),
    '20260818_125550_check.jpg': ('6.6', ('6.6', '4.9', '3.3', '2.1'), None),
    '20260818_125558_fold.jpg':  ('7.6', ('9.6', '7.4', '5.3', '3.8'), None),
    '20260818_125616_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_125637_check.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_125651_raise.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_125717_check.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_125746_check.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_125804_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_125825_raise.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_125836_raise.jpg': ('4', ('4', '3', '2', '1.3'), None),
    '20260818_125953_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_130014_call.jpg':  ('3', ('6', '5', '4', '3.3'), '1'),
    '20260818_130022_raise.jpg': ('4', ('4', '3', '2', '1.3'), None),
    '20260818_130033_fold.jpg':  ('7.9', ('11.9', '9.6', '7.2', '5.7'), None),
    '20260818_130050_call.jpg':  ('1.5', ('3', '4', '3', '2'), '0.5'),
    '20260818_133152_fold_badcards.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_133210_raise.jpg': ('2.5', ('3.5', '4', '3', '2'), None),
    '20260818_133220_check.jpg': ('4.5', ('4.5', '3.3', '2.2', '1.4'), None),
    '20260818_133225_raise.jpg': ('4.5', ('4.5', '3.3', '2.2', '1.4'), None),
    '20260818_133233_check.jpg': ('7.4', ('7.4', '5.6', '3.7', '2.4'), None),
    '20260818_133302_fold_badcards.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_133315_call.jpg':  ('1.5', ('3.5', '4', '3', '2'), '1'),
    '20260818_133523_fold_badcards.jpg': ('3', ('6', '5', '4', '3.3'), None),
    '20260818_133542_fold.jpg':  ('2', ('3', '4', '3', '2'), None),
    '20260818_133555_fold.jpg':  ('1.5', ('3.5', '4', '3', '2'), None),
    '20260818_133623_fold.jpg':  ('6', ('14', '11.6', '9.2', '~7.6'), None),
    '20260818_133639_raise.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_133739_fold.jpg':  ('1.5', ('3.5', '4', '3', '2'), None),
    '20260818_133810_fold.jpg':  ('5.5', ('13.5', '11.2', '9', '~7.4'), None),
    '20260818_133825_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_133852_raise.jpg': ('5.5', ('13.5', '11.2', '9', '~7.4'), None),
    '20260818_134539_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_134554_raise.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_134610_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_134637_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_134656_check.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_134701_check.jpg': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_134706_fold.jpg':  ('3', ('5', '4', '3', '2.3'), None),
    '20260818_134719_call.jpg':  ('1.5', ('3', '4', '3', '2'), '0.5'),
    '20260818_135003_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135030_raise.jpg': ('2', ('3', '4', '3', '2'), None),
    '20260818_135041_raise.jpg': ('4', ('4', '3', '2', '1.3'), None),
    '20260818_135057_raise.jpg': ('6.6', ('6.6', '4.9', '3.3', '2.1'), None),
    '20260818_135118_raise.jpg': ('11', ('11', '8.2', '5.5', '3.6'), None),
    '20260818_135129_raise.jpg': ('21.9', ('11.9', '11.9', '11.9', '11.9'), None),
    '20260818_135147_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135204_fold.jpg':  ('3', ('6', '5', '4', '3.3'), None),
    '20260818_135222_fold_badcards.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135243_fold.jpg':  ('6', (), None),
    '20260818_135257_call.jpg':  ('1.5', ('3', '4', '3', '2'), '0.5'),
    '20260818_135359_fold.jpg':  ('8', (), None),
    '20260818_135413_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135440_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135501_call.jpg':  ('9.5', (), '7.5'),
    '20260818_135527_fold_badcards.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135546_fold.jpg':  ('18.2', (), None),
    '20260818_135604_fold_badcards.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135623_fold.jpg':  ('19.7', (), None),
    '20260818_135637_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135658_call.jpg':  ('21.2', (), '15.9'),
    '20260818_135724_fold_badcards.jpg': ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135743_fold.jpg':  ('21.4', (), None),
    '20260818_135801_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
    '20260818_135819_fold.jpg':  ('22.9', (), None),
    '20260818_135833_fold.jpg':  ('1.5', ('3', '4', '3', '2'), None),
}

# Кадры 1080px (оригиналы с телефона) — тот же формат разметки.
DIGIT_FRAMES_FULL = {
    '20260818_154349_raise.png': ('4', ('4', '3', '2', '1.3'), None),
    '20260818_154732_raise.png': ('1.5', (), None),      # вскрытие: кнопок нет
    '20260818_154820_raise.png': ('4', (), None),        # вскрытие: кнопок нет
    '20260818_154841_raise.png': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_154915_check.png': ('2', ('2', '1.5', '1', '~0.6'), None),
    '20260818_154925_raise.png': ('2', ('2', '1.5', '1', '~0.6'), None),
}

DIGIT_DIRS = (('shots_digits', DIGIT_FRAMES), ('shots_audit', DIGIT_FRAMES_FULL))


def digit_labels(base=None):
    """Развернуть DIGIT_FRAMES в записи разметки цифр (только для файлов на диске).

    Каждая запись — {'file','rect','text','ink','dim'}: 'dim' помечает
    погашенную строку столбца (её текст тусклый).
    """
    base = base or config.BASE
    out = []

    def add(rel, rect, value):
        out.append({'file': rel, 'rect': list(rect), 'text': value.lstrip('~') + 'ББ',
                    'ink': 'amber', 'dim': value.startswith('~')})

    for folder, table in DIGIT_DIRS:
        for name, (pot, presets, call) in table.items():
            rel = os.path.join(folder, name)
            if not os.path.exists(os.path.join(base, rel)):
                continue
            if pot:
                add(rel, config.POT_ZONE, pot)
            # в таблице пресеты записаны сверху вниз, в config.PRESET_ROWS — снизу вверх
            for i, value in enumerate(reversed(presets)):
                if value:
                    add(rel, config.preset_rect(i), value)
            if call:
                add(rel, config.call_amount_rect(), call)
    return out


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
    """Эталоны цифр из прямоугольника с известным текстом ('2.5ББ', '15 BB').

    Буква «Б» — тоже эталон (ключ 'bb'): подпись «ББ» стоит рядом с любым
    числом тем же шрифтом и цветом, и без своего эталона она натягивается на
    ближайшую цифру.
    """
    from table_state import segment_text_glyphs   # локальный импорт: избегаем цикла
    glyphs = segment_text_glyphs(img, item['rect'], ink=item.get('ink', 'amber'))
    text = [c for c in item['text'] if not c.isspace()]
    if len(glyphs) != len(text):
        return f'глифов {len(glyphs)} != символов {len(text)} в "{item["text"]}"'
    for (_, gimg), ch in zip(glyphs, text):
        key = {'.': 'dot', ',': 'dot', 'Б': 'bb', 'B': 'bb'}.get(ch, ch)
        acc.setdefault(key, []).append(gimg)
    return None


def _average(imgs):
    """Усреднить бинарные глифы и снова бинаризовать."""
    stack = np.stack([i.astype(np.float32) for i in imgs])
    mean = stack.mean(axis=0)
    return (mean > 96).astype(np.uint8) * 255


def main():
    labels = KNOWN + digit_labels()
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(config.BASE, 'labels.json')
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            labels = json.load(f)
        print('разметка из', path)
    collect(labels)


if __name__ == '__main__':
    main()
