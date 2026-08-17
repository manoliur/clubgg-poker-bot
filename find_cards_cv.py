#!/usr/bin/env python3
"""cv2-детекция карт: найти белые прямоугольники (карты) в зоне стола.
Использование: python find_cards_cv.py <png>
"""
import sys, os
import cv2
import numpy as np

def find_cards(path, zone=None):
    img = cv2.imread(path)
    if img is None:
        print('ERR: не могу прочитать', path); return []
    H, W = img.shape[:2]
    if zone:
        x0, y0, x1, y1 = zone
        img = img[y0:y1, x0:x1]
        ox, oy = x0, y0
    else:
        ox = oy = 0
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # белые карты: порог (без морфологии — она слипает соседние карты)
    _, th = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cards = []
    for c in contours:
        x, y, w, h = cv2.boundingRect(c)
        # карта: ширина ~100-140, высота ~150-190, площадь достаточно большая
        if 90 < w < 160 and 130 < h < 210 and w * h > 15000:
            cards.append((ox + x, oy + y, ox + x + w, oy + y + h))
    # сортируем слева направо
    cards.sort(key=lambda b: b[0])
    return cards

if __name__ == '__main__':
    path = sys.argv[1]
    # зона стола: вся середина экрана
    img = cv2.imread(path)
    H, W = img.shape[:2]
    cards = find_cards(path, (0, int(H*0.15), W, int(H*0.85)))
    print(f'Найдено карт: {len(cards)}')
    for b in cards:
        print(f'  box=({b[0]},{b[1]})-({b[2]},{b[3]}) w={b[2]-b[0]} h={b[3]-b[1]}')
    # визуализация
    vis = img.copy()
    for b in cards:
        cv2.rectangle(vis, (b[0], b[1]), (b[2], b[3]), (0, 255, 0), 3)
    cv2.imwrite(os.path.join(os.path.dirname(path), 'cards_detected.png'), vis)
    print('визуализация: cards_detected.png')
