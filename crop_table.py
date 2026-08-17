#!/usr/bin/env python3
"""Быстрый кроппер: вырезает только нужные зоны стола (мои карты, доска, банк)
в одну маленькую картинку для быстрого vision-анализа. Использование: python crop_table.py
Результат: shots/table_crop.png (компактная картинка ~700px шириной)
"""
import subprocess, sys, os, io, time
from PIL import Image

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'
SHOTS = r'C:\Users\Vlad\clubgg_bot\shots'

def grab():
    p = subprocess.run([ADB, '-s', SERIAL, 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=20)
    if len(p.stdout) < 1000:
        return None
    return Image.open(io.BytesIO(p.stdout)).convert('RGB')

def make_crop(img):
    W, H = img.size  # 1080x2400
    # зоны (эмпирические для портрета 1080x2400):
    zones = {
        'my_cards': (80, 1720, 500, 2000),     # мои карманные карты (у аватара)
        'board':    (120, 1080, 960, 1330),    # общие карты
        'pot':      (300, 900, 780, 1080),     # банк + ставки
        'opponent': (200, 380, 900, 620),      # оппонент (стек, имя)
        'buttons':  (0, 1600, 1080, 2400),     # панель кнопок действий
    }
    crops = []
    for name, box in zones.items():
        c = img.crop(box)
        # масштабируем к ширине ~340px для экономии
        nw = 340
        nh = max(1, int(c.size[1] * nw / c.size[0]))
        c = c.resize((nw, nh), Image.LANCZOS)
        crops.append(c)
    # склеиваем в столбик с подписями-разделителями
    sep = 4
    total_w = max(c.size[0] for c in crops)
    total_h = sum(c.size[1] for c in crops) + sep * (len(crops) - 1)
    canvas = Image.new('RGB', (total_w, total_h), (0, 0, 0))
    y = 0
    for c in crops:
        canvas.paste(c, (0, y))
        y += c.size[1] + sep
    return canvas

if __name__ == '__main__':
    os.makedirs(SHOTS, exist_ok=True)
    img = grab()
    if img is None:
        print('ERR'); sys.exit(2)
    out = make_crop(img)
    path = os.path.join(SHOTS, 'table_crop.png')
    out.save(path)
    print(f'SAVED {path} {out.size}')
