#!/usr/bin/env python3
"""Распознавание карт ClubGG по скриншоту (без ИИ).

Почему старая версия возвращала '??' для моих карт
--------------------------------------------------
Старый код резал угол карты фиксированными долями (45% x 45%), ресайзил к 50x66
и делил на ранг/масть по фиксированной строке (RANK_H=36). Мои карты меньше карт
доски (~112x148 против ~123x175) и лежат веером: бокс слипшегося контура делился
ровно пополам, поэтому вырезанный «угол» правой карты уезжал в сторону, а глиф
внутри нормализованного окна не совпадал по позиции и масштабу с эталоном ->
корреляция < 0.3 -> '??'.

Что сделано
-----------
1. Глифы (ранг и масть) СЕГМЕНТИРУЮТСЯ внутри угла как связные компоненты
   «чернил» на белом фоне, а не режутся по фиксированным долям. Каждый глиф
   обрезается по своему bbox и нормализуется к канону с сохранением пропорций,
   поэтому распознавание не зависит от масштаба карты и сдвига угла.
2. Слипшийся бокс делится не пополам, а по ожидаемому соотношению сторон карты:
   левая карта начинается на x0, правая — на x1 - w_card. Для веера это даёт
   правильные углы обеих карт.
3. Ранг «10» узнаётся по двум компонентам в верхней строке -> 'T' (даже без эталона).
4. Масть сначала сужается по цвету (красная h/d, чёрная c/s), затем выбирается
   форма — так h/d и c/s больше не путаются между собой.
"""
import os
import sys
import cv2
import numpy as np

import config

RANK_ORDER = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
SUITS = ['h', 'd', 'c', 's']
RED_SUITS = ('h', 'd')
BLACK_SUITS = ('c', 's')

# канон нормализованных глифов (ранг чуть выше — цифры вытянутые)
CANON_RANK = (28, 36)   # w, h
CANON_SUIT = (28, 28)

CARD_ASPECT = 1.42      # h / w для одной карты ClubGG
MIN_SCORE = 0.45        # порог уверенности распознавания глифа

# доля карты, занимаемая угловым индексом (rank+suit).
# Ширина с запасом: «10» шире одиночного ранга, иначе второй глиф обрезается.
CORNER_W, CORNER_H = 0.64, 0.56
# отступ от края карты, чтобы не цеплять скруглённую рамку/фон
INSET = 0.06


# --------------------------------------------------------------------------
# эталоны
# --------------------------------------------------------------------------
def load_templates(tpl_dir=None):
    """Загрузить эталоны: ({ранг: img}, {масть: img}), бинарные, канонический размер."""
    tpl_dir = tpl_dir or config.TEMPLATES_DIR
    ranks, suits = {}, {}
    if not os.path.isdir(tpl_dir):
        return ranks, suits
    for f in sorted(os.listdir(tpl_dir)):
        if not f.endswith('.png'):
            continue
        img = cv2.imread(os.path.join(tpl_dir, f), cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        if f.startswith('rank_'):
            ranks[f[5:-4]] = img
        elif f.startswith('suit_'):
            suits[f[5:-4]] = img
    return ranks, suits


def save_template(name, img, tpl_dir=None):
    tpl_dir = tpl_dir or config.TEMPLATES_DIR
    os.makedirs(tpl_dir, exist_ok=True)
    cv2.imwrite(os.path.join(tpl_dir, f'{name}.png'), img)


# --------------------------------------------------------------------------
# детекция карт
# --------------------------------------------------------------------------
def white_mask(bgr, thr=170):
    """Маска «лицо карты»: светлые малонасыщенные пиксели."""
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1], hsv[:, :, 2]
    return ((v > thr) & (s < 110)).astype(np.uint8) * 255


def _split_wide(box, aspect=CARD_ASPECT):
    """Разделить слипшийся бокс на отдельные карты по ожидаемой ширине карты.

    Ширина одной карты ~ h/aspect; если карты лежат без перекрытия, ширина ~ w/n.
    Берём максимум из двух оценок: лучше захватить лишнее СЛЕВА от угла (там пустое
    лицо соседней карты), чем срезать сам индекс. Левая карта отсчитывается от
    левого края бокса, правая — от правого: у обеих угол попадает целиком.
    """
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    approx = max(1, h / aspect)
    n = max(1, int(round(w / approx)))
    if n <= 1:
        return [box]
    card_w = int(round(min(w, max(approx, w / n))))
    if n == 2:
        return [(x0, y0, x0 + card_w, y1), (x1 - card_w, y0, x1, y1)]
    step = (w - card_w) / (n - 1)
    return [(int(x0 + i * step), y0, int(x0 + i * step) + card_w, y1) for i in range(n)]


def _card_boxes_in_zone(img, zone, min_w_frac, max_w_frac, min_h_frac):
    """Найти боксы карт (белые прямоугольники) внутри зоны (доли экрана)."""
    H, W = img.shape[:2]
    zx0, zy0, zx1, zy1 = config.zone_px(zone, W, H)
    sub = img[zy0:zy1, zx0:zx1]
    if sub.size == 0:
        return []
    mask = white_mask(sub)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if w < min_w_frac * W or w > max_w_frac * W or h < min_h_frac * H:
            continue
        box = (zx0 + x, zy0 + y, zx0 + x + w, zy0 + y + h)
        parts = _split_wide(box)
        # пропорции проверяем у ОТДЕЛЬНОЙ карты, а не у слипшегося контура
        cw = parts[0][2] - parts[0][0]
        if not (1.15 < h / max(1, cw) < 1.85):
            continue
        boxes.extend(parts)
    boxes.sort(key=lambda b: b[0])
    return boxes


def find_board_cards(img):
    """Карты доски: боксы (x0,y0,x1,y1) слева направо, максимум 5."""
    boxes = _card_boxes_in_zone(img, config.BOARD_ZONE,
                                min_w_frac=0.070, max_w_frac=0.60, min_h_frac=0.045)
    return boxes[:5]


def my_card_boxes(img):
    """Мои карманные карты: боксы слева направо, максимум 2."""
    boxes = _card_boxes_in_zone(img, config.HERO_CARDS_ZONE,
                                min_w_frac=0.060, max_w_frac=0.40, min_h_frac=0.040)
    return boxes[:2]


# --------------------------------------------------------------------------
# сегментация глифов в углу карты
# --------------------------------------------------------------------------
def corner_crop(img, box):
    """Угол карты (BGR) с отступом от рамки."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    ix, iy = int(w * INSET), int(h * INSET)
    cx0, cy0 = x0 + ix, y0 + iy
    cx1, cy1 = cx0 + int(w * CORNER_W), cy0 + int(h * CORNER_H)
    cx1, cy1 = min(cx1, img.shape[1]), min(cy1, img.shape[0])
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    return img[cy0:cy1, cx0:cx1]


def ink_mask(corner_bgr):
    """Маска «чернил» глифа на белом фоне карты (тёмное ИЛИ насыщенно-красное)."""
    hsv = cv2.cvtColor(corner_bgr, cv2.COLOR_BGR2HSV)
    s, v = hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    # порог по яркости адаптивный: фон карты почти белый
    bg = np.percentile(v, 80)
    dark = v < max(120, bg * 0.75)
    colored = (s > 90) & (v > 60)
    return ((dark | colored).astype(np.uint8)) * 255


def _components(mask):
    """Значимые связные компоненты: список (x, y, w, h, area, label), сверху вниз."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    H, W = mask.shape
    min_area = max(8, int(0.006 * H * W))
    comps = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or w < 2 or h < 4:
            continue
        if w > W * 0.95 and h > H * 0.95:      # весь угол засветился — мусор
            continue
        if h > H * 0.9 and w < W * 0.3:        # вертикальная полоса фона у края
            continue
        if w > W * 0.9 and h < H * 0.3:        # горизонтальная полоса (рамка)
            continue
        comps.append((x, y, w, h, area, i))
    comps.sort(key=lambda c: c[1])
    return comps, labels


def _norm_glyph(mask, bbox, canon):
    """Вырезать глиф по bbox и нормализовать к канону с сохранением пропорций."""
    x, y, w, h = bbox
    g = mask[y:y + h, x:x + w]
    cw, ch = canon
    scale = min(cw / w, ch / h)
    nw, nh = max(1, int(round(w * scale))), max(1, int(round(h * scale)))
    g = cv2.resize(g, (nw, nh), interpolation=cv2.INTER_AREA)
    out = np.zeros((ch, cw), np.uint8)
    ox, oy = (cw - nw) // 2, (ch - nh) // 2
    out[oy:oy + nh, ox:ox + nw] = g
    return out


def extract_glyphs(corner_bgr):
    """Из угла карты выделить глифы.

    Возвращает dict: rank_img, suit_img (канон, бинарные), rank_parts (шт.),
    color ('red'/'black'), либо None если глифов не нашлось.
    """
    if corner_bgr is None or corner_bgr.size == 0:
        return None
    mask = ink_mask(corner_bgr)
    comps, labels = _components(mask)
    if not comps:
        return None

    # ранг = самый верхний компонент + соседи в той же строке (для «10» их два)
    anchor = comps[0]
    a_y0, a_y1 = anchor[1], anchor[1] + anchor[3]
    rank_comps = []
    for c in comps:
        y0, y1 = c[1], c[1] + c[3]
        overlap = min(a_y1, y1) - max(a_y0, y0)
        near_x = abs(c[0] - anchor[0]) < 2.5 * anchor[2]
        if overlap > 0.45 * min(a_y1 - a_y0, y1 - y0) and near_x:
            rank_comps.append(c)

    rx0 = min(c[0] for c in rank_comps)
    ry0 = min(c[1] for c in rank_comps)
    rx1 = max(c[0] + c[2] for c in rank_comps)
    ry1 = max(c[1] + c[3] for c in rank_comps)
    rank_img = _norm_glyph(mask, (rx0, ry0, rx1 - rx0, ry1 - ry0), CANON_RANK)

    # масть = ближайший компонент ПОД рангом, примерно под тем же x (не рисунок карты)
    rank_w = rx1 - rx0
    below = [c for c in comps
             if c not in rank_comps and c[1] >= ry1 - 0.3 * (ry1 - ry0)
             and rx0 - 1.0 * rank_w < c[0] + c[2] / 2 < rx1 + 1.0 * rank_w]
    suit_img, color = None, 'black'
    if below:
        s = min(below, key=lambda c: c[1])
        bbox = (s[0], s[1], s[2], s[3])
        rank_h = ry1 - ry0
        if s[2] > 1.8 * rank_w or s[3] > 1.8 * rank_h:
            # значок слипся с рисунком в центре карты — ограничим область под рангом
            bbox = _clip_suit_bbox(mask, bbox, rx0, rx1, rank_w, rank_h)
        suit_img = _norm_glyph(mask, bbox, CANON_SUIT)
        color = glyph_color(corner_bgr, labels == s[5])
    else:
        color = glyph_color(corner_bgr, labels == anchor[5])

    return {'rank_img': rank_img, 'suit_img': suit_img,
            'rank_parts': len(rank_comps), 'color': color, 'mask': mask}


def _clip_suit_bbox(mask, bbox, rx0, rx1, rank_w, rank_h):
    """Обрезать раздувшийся компонент масти до области ровно под рангом."""
    x, y, w, h = bbox
    cx0 = max(x, int(rx0 - 0.5 * rank_w))
    cx1 = min(x + w, int(rx1 + 0.5 * rank_w))
    cy1 = min(y + h, int(y + 1.6 * rank_h))
    if cx1 - cx0 < 4 or cy1 - y < 4:
        return bbox
    sub = mask[y:cy1, cx0:cx1]
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return bbox
    return (cx0 + xs.min(), y + ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)


def glyph_color(corner_bgr, sel):
    """Цвет глифа по пикселям маски: 'red' (h/d) или 'black' (c/s)."""
    px = corner_bgr[sel]
    if px.size == 0:
        return 'black'
    b, g, r = px[:, 0].astype(int), px[:, 1].astype(int), px[:, 2].astype(int)
    red = np.mean((r - np.maximum(g, b)) > 35)
    return 'red' if red > 0.25 else 'black'


# --------------------------------------------------------------------------
# сопоставление с эталонами
# --------------------------------------------------------------------------
def match_best(part, templates, allowed=None):
    """Нормализованная корреляция с эталонами. Возвращает (имя, score)."""
    if part is None or not templates:
        return None, 0.0
    p = part.astype(np.float32)
    p -= p.mean()
    pn = float(np.linalg.norm(p))
    if pn < 1e-6:
        return None, 0.0
    best_name, best = None, -1.0
    for name, tpl in templates.items():
        if allowed is not None and name not in allowed:
            continue
        t = tpl.astype(np.float32)
        if t.shape != p.shape:
            t = cv2.resize(t, (p.shape[1], p.shape[0])).astype(np.float32)
        t -= t.mean()
        tn = float(np.linalg.norm(t))
        if tn < 1e-6:
            continue
        score = float(np.sum(p * t) / (pn * tn))
        if score > best:
            best, best_name = score, name
    return best_name, best


def recognize_card(img, box, ranks, suits):
    """Распознать карту в боксе. Возвращает (строка_карты|None, score, инфо)."""
    corner = corner_crop(img, box)
    g = extract_glyphs(corner)
    if g is None:
        return None, 0.0, {'reason': 'no glyphs'}

    color = g['color']
    allowed_suits = RED_SUITS if color == 'red' else BLACK_SUITS
    suit, s_score = match_best(g['suit_img'], suits, allowed=allowed_suits)
    if suit is None:                       # нет эталонов масти — хотя бы цвет
        suit, s_score = ('h' if color == 'red' else 's'), 0.0

    if g['rank_parts'] >= 2:               # «10» — единственный двухглифовый ранг
        rank, r_score = 'T', 1.0
    else:
        allowed_ranks = [r for r in ranks if r != 'T'] or None
        rank, r_score = match_best(g['rank_img'], ranks, allowed=allowed_ranks)

    if rank is None:
        return None, 0.0, {'reason': 'no rank templates', 'color': color}

    score = min(r_score, s_score) if suits else r_score
    info = {'rank_score': round(r_score, 3), 'suit_score': round(s_score, 3),
            'color': color, 'rank_parts': g['rank_parts']}
    if r_score < MIN_SCORE:
        return None, score, {**info, 'reason': 'low rank score'}
    return f'{rank}{suit}', score, info


# --------------------------------------------------------------------------
# верхний уровень
# --------------------------------------------------------------------------
def read_table(img, tpl_dir=None):
    """Прочитать карты со скриншота.

    Возвращает dict: hole (2 элемента: 'Ah' или None), board (список карт),
    detail — диагностика по каждой карте.
    """
    ranks, suits = load_templates(tpl_dir)
    detail = {'hole': [], 'board': []}
    hole, board = [], []
    for box in my_card_boxes(img):
        card, score, info = recognize_card(img, box, ranks, suits)
        hole.append(card)
        detail['hole'].append({'box': box, 'card': card, 'score': round(score, 3), **info})
    for box in find_board_cards(img):
        card, score, info = recognize_card(img, box, ranks, suits)
        if card:
            board.append(card)
        detail['board'].append({'box': box, 'card': card, 'score': round(score, 3), **info})
    while len(hole) < 2:
        hole.append(None)
    return {'hole': hole[:2], 'board': board, 'detail': detail}


def debug_dump(img, out_dir, tpl_dir=None):
    """Сохранить разметку и вырезанные углы/маски — для отладки на компе."""
    os.makedirs(out_dir, exist_ok=True)
    ranks, suits = load_templates(tpl_dir)
    vis = img.copy()
    for kind, boxes in (('hole', my_card_boxes(img)), ('board', find_board_cards(img))):
        for i, box in enumerate(boxes):
            card, score, info = recognize_card(img, box, ranks, suits)
            cv2.rectangle(vis, box[:2], box[2:], (0, 255, 0), 3)
            cv2.putText(vis, f'{card or "??"} {score:.2f}', (box[0], box[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
            corner = corner_crop(img, box)
            if corner is not None:
                cv2.imwrite(os.path.join(out_dir, f'{kind}{i}_corner.png'), corner)
                g = extract_glyphs(corner)
                if g:
                    cv2.imwrite(os.path.join(out_dir, f'{kind}{i}_mask.png'), g['mask'])
                    cv2.imwrite(os.path.join(out_dir, f'{kind}{i}_rank.png'), g['rank_img'])
                    if g['suit_img'] is not None:
                        cv2.imwrite(os.path.join(out_dir, f'{kind}{i}_suit.png'), g['suit_img'])
            print(f'{kind}[{i}] box={box} -> {card} {score:.3f} {info}')
    cv2.imwrite(os.path.join(out_dir, 'detected.png'), vis)
    print('разметка:', os.path.join(out_dir, 'detected.png'))


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(config.SHOTS_DIR, 'turn_191709.png')
    image = cv2.imread(path)
    if image is None:
        print('ERR: не читается', path)
        sys.exit(2)
    if '--debug' in sys.argv:
        debug_dump(image, os.path.join(config.BASE, 'debug'))
    else:
        res = read_table(image)
        print('Мои карты:', res['hole'])
        print('Доска:    ', res['board'])
