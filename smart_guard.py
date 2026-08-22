#!/usr/bin/env python3
"""ПРАВИЛЬНЫЙ автономный страж стола ClubGG v2.

Кнопки ClubGG тёмно-серые, фон панели тоже серый -> по цвету не отличить.
Признаки:
- МОЙ ХОД: нижняя зона (y>0.86H) изменилась относительно базового кадра (появились кнопки)
- СТАВКА ЕСТЬ: жёлтая сумма на кнопке колла («Колл 0.5 ББ» жёлтым) > 100 пикселей
- Кнопка «Чек/Фолд»: фиксированная точка (283, 2315) эталона 1080x2400 — тапаем её в долях экрана
- Кнопка «Колл»: центр жёлтых пикселей

Логика:
1. Стабилизация базового кадра.
2. Ждём diff нижней зоны > порога 2 кадра подряд (кнопки появились = мой ход).
3. yellow < 100 -> АВТО-ЧЕК в центр кнопки «Чек/Фолд». yellow >= 100 -> NEED_DECISION (кадр + центр колла).

Использование: python smart_guard.py [--timeout N] [--interval 1.5] [--threshold 0.08]
Выход: 0 = авто-чек, 1 = нужно решение, 3 = таймаут
"""
import subprocess, sys, os, io, time, json, argparse
from PIL import Image

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'
SHOTS = r'C:\Users\Vlad\clubgg_bot\shots'
# Точка тапа — в ДОЛЯХ экрана (снята с эталона 1080x2400): у телефонов разная
# высота экрана (1080x2400, 1080x2340), пиксельная точка бьёт мимо кнопки.
REF_W, REF_H = 1080, 2400
CHECK_POINT = (283 / REF_W, 2315 / REF_H)  # кнопка «Чек/Фолд»

def grab():
    p = subprocess.run([ADB, '-s', SERIAL, 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=20)
    if len(p.stdout) < 1000:
        return None
    return Image.open(io.BytesIO(p.stdout)).convert('RGB')

def diff_ratio(a, b, threshold=18):
    da, db = a.convert('L'), b.convert('L')
    if da.size != db.size:
        return 1.0
    pa, pb = da.load(), db.load()
    w, h = da.size
    changed = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            if abs(pa[x, y] - pb[x, y]) > threshold:
                changed += 1
    total = (w // 3) * (h // 3)
    return changed / total

def yellow_center(img):
    """Центр жёлтых пикселей (сумма на кнопке колла) и их число."""
    W, H = img.size
    px = img.load()
    y0, y1 = int(H*0.86), int(H*0.99)
    xs, ys = [], []
    for y in range(y0, y1, 2):
        for x in range(0, W, 2):
            r, g, b = px[x, y]
            if r > 140 and g > 110 and b < 90:
                xs.append(x); ys.append(y)
    if len(xs) < 100:
        return None, len(xs)
    return (sum(xs)//len(xs), sum(ys)//len(ys)), len(xs)

def tap(x, y):
    subprocess.run([ADB, '-s', SERIAL, 'shell', 'input', 'tap', str(x), str(y)], check=False)

def point(img, frac):
    """Доли экрана -> пиксели ФАКТИЧЕСКОГО кадра (он же экран телефона)."""
    W, H = img.size
    return int(round(frac[0] * W)), int(round(frac[1] * H))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=int, default=0)
    ap.add_argument('--interval', type=float, default=1.5)
    ap.add_argument('--threshold', type=float, default=0.08)
    args = ap.parse_args()
    os.makedirs(SHOTS, exist_ok=True)

    base = None
    for _ in range(3):
        img = grab()
        if img is None:
            print('ERR: screencap'); sys.exit(2)
        if base is not None and diff_ratio(base, img) < 0.02:
            base = img; break
        base = img
        time.sleep(args.interval)
    W, H = base.size
    base_bottom = base.crop((0, int(H*0.86), W, H))
    print('[guard] жду свой ход...', flush=True)

    start = time.time()
    streak = 0
    while True:
        if args.timeout and time.time() - start > args.timeout:
            print('TIMEOUT', flush=True); sys.exit(3)
        img = grab()
        if img is None:
            print('ERR: screencap'); sys.exit(2)
        bottom = img.crop((0, int(H*0.86), W, H))
        d = diff_ratio(base_bottom, bottom)
        if d > args.threshold:
            streak += 1
        else:
            streak = 0
        if streak >= 2:
            yc, yn = yellow_center(img)
            print(f'[guard] кнопки появились! diff={d:.3f} yellow={yn}', flush=True)
            if yn < 100:
                pt = point(img, CHECK_POINT)
                tap(*pt)
                print(f'AUTO_CHECK at {pt}', flush=True)
                sys.exit(0)
            else:
                path = os.path.join(SHOTS, 'need_decision.png')
                img.save(path)
                with open(os.path.join(SHOTS, 'need_decision.json'), 'w', encoding='utf-8') as f:
                    json.dump({'yellow': yn, 'yellow_center': yc, 'diff': d}, f, ensure_ascii=False)
                print(f'NEED_DECISION: {path} yellow={yn} call_btn={yc}', flush=True)
                sys.exit(1)
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
