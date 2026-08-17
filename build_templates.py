#!/usr/bin/env python3
"""Сбор эталонов карт ClubGG v3 — с нормализацией размера.

Все углы ресайзятся к CANON (50x66). Ранг = верхние 55% (50x36), масть = нижние 45% (50x30).
Так масштаб карты (доска 123x175 vs мои 112x148) не влияет на сравнение.
"""
import os
import cv2
import numpy as np

BASE = r'C:\Users\Vlad\clubgg_bot'
TPL = os.path.join(BASE, 'templates')
os.makedirs(TPL, exist_ok=True)

CANON_W, CANON_H = 50, 66
RANK_H = 36   # верх угла
SUIT_H = 30   # низ угла

KNOWN = [
    ('shots/turn_191709.png', 'board', ['4h', '3s', 'Kc', '5c', 'Ad']),
    ('shots/turn_191709.png', 'my',    ['Jh', '2d']),
    ('shots/now2.png',        'my',    ['8h', '2h']),
    ('shots/chk2.png',        'my',    ['7h', '6s']),
    ('shots/turn_184049.png', 'my',    ['Kc', '8c']),
    ('shots/after_call.png',  'board', ['6d', '5h', 'As']),
    ('shots/fast1.png',       'board', ['6d', '5h', 'As', '9c']),
    ('shots/fast2.png',       'board', ['6d', '5h', 'As', '9c', 'Qc']),
]

def find_board_cards(img):
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
    H, W = img.shape[:2]
    zone = img[int(H*0.70):int(H*0.84), 0:int(W*0.60)]
    gray = cv2.cvtColor(zone, cv2.COLOR_BGR2GRAY)
    _, th = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cards = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        if h < 120 or w < 100:
            continue
        full_y = int(H*0.70) + y
        if w > 150:
            half = w // 2
            cards.append((x, full_y, x + half, full_y + h))
            cards.append((x + half, full_y, x + w, full_y + h))
        else:
            cards.append((x, full_y, x + w, full_y + h))
    cards.sort(key=lambda b: b[0])
    return cards[:2]

def canon_corner(img, box):
    """Вырезать угол карты, нормализовать к CANON."""
    x0, y0, x1, y1 = box
    card = img[y0:y1, x0:x1]
    h, w = card.shape[:2]
    corner = card[0:int(h*0.45), 0:int(w*0.45)]
    corner = cv2.resize(corner, (CANON_W, CANON_H))
    return corner

def split_rank_suit(corner):
    return corner[0:RANK_H, :], corner[RANK_H:, :]

def main():
    saved_rank, saved_suit = set(), set()
    for path, zone, labels in KNOWN:
        full = os.path.join(BASE, path)
        if not os.path.exists(full):
            print(f'skip: {path}')
            continue
        img = cv2.imread(full)
        if img is None:
            continue
        boxes = find_board_cards(img) if zone == 'board' else my_card_boxes(img)
        if len(boxes) < len(labels):
            print(f'skip {path}: карт {len(boxes)} < {len(labels)}')
            continue
        for box, label in zip(boxes[:len(labels)], labels):
            corner = canon_corner(img, box)
            rank, suit = label[0], label[1]
            r_img, s_img = split_rank_suit(corner)
            r_img = cv2.cvtColor(r_img, cv2.COLOR_BGR2GRAY) if r_img.ndim == 3 else r_img
            s_img = cv2.cvtColor(s_img, cv2.COLOR_BGR2GRAY) if s_img.ndim == 3 else s_img
            # сохраняем УСРЕДНЁННЫЙ эталон: если уже есть — смешиваем
            rp, sp = os.path.join(TPL, f'rank_{rank}.png'), os.path.join(TPL, f'suit_{suit}.png')
            if rank not in saved_rank:
                cv2.imwrite(rp, r_img)
                saved_rank.add(rank)
                print(f'ранг {rank}: {path}')
            else:
                prev = cv2.imread(rp, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                cv2.imwrite(rp, ((prev + r_img.astype(np.float32)) / 2).astype(np.uint8))
            if suit not in saved_suit:
                cv2.imwrite(sp, s_img)
                saved_suit.add(suit)
                print(f'масть {suit}: {path}')
            else:
                prev = cv2.imread(sp, cv2.IMREAD_GRAYSCALE).astype(np.float32)
                cv2.imwrite(sp, ((prev + s_img.astype(np.float32)) / 2).astype(np.uint8))
    print(f'\nИтого: рангов={len(saved_rank)} мастей={len(saved_suit)}')
    print('Ранги:', sorted(saved_rank))

if __name__ == '__main__':
    main()
