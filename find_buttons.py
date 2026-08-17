#!/usr/bin/env python3
"""Найти кнопки действий на экране покерного стола по цвету.
Использование: python find_buttons.py [png]
Ищет в нижней 30% экрана связные области ярких цветов (кнопки)
и печатает их центры + размеры. Красный=фолд, зелёный=чек/колл, оранжевый=рейз.
"""
import sys, os
from PIL import Image

def find_buttons(path):
    img = Image.open(path).convert('RGB')
    W, H = img.size
    px = img.load()
    # нижняя 30% экрана
    y0 = int(H * 0.70)
    # собираем пиксели кнопок: насыщенные, яркие (не серые, не тёмные)
    points = {'red': [], 'green': [], 'orange': [], 'blue': []}
    step = 3
    for y in range(y0, H, step):
        for x in range(0, W, step):
            r, g, b = px[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 90 or mx - mn < 40:
                continue  # тёмное или серое
            if r > 150 and g < 110 and b < 110:
                points['red'].append((x, y))
            elif g > 120 and r < 130 and b < 130:
                points['green'].append((x, y))
            elif r > 180 and g > 110 and b < 90:
                points['orange'].append((x, y))
            elif b > 140 and r < 120 and g < 140:
                points['blue'].append((x, y))
    res = {}
    for color, pts in points.items():
        if len(pts) < 30:
            continue
        xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
        cx, cy = sum(xs)//len(xs), sum(ys)//len(ys)
        res[color] = {'center': (cx, cy), 'count': len(pts),
                      'bbox': (min(xs), min(ys), max(xs), max(ys))}
    return res

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else r'C:\Users\Vlad\clubgg_bot\shots\now.png'
    print(path)
    for color, info in find_buttons(path).items():
        print(f"{color}: center={info['center']} count={info['count']} bbox={info['bbox']}")
