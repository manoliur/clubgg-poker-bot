#!/usr/bin/env python3
"""Игровой цикл v3 (правильный): сторож + Claude Code на сервере.

ВАЖНО (разобрано по кадрам):
- НЕ мой ход: внизу одна кнопка «Чек/Фолд» с зелёной галочкой (АВТОДЕЙСТВИЕ, x 45-520)
- МОЙ ход: три настоящие кнопки — «Фолд» (x 15-355), «Чек»/«Колл X» (центр ~535,2315), «Бет» (x 720-1035)
  (пиксели здесь — эталон 1080x2400; тапаем по долям от размера кадра, см. point())
- Детекция хода: дифф в зоне x 520-1080 (там при моём ходе появляются кнопки «Чек» и «Бет»)
- Чек/Колл: тап (535, 2315). Фолд: (185, 2315). Бет/рейз: (880, 2315) — в долях экрана.
- Жёлтая сумма на кнопке колл = оппонент поставил -> зовём Клода.

Использование: python claude_guard.py [--timeout N] [--interval 1.5]
"""
import subprocess, sys, os, io, time, json, argparse
from PIL import Image

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'
BASE = r'C:\Users\Vlad\clubgg_bot'
SHOTS = os.path.join(BASE, 'shots')

# Точки тапа — в ДОЛЯХ экрана (сняты с эталона 1080x2400): у телефонов разная
# высота экрана (1080x2400, 1080x2340), пиксельная точка бьёт мимо кнопки.
REF_W, REF_H = 1080, 2400
BTN_CHECK = (535 / REF_W, 2315 / REF_H)   # «Чек» / «Колл»
BTN_FOLD = (185 / REF_W, 2315 / REF_H)    # «Фолд»
BTN_BET = (880 / REF_W, 2315 / REF_H)     # «Бет 33%» / пресет рейза

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

def yellow_in_zone(img, x0, x1, y0_frac=0.86, y1_frac=0.99):
    """Жёлтые пиксели (суммы на кнопках) в заданной x-зоне."""
    W, H = img.size
    px = img.load()
    y0, y1 = int(H*y0_frac), int(H*y1_frac)
    cnt = 0
    xs, ys = [], []
    for y in range(y0, y1, 2):
        for x in range(max(0, x0), min(W, x1), 2):
            r, g, b = px[x, y]
            if r > 140 and g > 110 and b < 90:
                cnt += 1
                xs.append(x); ys.append(y)
    center = (sum(xs)//len(xs), sum(ys)//len(ys)) if cnt >= 50 else None
    return cnt, center

def tap(x, y):
    subprocess.run([ADB, '-s', SERIAL, 'shell', 'input', 'tap', str(x), str(y)], check=False)

def point(img, frac):
    """Доли экрана -> пиксели ФАКТИЧЕСКОГО кадра (он же экран телефона)."""
    W, H = img.size
    return int(round(frac[0] * W)), int(round(frac[1] * H))

def ask_claude(img_path):
    p = subprocess.run([sys.executable, os.path.join(BASE, 'claude_act.py'), img_path],
                       capture_output=True, timeout=170, cwd=BASE)
    out = p.stdout.decode('utf-8', errors='replace').strip()
    try:
        return json.loads(out[out.find('{'): out.rfind('}')+1])
    except Exception:
        return {'action': 'check', 'raise_to_bb': None, 'reason': f'parse: {out[:100]}'}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--timeout', type=int, default=0)
    ap.add_argument('--interval', type=float, default=1.5)
    ap.add_argument('--threshold', type=float, default=0.06)
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
    # зона появления настоящих кнопок: x 520-1080, y 0.86-0.99H
    W, H = base.size
    base_zone = base.crop((520, int(H*0.86), W, H))
    print('[guard] жду свой ход...', flush=True)

    start = time.time()
    streak = 0
    while True:
        if args.timeout and time.time() - start > args.timeout:
            print('TIMEOUT', flush=True); sys.exit(3)
        img = grab()
        if img is None:
            print('ERR: screencap'); sys.exit(2)
        zone = img.crop((520, int(H*0.86), W, H))
        d = diff_ratio(base_zone, zone)
        if d > args.threshold:
            streak += 1
        else:
            streak = 0
        if streak >= 2:
            # проверим: жёлтая сумма на кнопке колл (зона центра x 380-700) и на бет-кнопке (x 720-1080)
            yc_call, cc = yellow_in_zone(img, 380, 700)
            has_call = yc_call >= 100
            print(f'[guard] мой ход! diff={d:.3f} yellow_call={yc_call}', flush=True)
            shot = os.path.join(SHOTS, 'claude_turn.png')
            img.save(shot)
            if not has_call:
                # ставок нет (кнопка «Чек», а не «Колл X») -> ЧЕК по центру
                pt = point(img, BTN_CHECK)
                tap(*pt)
                print(f'AUTO_CHECK at {pt}', flush=True)
                sys.exit(0)
            # есть ставка -> зовём Клода
            decision = ask_claude(shot)
            print(f'CLAUDE: {json.dumps(decision, ensure_ascii=False)}', flush=True)
            action = decision.get('action', 'check')
            if action == 'fold':
                tap(*point(img, BTN_FOLD))
                print('TAP FOLD', flush=True)
            elif action == 'call':
                tap(*point(img, BTN_CHECK))
                print('TAP CALL', flush=True)
            elif action == 'raise':
                tap(*point(img, BTN_BET))
                print(f'TAP RAISE (raise_to={decision.get("raise_to_bb")})', flush=True)
            else:
                tap(*point(img, BTN_CHECK))
                print('TAP CHECK', flush=True)
            with open(os.path.join(BASE, 'claude_log.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps({**decision, 'ts': time.strftime('%H:%M:%S')}, ensure_ascii=False) + '\n')
            sys.exit(0)
        time.sleep(args.interval)

if __name__ == '__main__':
    main()
