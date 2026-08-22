#!/usr/bin/env python3
"""ПОЛНОСТЬЮ АВТОНОМНЫЙ покерный бот ClubGG (heads-up NLH).

Работает БЕЗ участия агента, в бесконечном цикле:
1. Следит за столом (скриншот каждые ~1.2 сек), базовый кадр обновляется скользяще.
2. Определяет позицию: кнопка D у меня (внизу) = SB, у оппонента (вверху) = BB.
   - Префлоп: первым ходит SB (у кого D). Постфлоп: первым ходит BB.
3. МОЙ ХОД = в зоне x 520-1080 появились настоящие кнопки (Чек/Колл/Бет).
   - Жёлтой суммы на кнопке колл нет -> ставок нет -> МГНОВЕННЫЙ ЧЕК в центр кнопки (доли экрана)
   - Жёлтая сумма есть -> оппонент поставил -> Клод на сервере решает -> тап
4. После действия обновляет базу и ждёт следующий ход. Крутится вечно.

Запуск: python auto_bot.py            (в фоне, без моего участия)
Лог:   auto_bot.log, claude_log.jsonl
"""
import subprocess, sys, os, io, time, json
from PIL import Image

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'
BASE = r'C:\Users\Vlad\clubgg_bot'
SHOTS = os.path.join(BASE, 'shots')

# Точки тапа — в ДОЛЯХ экрана (сняты с эталона 1080x2400): у телефонов разная
# высота экрана (1080x2400, 1080x2340), пиксельная точка мимо кнопки.
REF_W, REF_H = 1080, 2400
BTN_CHECK = (535 / REF_W, 2315 / REF_H)   # «Чек» / «Колл» — центр
BTN_FOLD = (185 / REF_W, 2315 / REF_H)    # «Фолд» — слева
BTN_BET = (880 / REF_W, 2315 / REF_H)     # «Бет 33%» / пресет рейза — справа

LOG = os.path.join(BASE, 'auto_bot.log')

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')

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

def find_dealer_d(img):
    """Где кнопка D: 'me' (внизу, у моего аватара) / 'opp' (вверху) / None.
    Золотистый круглый маркер D у аватара игрока."""
    W, H = img.size
    px = img.load()
    zones = {'opp': (0, int(H*0.15), W, int(H*0.30)), 'me': (0, int(H*0.72), W, int(H*0.88))}
    best = None
    for name, (x0, y0, x1, y1) in zones.items():
        pts = 0
        for y in range(y0, y1, 2):
            for x in range(x0, x1, 2):
                r, g, b = px[x, y]
                if r > 180 and g > 130 and b < 120 and r > b + 40:
                    pts += 1
        if pts >= 60:
            best = name
            break
    return best

def have_my_cards(img):
    """У меня есть карманные карты? Крупный белый прямоугольник карты справа от аватара
    (x 250-600, y 1720-1960). Флаг/имя — мелкие белые точки, карта — большой блок."""
    W, H = img.size
    px = img.load()
    x0, x1 = int(W*0.23), int(W*0.58)
    y0, y1 = int(H*0.72), int(H*0.82)
    white = 0
    for y in range(y0, y1, 2):
        for x in range(x0, x1, 2):
            r, g, b = px[x, y]
            if r > 190 and g > 190 and b > 190:
                white += 1
    return white >= 400

def tap(x, y):
    subprocess.run([ADB, '-s', SERIAL, 'shell', 'input', 'tap', str(x), str(y)], check=False)

def point(img, frac):
    """Доли экрана -> пиксели ФАКТИЧЕСКОГО кадра (он же экран телефона)."""
    W, H = img.size
    return int(round(frac[0] * W)), int(round(frac[1] * H))

def ask_claude(img_path, timeout=25):
    """Позвать Клода на сервере. При превышении timeout — фолд (безопасно)."""
    try:
        p = subprocess.run([sys.executable, os.path.join(BASE, 'claude_act.py'), img_path],
                           capture_output=True, timeout=timeout, cwd=BASE)
        out = p.stdout.decode('utf-8', errors='replace').strip()
        try:
            return json.loads(out[out.find('{'): out.rfind('}')+1])
        except Exception:
            return {'action': 'fold', 'raise_to_bb': None, 'reason': f'parse: {out[:100]}'}
    except subprocess.TimeoutExpired:
        return {'action': 'fold', 'raise_to_bb': None, 'reason': 'claude timeout'}

def log_action(decision, position):
    with open(os.path.join(BASE, 'claude_log.jsonl'), 'a', encoding='utf-8') as f:
        f.write(json.dumps({**decision, 'pos': position, 'ts': time.strftime('%H:%M:%S')},
                           ensure_ascii=False) + '\n')

def main():
    os.makedirs(SHOTS, exist_ok=True)
    log('=== БОТ ЗАПУЩЕН ===')
    INTERVAL = 1.2
    ZONE_X = 520
    THRESH = 0.05

    # скользящий базовый кадр (нижняя зона x 520-1080)
    base_zone = None
    streak = 0
    position = None
    idle_since = time.time()

    while True:
        img = grab()
        if img is None:
            log('ERR: screencap')
            time.sleep(2)
            continue
        W, H = img.size
        zone = img.crop((ZONE_X, int(H*0.86), W, H))

        if base_zone is None:
            base_zone = zone
            continue

        d = diff_ratio(base_zone, zone)
        has_buttons = d > THRESH

        if not has_buttons:
            # кнопок нет: обновляем базу скользяще, копим streak=0
            base_zone = zone
            streak = 0
            # периодически (раз в 20 сек) обновляем позицию D
            if time.time() - idle_since > 20:
                position = find_dealer_d(img)
                log(f'позиция D: {position}')
                idle_since = time.time()
            time.sleep(INTERVAL)
            continue

        # кнопки есть: 2 кадра подряд = стабильно мой ход
        streak += 1
        if streak < 2:
            time.sleep(INTERVAL)
            continue
        streak = 0

        # КРИТИЧНО: проверяем, что у меня есть карты (я в раздаче). Иначе это чужие кнопки/анимации.
        if not have_my_cards(img):
            base_zone = zone  # обновляем базу — это был не мой ход
            time.sleep(INTERVAL)
            continue

        yc_call, cc = yellow_in_zone(img, 380, 700)
        has_call = yc_call >= 100
        if position is None:
            position = find_dealer_d(img)
        log(f'МОЙ ХОД (pos={position}) diff={d:.3f} yellow_call={yc_call}')

        if not has_call:
            # ставок нет -> мгновенный чек
            pt = point(img, BTN_CHECK)
            tap(*pt)
            log(f'AUTO_CHECK {pt}')
            log_action({'action': 'check', 'raise_to_bb': None,
                        'reason': 'auto_check (no bet)', 'source': 'local'}, position)
        else:
            # есть ставка -> Клод на сервере
            shot = os.path.join(SHOTS, 'claude_turn.png')
            img.save(shot)
            t0 = time.time()
            decision = ask_claude(shot)
            dt = round(time.time() - t0, 1)
            action = decision.get('action', 'check')
            log(f'КЛОД ({dt}s): {json.dumps(decision, ensure_ascii=False)}')
            if action == 'fold':
                pt = point(img, BTN_FOLD)
                tap(*pt)
                log(f'TAP FOLD {pt}')
            elif action == 'call':
                pt = point(img, BTN_CHECK)
                tap(*pt)
                log(f'TAP CALL {pt}')
            elif action == 'raise':
                pt = point(img, BTN_BET)
                tap(*pt)
                log(f'TAP RAISE {pt} raise_to={decision.get("raise_to_bb")}')
            else:
                pt = point(img, BTN_CHECK)
                tap(*pt)
                log(f'TAP CHECK {pt}')
            log_action(decision, position)

        # ждём, пока кнопки исчезнут (ход принят), обновляем базу
        time.sleep(2.5)
        for _ in range(10):
            img2 = grab()
            if img2 is None:
                break
            W2, H2 = img2.size
            z2 = img2.crop((ZONE_X, int(H2*0.86), W2, H2))
            if diff_ratio(base_zone, z2) <= THRESH:
                base_zone = z2
                break
            base_zone = z2
            time.sleep(1.2)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        log('=== БОТ ОСТАНОВЛЕН ===')
    except Exception as e:
        log(f'FATAL: {e}')
        raise
