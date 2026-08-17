#!/usr/bin/env python3
"""Умный тап: снять скриншот, найти кнопку нужного цвета, тапнуть в её центр.
Использование: python smart_tap.py [check|fold|raise|call]
  check/call  — зелёная кнопка (чек/колл)
  fold        — красная кнопка (фолд)
  raise       — оранжевая кнопка (рейз/быстрая ставка)
Возвращает координаты, куда тапнул. Если кнопка не найдена — код 3.
"""
import subprocess, sys, os, io, time
from PIL import Image

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'

COLOR_RULES = {
    'check': lambda r, g, b: g > 120 and r < 130 and b < 130,   # зелёная
    'call':  lambda r, g, b: g > 120 and r < 130 and b < 130,
    'fold':  lambda r, g, b: r > 150 and g < 110 and b < 110,   # красная
    'raise': lambda r, g, b: r > 180 and g > 110 and b < 90,    # оранжевая
}
MIN_POINTS = 80
STEP = 3

def grab():
    p = subprocess.run([ADB, '-s', SERIAL, 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=20)
    if len(p.stdout) < 1000:
        return None
    return Image.open(io.BytesIO(p.stdout)).convert('RGB')

def find_button(img, color):
    W, H = img.size
    px = img.load()
    y0 = int(H * 0.60)
    rule = COLOR_RULES[color]
    pts = []
    for y in range(y0, H, STEP):
        for x in range(0, W, STEP):
            r, g, b = px[x, y]
            mx, mn = max(r, g, b), min(r, g, b)
            if mx < 90 or mx - mn < 40:
                continue
            if rule(r, g, b):
                pts.append((x, y))
    if len(pts) < MIN_POINTS:
        return None
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    return (sum(xs)//len(xs), sum(ys)//len(ys), len(pts))

if __name__ == '__main__':
    color = sys.argv[1] if len(sys.argv) > 1 else 'check'
    img = grab()
    if img is None:
        print('ERR: no screenshot'); sys.exit(2)
    res = find_button(img, color)
    if res is None:
        print(f'NOT_FOUND {color}')
        sys.exit(3)
    x, y, n = res
    subprocess.run([ADB, '-s', SERIAL, 'shell', 'input', 'tap', str(x), str(y)],
                   check=False)
    print(f'TAPPED {color} at {x},{y} (points={n})')
