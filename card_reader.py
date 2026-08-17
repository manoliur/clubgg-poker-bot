#!/usr/bin/env python3
"""Распознавание карт ClubGG по эталонам (без ИИ).

Сравнение угла карты с эталонами через cv2.matchTemplate (TM_CCOEFF_NORMED).
Доска: cv2-детекция карт. Мои карты: фиксированные зоны.
"""
import os
import cv2
import numpy as np

BASE = r'C:\Users\Vlad\clubgg_bot'
TPL = os.path.join(BASE, 'templates')
RANK_ORDER = ['2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K', 'A']
CANON_W, CANON_H = 50, 66
RANK_H = 36

def load_templates():
    """Загрузить эталоны: {ранг: img}, {масть: img} (уже нормализованные)."""
    ranks, suits = {}, {}
    for f in os.listdir(TPL):
        if f.startswith('rank_'):
            ranks[f[5:-4]] = cv2.imread(os.path.join(TPL, f), cv2.IMREAD_GRAYSCALE)
        elif f.startswith('suit_'):
            suits[f[5:-4]] = cv2.imread(os.path.join(TPL, f), cv2.IMREAD_GRAYSCALE)
    return ranks, suits

def canon_corner_from_box(img, box):
    """Угол карты, нормализованный к CANON (50x66)."""
    x0, y0, x1, y1 = box
    card = img[y0:y1, x0:x1]
    h, w = card.shape[:2]
    corner = card[0:int(h*0.45), 0:int(w*0.45)]
    if corner.size == 0:
        return None
    return cv2.resize(corner, (CANON_W, CANON_H))

def split_rank_suit(corner):
    return corner[0:RANK_H, :], corner[RANK_H:, :]

def match_best(part, templates):
    """Сравнить часть с эталонами через нормализованную корреляцию (numpy).
    Возвращает (имя, score)."""
    best_name, best_score = None, -2.0
    p = part.astype(np.float32)
    p = p - p.mean()
    pn = np.linalg.norm(p)
    if pn < 1e-6:
        return best_name, 0.0
    for name, tpl in templates.items():
        t = tpl.astype(np.float32)
        if t.shape != p.shape:
            t = cv2.resize(t, (p.shape[1], p.shape[0])).astype(np.float32)
        t = t - t.mean()
        tn = np.linalg.norm(t)
        if tn < 1e-6:
            continue
        score = float(np.sum(p * t) / (pn * tn))
        if score > best_score:
            best_score, best_name = score, name
    return best_name, best_score

def recognize_corner(corner, ranks, suits):
    """Распознать карту по нормализованному углу (CANON). Возвращает (ранг, масть, score)."""
    if corner is None or corner.size == 0:
        return '?', '?', 0.0
    if corner.ndim == 3:
        corner = cv2.cvtColor(corner, cv2.COLOR_BGR2GRAY)
    r_part, s_part = split_rank_suit(corner)
    rank, rs = match_best(r_part, ranks)
    suit, ss = match_best(s_part, suits)
    return rank, suit, min(rs, ss)

def find_board_cards(img):
    """cv2-детекция карт доски. Список боксов (x0,y0,x1,y1)."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cards = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if 90 < w < 160 and 130 < h < 210 and w * h > 15000:
            cards.append((x, y, x + w, y + h))
    cards.sort(key=lambda b: b[0])
    return cards

def my_card_boxes(img):
    """Мои карты в нижней зоне стола. Контур карт слипается в один (веер),
    делим пополам на две карты."""
    H, W = img.shape[:2]
    zone = img[int(H*0.70):int(H*0.84), 0:int(W*0.60)]
    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cards = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # карты: высота > 120 (панель имени ниже и плоская)
        if h < 120 or w < 100:
            continue
        full_y = int(H*0.70) + y
        if w > 150:
            # две слипшиеся карты: делим пополам
            half = w // 2
            cards.append((x, full_y, x + half, full_y + h))
            cards.append((x + half, full_y, x + w, full_y + h))
        else:
            cards.append((x, full_y, x + w, full_y + h))
    cards.sort(key=lambda b: b[0])
    return cards[:2]

def suit_color(img, box):
    """Цветовой признак масти в углу карты: red (h/d) / black (c/s)."""
    x0, y0, x1, y1 = box
    card = img[y0:y1, x0:x1]
    h, w = card.shape[:2]
    corner = card[0:int(h*0.45), 0:int(w*0.45)]
    # нижняя часть угла = масть
    suit_part = corner[int(corner.shape[0]*0.5):, :]
    b, g, r = cv2.split(suit_part)
    red_px = np.sum((r.astype(int) - g.astype(int) > 40) & (r.astype(int) - b.astype(int) > 40))
    total = suit_part.shape[0] * suit_part.shape[1]
    return 'red' if red_px > total * 0.02 else 'black'

def read_table(img):
    """Прочитать карты со стола. Возвращает (мои_карты, доска, confidence)."""
    ranks, suits = load_templates()
    my_cards, board = [], []
    # мои карты через cv2
    for box in my_card_boxes(img):
        x0, y0, x1, y1 = box
        card = img[y0:y1, x0:x1]
        gray = cv2.cvtColor(card, cv2.COLOR_BGR2GRAY)
        white = np.sum(gray > 180)
        if white < 500:
            my_cards.append(None)
            continue
        corner = canon_corner_from_box(img, box)
        rank, suit, score = recognize_corner(corner, ranks, suits)
        # коррекция масти по цвету
        col = suit_color(img, box)
        if col == 'red' and suit not in ('h', 'd'):
            suit = 'h'  # или d — уточним по эталонам
        elif col == 'black' and suit not in ('c', 's'):
            suit = 's'
        my_cards.append(f'{rank}{suit}' if score > 0.3 else '??')
    # доска
    for box in find_board_cards(img):
        corner = canon_corner_from_box(img, box)
        rank, suit, score = recognize_corner(corner, ranks, suits)
        col = suit_color(img, box)
        if col == 'red' and suit not in ('h', 'd'):
            suit = 'h'
        elif col == 'black' and suit not in ('c', 's'):
            suit = 's'
        board.append(f'{rank}{suit}' if score > 0.3 else '??')
    return my_cards, board

if __name__ == '__main__':
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else 'shots/turn_191709.png'
    img = cv2.imread(path)
    my, board = read_table(img)
    print(f'Мои карты: {my}')
    print(f'Доска: {board}')
