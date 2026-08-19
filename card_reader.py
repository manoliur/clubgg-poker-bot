#!/usr/bin/env python3
"""Распознавание карт ClubGG по скриншоту (без ИИ).

Как читается карта
------------------
1. Карты доски находятся контуром белого лица; слипшийся контур делится по
   ожидаемой ширине карты (высота / CARD_ASPECT).
2. Мои карманные карты геометрией не берутся: они лежат веером внахлёст, снизу
   их режет плашка игрока, а слева к белому пятну липнет светлое кольцо аватара.
   Поэтому индекс (ранг+масть) обеих карт берётся фиксированными окнами
   config.HERO_INDEX_RECTS — раскладка клиента неподвижна.
3. Глифы внутри окна выделяются как ДЫРКИ в лице карты: берём самое крупное
   белое пятно, и всё не-белое, полностью окружённое им, и есть индекс. Так
   сами собой отсекаются сукно, тень, соседняя карта и крупный рисунок в центре
   карты (он упирается в край окна и перестаёт быть дыркой), а порог яркости
   больше не приходится подгонять под фон.
4. Ранг «10» узнаётся по двум компонентам в верхней строке и широкому боксу.
5. Масть сначала сужается по цвету (красная h/d, чёрная c/s), затем выбирается
   форма — так h/d и c/s не путаются между собой.
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
T_WIDTH = 0.9           # «10» шире единичного ранга: w/h бокса не меньше этого

# доля карты, занимаемая угловым индексом (rank+suit).
# Ширина с запасом: «10» шире одиночного ранга, иначе второй глиф обрезается.
CORNER_W, CORNER_H = 0.64, 0.56
# отступ от края карты, чтобы не цеплять скруглённую рамку/фон
INSET = 0.06

# различение 2 и 7 по нижней черте глифа (см. resolve_2_vs_7)
BOTTOM_BAR_FRAC = 0.28      # какая доля высоты глифа считается «низом»
BOTTOM_BAR_MARGIN = 0.05    # мёртвая зона вокруг середины между эталонами
BOTTOM_BAR_MIN_GAP = 0.20   # эталоны ближе этого — признак не работает


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
        # лицо карты — сплошная заливка; светящаяся рамка плашки игрока имеет
        # такой же бокс, но внутри пустая, и раньше считалась картой
        if cv2.contourArea(c) < 0.6 * w * h:
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


def my_index_rects(img):
    """Окна индексов моих карманных карт: (x0,y0,x1,y1) слева направо.

    Окна фиксированы (config.HERO_INDEX_RECTS) и масштабируются под размер
    скриншота; отдаются только те, где действительно лежит лицо карты.
    """
    H, W = img.shape[:2]
    rects = []
    for rect in config.HERO_INDEX_RECTS:
        x0, y0, x1, y1 = config.rect_px(rect, W, H)
        crop = img[max(0, y0):y1, max(0, x0):x1]
        if crop.size == 0 or card_face(crop) is None:
            continue
        rects.append((x0, y0, x1, y1))
    return rects


# --------------------------------------------------------------------------
# сегментация глифов в углу карты
# --------------------------------------------------------------------------
def index_crop(img, rect):
    """Кроп по готовому прямоугольнику (x0,y0,x1,y1) экрана."""
    x0, y0, x1, y1 = rect
    return img[max(0, y0):y1, max(0, x0):x1]


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


def card_face(corner_bgr, min_frac=0.35):
    """Лицо карты в окне: самое крупное белое пятно, или None если карты нет."""
    face = white_mask(corner_bgr, thr=150)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(face, 8)
    if n < 2:
        return None
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    if stats[big, cv2.CC_STAT_AREA] < min_frac * face.size:
        return None
    return (labels == big).astype(np.uint8) * 255


def ink_mask(corner_bgr, allow_bottom=False):
    """Маска глифов индекса: дырки внутри лица карты.

    Дырка = не-белая область, не касающаяся края окна. Всё, что упирается в
    край (сукно, тень, соседняя карта, рисунок в центре карты), отсекается.
    allow_bottom разрешает касание нижнего края — для случая, когда индекс
    снизу перекрыт всплывающей плашкой и глиф масти обрезан.
    """
    face = card_face(corner_bgr)
    if face is None:
        return None
    H, W = face.shape
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(face), 4)
    holes = np.zeros_like(face)
    for i in range(1, n):
        x, y, w, h, _ = stats[i]
        if x == 0 or y == 0 or x + w == W:
            continue
        if y + h == H and not allow_bottom:
            continue
        holes[labels == i] = 255
    return holes


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
    """Из окна индекса выделить глифы ранга и масти.

    Возвращает dict: rank_img, suit_img (канон, бинарные), rank_parts (шт.),
    rank_wide (ширина/высота бокса ранга — у «10» она заметно больше),
    color ('red'/'black'), mask; либо None если глифов не нашлось.
    """
    if corner_bgr is None or corner_bgr.size == 0:
        return None
    g = _glyphs_from_mask(corner_bgr, ink_mask(corner_bgr))
    if g is not None and g['suit_img'] is None:
        # масть срезана снизу всплывающей плашкой («Бет», панель игрока): режем
        # окно по её краю и разрешаем усечённому глифу упираться в низ окна
        cut = _cut_at_overlap(corner_bgr)
        if cut is not None:
            alt = _glyphs_from_mask(cut, ink_mask(cut, allow_bottom=True))
            if alt is not None and alt['suit_img'] is not None:
                return alt
    return g


def _cut_at_overlap(corner_bgr):
    """Окно, обрезанное по верхнему краю перекрывающей карту плашки, или None."""
    face = card_face(corner_bgr)
    if face is None:
        return None
    H, W = face.shape
    wide = (face > 0).sum(axis=1) >= 0.25 * W        # строка идёт по лицу карты
    if wide.all():
        return None
    top = int(np.argmax(wide))
    cut = top + int(np.argmin(wide[top:]))
    return corner_bgr[:cut] if cut > top + H // 3 else None


def _glyphs_from_mask(corner_bgr, mask):
    if mask is None:
        return None
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
    rank_w, rank_h = rx1 - rx0, ry1 - ry0
    rank_img = _norm_glyph(mask, (rx0, ry0, rank_w, rank_h), CANON_RANK)

    # масть = ближайший компонент ПОД рангом, примерно под тем же x (не рисунок карты)
    below = [c for c in comps
             if c not in rank_comps and c[1] >= ry1 - 0.3 * rank_h
             and rx0 - 1.0 * rank_w < c[0] + c[2] / 2 < rx1 + 1.0 * rank_w]
    suit_img, suit_raw, sel, trunc = None, None, None, False
    if below:
        s = min(below, key=lambda c: c[1])
        bbox = (s[0], s[1], s[2], s[3])
        # значок масти примерно одного размера с рангом; вдвое больший — слипся
        # с рисунком в центре карты, оставляем от него область под рангом
        if s[2] > 2.0 * rank_w or s[3] > 2.0 * rank_h:
            bbox = _clip_suit_bbox(mask, bbox, rx0, rx1, rank_w, rank_h)
        x, y, w, h = bbox
        suit_raw = mask[y:y + h, x:x + w]
        suit_img = _norm_glyph(mask, bbox, CANON_SUIT)
        sel = (labels == s[5])
        trunc = y + h >= mask.shape[0]          # значок упёрся в низ окна — обрезан

    ink = np.zeros(mask.shape, bool)
    for c in rank_comps:
        ink |= (labels == c[5])
    if sel is not None:
        ink |= sel
    return {'rank_img': rank_img, 'suit_img': suit_img, 'suit_raw': suit_raw,
            'suit_trunc': trunc, 'rank_parts': len(rank_comps),
            'rank_wide': rank_w / max(1, rank_h), 'color': glyph_color(corner_bgr, ink),
            'mask': mask}


def _clip_suit_bbox(mask, bbox, rx0, rx1, rank_w, rank_h):
    """Обрезать раздувшийся компонент масти до области ровно под рангом."""
    x, y, w, h = bbox
    cx0 = max(x, int(rx0 - 0.6 * rank_w))
    cx1 = min(x + w, int(rx1 + 0.6 * rank_w))
    cy1 = min(y + h, int(y + 1.4 * rank_h))
    if cx1 - cx0 < 4 or cy1 - y < 4:
        return bbox
    ys, xs = np.nonzero(mask[y:cy1, cx0:cx1])
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


def bottom_bar_ratio(glyph, frac=BOTTOM_BAR_FRAC):
    """Доля колонок канона, занятых чернилами в нижних frac высоты глифа.

    У двойки низ — горизонтальная черта во всю ширину, у семёрки там только
    хвост диагонали. На живых эталонах: rank_2 = 0.61, rank_7 = 0.29.
    """
    if glyph is None or glyph.size == 0:
        return 0.0
    ink = glyph > 127
    h = ink.shape[0]
    k = max(1, int(round(h * frac)))
    return float(ink[h - k:].any(axis=0).mean())


def resolve_2_vs_7(glyph, s2, s7, tpl_2=None, tpl_7=None):
    """Развести 2 и 7 по нижней черте глифа. Возвращает (ранг, скор).

    Корреляция их путает: мои карты лежат веером, у наклонённой семёрки
    диагональ уходит влево так же, как у двойки, и скор двойки выходит даже
    выше (на живых кадрах 0.49-0.56 против 0.28-0.41 у настоящего эталона 7).
    Отличает их низ глифа — см. bottom_bar_ratio.

    Порог берётся не константой, а серединой между эталонами: шрифт эталонов
    может быть любым (в тестах глифы рисуются другим шрифтом, и абсолютный
    порог, подобранный под ClubGG, ломал бы распознавание семёрок). Вокруг
    середины оставлена мёртвая зона BOTTOM_BAR_MARGIN, а слишком похожие
    эталоны (разница меньше BOTTOM_BAR_MIN_GAP) признаком не разводятся вовсе.

    Скор возвращается лучший из двух: когда признак спорит с корреляцией,
    корреляция у наклонённого глифа врёт, и её значение говорит лишь «глиф
    похож на цифру этой пары» — иначе верно опознанная карта отсеклась бы
    порогом MIN_SCORE.
    """
    pick, best = ('2', s2) if s2 >= s7 else ('7', s7)
    if tpl_2 is None or tpl_7 is None:
        return pick, best
    b2, b7 = bottom_bar_ratio(tpl_2), bottom_bar_ratio(tpl_7)
    if b2 - b7 < BOTTOM_BAR_MIN_GAP:
        return pick, best
    mid = (b2 + b7) / 2
    bar = bottom_bar_ratio(glyph)
    if bar < mid - BOTTOM_BAR_MARGIN:
        pick = '7'
    elif bar > mid + BOTTOM_BAR_MARGIN:
        pick = '2'
    return pick, max(s2, s7)


def _ink_bbox(img):
    """Бокс непустых пикселей (x, y, w, h) или None."""
    ys, xs = np.nonzero(img)
    if len(xs) == 0:
        return None
    return xs.min(), ys.min(), xs.max() - xs.min() + 1, ys.max() - ys.min() + 1


def match_suit(g, suits, allowed):
    """Масть по глифу.

    Значок, срезанный снизу плашкой, нормализация растягивает на всю высоту
    канона, и обрубок трефы становится похож на пику. Поэтому у обрезанного
    глифа эталоны режутся сверху той же долей: сравниваются одинаковые куски.
    """
    if not g['suit_trunc'] or g['suit_raw'] is None:
        return match_best(g['suit_img'], suits, allowed=allowed)
    rh, rw = g['suit_raw'].shape
    best_name, best = None, -1.0
    for name in allowed:
        tpl = suits.get(name)
        bbox = _ink_bbox(tpl) if tpl is not None else None
        if bbox is None:
            continue
        x, y, w, h = bbox
        # глиф и эталон подобны, ширина у обрезанного значка сохранилась
        cut = int(round(h * rh / max(1.0, rw * h / w)))
        if not 3 <= cut < h:
            continue
        _, score = match_best(g['suit_img'], {name: _norm_glyph(tpl, (x, y, w, cut), CANON_SUIT)})
        if score > best:
            best, best_name = score, name
    if best_name is None:                  # обрезка не применима — обычное сравнение
        return match_best(g['suit_img'], suits, allowed=allowed)
    return best_name, best


def recognize_corner(corner, ranks, suits):
    """Распознать карту по окну индекса. Возвращает (строка_карты|None, score, инфо)."""
    g = extract_glyphs(corner)
    if g is None:
        return None, 0.0, {'reason': 'no glyphs'}

    color = g['color']
    allowed_suits = RED_SUITS if color == 'red' else BLACK_SUITS
    suit, s_score = match_suit(g, suits, allowed_suits)
    if suit is None:                       # нет эталонов масти — хотя бы цвет
        suit, s_score = ('h' if color == 'red' else 's'), 0.0

    # «10» — единственный ранг из двух глифов; эталон T сравнивается как обычный,
    # а широкий бокс — запасной путь, если эталона T нет (живой шрифт компактный,
    # wide=0.7, поэтому без эталона десятка не читалась вовсе)
    bar = None
    if g['rank_parts'] >= 2 and g['rank_wide'] >= T_WIDTH:
        rank, r_score = 'T', 1.0
    else:
        rank, r_score = match_best(g['rank_img'], ranks)
        # 2 и 7 корреляция путает — разводим их структурным признаком
        if rank in ('2', '7') and '2' in ranks and '7' in ranks:
            other = '7' if rank == '2' else '2'
            _, o_score = match_best(g['rank_img'], ranks, allowed={other})
            s2, s7 = (r_score, o_score) if rank == '2' else (o_score, r_score)
            rank, r_score = resolve_2_vs_7(g['rank_img'], s2, s7,
                                           ranks['2'], ranks['7'])
            bar = round(bottom_bar_ratio(g['rank_img']), 3)

    if rank is None:
        return None, 0.0, {'reason': 'no rank templates', 'color': color}

    score = min(r_score, s_score) if suits else r_score
    info = {'rank_score': round(r_score, 3), 'suit_score': round(s_score, 3),
            'color': color, 'rank_parts': g['rank_parts']}
    if bar is not None:
        info['bottom_bar'] = bar
    if r_score < MIN_SCORE:
        return None, score, {**info, 'reason': 'low rank score'}
    return f'{rank}{suit}', score, info


def recognize_card(img, box, ranks, suits):
    """Распознать карту в боксе (доска). Возвращает (строка_карты|None, score, инфо)."""
    return recognize_corner(corner_crop(img, box), ranks, suits)


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
    for rect in my_index_rects(img):
        card, score, info = recognize_corner(index_crop(img, rect), ranks, suits)
        hole.append(card)
        detail['hole'].append({'box': rect, 'card': card, 'score': round(score, 3), **info})
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
    for kind, boxes in (('hole', my_index_rects(img)), ('board', find_board_cards(img))):
        for i, box in enumerate(boxes):
            corner = index_crop(img, box) if kind == 'hole' else corner_crop(img, box)
            card, score, info = recognize_corner(corner, ranks, suits)
            cv2.rectangle(vis, box[:2], box[2:], (0, 255, 0), 3)
            cv2.putText(vis, f'{card or "??"} {score:.2f}', (box[0], box[1] - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
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
