#!/usr/bin/env python3
"""Мост к Claude Code на сервере v2: промпт передаётся через файл на сервере.
Использование: python claude_act.py [png]"""
import subprocess, sys, os, io, json, time, textwrap
from PIL import Image

SERVER = 'root@45.139.29.169'
ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'
SHOTS = r'C:\Users\Vlad\clubgg_bot\shots'

PROMPT = """Ты — покерный бот за столом ClubGG (No-Limit Hold'em, кэш, heads-up). Посмотри на /tmp/table.jpg — скриншот стола: мои карты внизу, оппонент сверху.

Верни СТРОГО один JSON, без markdown-обёртки и без пояснений:
{"action": "fold|check|call|raise", "raise_to_bb": число или null, "reason": "кратко"}

Правила решений:
- check: ставок нет, рука слабая/средняя (смотрим бесплатно)
- call: есть ставка и рука достаточно сильна или пот-оддсы оправданы
- fold: есть ставка и рука мусорная (нет пары/дро)
- raise: только сильная рука (топ-пара+, сет, две пары, стрит, флеш, сильное дро). raise_to_bb: префлоп 2.5-3 ББ, постфлоп 50-70% банка
- Тайтово-агрессивно. Учитывай стадию, позицию, банк. Читай карты со скриншота внимательно."""

def grab():
    p = subprocess.run([ADB, '-s', SERIAL, 'exec-out', 'screencap', '-p'],
                       capture_output=True, timeout=20)
    if len(p.stdout) < 1000:
        return None
    return Image.open(io.BytesIO(p.stdout)).convert('RGB')

def upload(img):
    w, h = img.size
    if w > 400:
        img = img.resize((400, int(h * 400 / w)), Image.LANCZOS)
    local = os.path.join(SHOTS, 'to_claude.jpg')
    img.save(local, 'JPEG', quality=65)
    subprocess.run(['scp', '-o', 'ConnectTimeout=10', '-q', local,
                    f'{SERVER}:/tmp/table.jpg'], check=True, timeout=30)

def ask_claude():
    # промпт в файл на сервере, чтобы не ломались кавычки
    subprocess.run(['ssh', '-o', 'ConnectTimeout=10', SERVER,
                    'cat > /tmp/poker_prompt.txt'],
                   input=PROMPT.encode('utf-8'), timeout=20, check=True)
    cmd = ('cd /tmp && claude -p "$(cat /tmp/poker_prompt.txt)" '
           '--allowedTools Read --max-turns 3 --model sonnet --effort low '
           '2>/dev/null')
    p = subprocess.run(['ssh', '-o', 'ConnectTimeout=10', SERVER, cmd],
                       capture_output=True, timeout=150)
    out = p.stdout.decode('utf-8', errors='replace')
    try:
        d = json.loads(out)
        result = d.get('result', out)
    except Exception:
        result = out
    # ищем JSON-объект
    start = result.find('{')
    end = result.rfind('}') + 1
    if start >= 0 and end > start:
        return json.loads(result[start:end])
    return {'action': 'check', 'raise_to_bb': None, 'reason': 'parse_fail: ' + result[:200]}

if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if path:
        img = Image.open(path).convert('RGB')
    else:
        img = grab()
        if img is None:
            print(json.dumps({'action': 'check', 'reason': 'no screenshot'})); sys.exit(2)
    t0 = time.time()
    upload(img)
    t1 = time.time()
    decision = ask_claude()
    t2 = time.time()
    decision['upload_s'] = round(t1 - t0, 1)
    decision['think_s'] = round(t2 - t1, 1)
    print(json.dumps(decision, ensure_ascii=False))
