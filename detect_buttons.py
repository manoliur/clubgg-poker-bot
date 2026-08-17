#!/usr/bin/env python3
"""Детекция кнопок действий ClubGG по нейтрально-серому цвету (#1e1e1e-#333).
Кнопки НЕ цветные — они тёмно-серые, в нижней полосе y 2100-2380.
Логика:
- 1 кнопка слева -> «Чек/Фолд» (ставок нет) -> тап = ЧЕК (безопасно)
- 2+ кнопки -> «Фолд» + «Колл X» (+ пресеты рейза) -> есть ставка -> НУЖНО РЕШЕНИЕ
Использование: python detect_buttons.py [png|live]
"""
import subprocess, sys, os, io, json
from PIL import Image

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'

def grab():
    p = subprocess.run([ADB, '-s', SERIAL, 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=20)
    if len(p.stdout) < 1000:
        return None
    return Image.open(io.BytesIO(p.stdout)).convert('RGB')

def detect(img):
    """Возвращает список кнопок: [{'x','y','w'}] в нижней полосе."""
    W, H = img.size
    px = img.load()
    # нижняя полоса: y 2100-2380 (кнопки), выше 2380 - навигация Android
    y0, y1 = int(H*0.875), int(H*0.992)
    cols = {}
    for y in range(y0, y1, 2):
        for x in range(0, W, 2):
            r, g, b = px[x, y]
            # нейтральный серый: каналы близки, яркость 25-90 (не чёрный, не белый)
            if abs(r-g) < 14 and abs(g-b) < 14 and 25 < r < 95:
                cols.setdefault(x, 0)
                cols[x] += 1
    # группируем x в кластеры (кнопки шириной ~300px, шаг между ними > 60px)
    xs = sorted(cols)
    clusters = []
    cur = []
    for x in xs:
        if cols[x] < 2:
            continue
        if cur and x - cur[-1] > 60:
            clusters.append(cur); cur = []
        cur.append(x)
    if cur: clusters.append(cur)
    # кластер должен иметь минимум 40 точек (кнопка) и ширину > 100px
    buttons = []
    for c in clusters:
        if len(c) < 40:
            continue
        x0, x1 = c[0], c[-1]
        w = x1 - x0
        if w < 100:
            continue
        buttons.append({'x': (x0+x1)//2, 'y': (y0+y1)//2, 'w': w, 'x0': x0, 'x1': x1})
    buttons.sort(key=lambda b: b['x'])
    return buttons

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else 'live'
    if path == 'live':
        img = grab()
        if img is None:
            print('ERR'); sys.exit(2)
    else:
        img = Image.open(path).convert('RGB')
    btns = detect(img)
    print(json.dumps(btns, ensure_ascii=False))
    n = len(btns)
    if n == 0:
        print('STATUS: no buttons (not my turn / no hand)')
    elif n == 1:
        print('STATUS: CHECK available (single Чек/Фолд button) -> tap = CHECK')
    else:
        print(f'STATUS: DECISION needed ({n} buttons: fold/call/raise)')
