#!/usr/bin/env python3
"""Общая конфигурация бота ClubGG: пути, adb, координаты стола.

Пути берутся из переменных окружения, иначе — из папки скрипта. Так один и тот же
код работает и на компе (C:\\Users\\Vlad\\clubgg_bot), и на сервере/в тестах.

Координаты заданы для эталонного экрана 1080x2400 (Redmi Note 13) и масштабируются
под фактический размер скриншота функцией scale().
"""
import os

BASE = os.environ.get('CLUBGG_BASE') or os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE, 'templates')
SHOTS_DIR = os.path.join(BASE, 'shots')
HAND_HISTORY = os.path.join(BASE, 'hand_history.jsonl')
LOG_FILE = os.path.join(BASE, 'bot.log')

ADB = os.environ.get('CLUBGG_ADB', r'E:/down/platform-tools/platform-tools/adb.exe')
SERIAL = os.environ.get('CLUBGG_SERIAL', '1cf5db29')

# --- эталонный экран ---
REF_W, REF_H = 1080, 2400

# --- кнопки действий (центры, эталонные координаты) ---
BTN_FOLD = (185, 2315)    # «Фолд»
BTN_CALL = (535, 2315)    # «Чек» / «Колл»
BTN_RAISE = (880, 2315)   # «Бет» / пресет рейза

# Полоса кнопок действий (доли высоты экрана)
ACTION_BAR_Y = (0.86, 0.99)
# Зона, где появляются НАСТОЯЩИЕ кнопки (левее — автодействие «Чек/Фолд» с галочкой)
ACTION_BAR_X0 = 520
# Зона жёлтой суммы на кнопке «Колл»
CALL_AMOUNT_X = (380, 700)

# --- зоны стола (доли) ---
BOARD_ZONE = (0.02, 0.40, 0.98, 0.60)     # x0,y0,x1,y1 — общие карты
HERO_CARDS_ZONE = (0.0, 0.69, 0.60, 0.85)  # мои карманные карты
HERO_D_ZONE = (0.0, 0.70, 1.0, 0.88)       # где искать кнопку D у меня
OPP_D_ZONE = (0.0, 0.10, 1.0, 0.62)        # где искать кнопку D у оппонентов
POT_ZONE = (0.10, 0.30, 0.90, 0.40)        # «Общий банк X ББ»

# --- места за столом ---
# Панели игроков (аватар + имя + стек) в долях экрана. Порядок — по часовой
# стрелке от места слева от героя. Значения приблизительные: подстраиваются
# файлом seats.json (список [x0,y0,x1,y1]) или командой `python table_state.py <png> --seats`.
HERO_SEAT = (0.02, 0.785, 0.32, 0.850)
SEATS = [
    (0.02, 0.620, 0.28, 0.685),   # слева снизу
    (0.02, 0.460, 0.28, 0.525),   # слева
    (0.06, 0.280, 0.32, 0.345),   # слева сверху
    (0.20, 0.170, 0.46, 0.235),   # сверху слева
    (0.54, 0.170, 0.80, 0.235),   # сверху справа
    (0.68, 0.280, 0.94, 0.345),   # справа сверху
    (0.72, 0.460, 0.98, 0.525),   # справа
    (0.72, 0.620, 0.98, 0.685),   # справа снизу
]

# --- стол ---
BLINDS = (15, 30)          # SB / BB в фишках
HERO_NAME = 'Robert Nikson'
MAX_PLAYERS = 9


def scale(pt, img_w, img_h):
    """Эталонная координата -> координата для скриншота размера img_w x img_h."""
    x, y = pt
    return int(round(x * img_w / REF_W)), int(round(y * img_h / REF_H))


def zone_px(zone, img_w, img_h):
    """Доли (x0,y0,x1,y1) -> пиксели (x0,y0,x1,y1)."""
    x0, y0, x1, y1 = zone
    return (int(x0 * img_w), int(y0 * img_h), int(x1 * img_w), int(y1 * img_h))
