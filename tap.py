#!/usr/bin/env python3
"""Тап по координатам на телефоне. Использование: python tap.py X Y"""
import subprocess, sys

ADB = r'E:/down/platform-tools/platform-tools/adb.exe'
SERIAL = '1cf5db29'

def tap(x, y):
    subprocess.run([ADB, '-s', SERIAL, 'shell', 'input', 'tap', str(x), str(y)], check=False)

if __name__ == '__main__':
    x, y = int(sys.argv[1]), int(sys.argv[2])
    tap(x, y)
    print(f'tapped {x},{y}')
