#!/usr/bin/env python3
"""Скриншот телефона (ClubGG bot). Использование: python shot.py [имя_файла]"""
import subprocess, sys, os, time

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'
DIR = r'C:\Users\Vlad\clubgg_bot\shots'
os.makedirs(DIR, exist_ok=True)

def shot(name=None):
    name = name or time.strftime('table_%H%M%S.png')
    path = os.path.join(DIR, name)
    p = subprocess.run([ADB, '-s', SERIAL, 'exec-out', 'screencap', '-p'], capture_output=True)
    if len(p.stdout) > 1000:
        with open(path, 'wb') as f:
            f.write(p.stdout)
        return path, len(p.stdout)
    return None, len(p.stdout)

if __name__ == '__main__':
    path, size = shot(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f'{path} ({size} bytes)' if path else f'FAILED ({size})')
