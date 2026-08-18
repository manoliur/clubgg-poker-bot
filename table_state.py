#!/usr/bin/env python3
"""Состояние стола ClubGG по скриншоту: чей ход, кнопки, банк, позиции, игроки.

Всё локально, без ИИ. Основные функции:
    read_state(img)      -> полный снимок состояния (dict)
    is_my_turn(img)      -> появились ли настоящие кнопки действий
    find_dealer(img)     -> где кнопка D ('me' / 'opp' / номер места по кругу)
    count_players(img)   -> сколько игроков в раздаче (2..6)
    read_number(img, ..) -> число (банк, сумма колла), если собраны эталоны цифр

Игроки (2..6) ищутся по плашкам мест: у занятого места клиент подписывает стек
голубым («259 ББ»). Плашки упорядочиваются по часовой стрелке вокруг центра
стола начиная с героя — по этому кругу считаются позиции и очерёдность хода.
Фиксированные прямоугольники мест для этого не годятся: клиент сажает игроков
по-разному, а пустые места не рисует вовсе.

Числа читаются только при наличии templates/digit_*.png (см. build_templates.py).
Без них бот работает по факту наличия жёлтой суммы на кнопке колла: есть жёлтое —
перед нами ставка, нет — можно чекать.
"""
import math
import os
import sys
import cv2
import numpy as np

import config
import card_reader

STREETS = {0: 'preflop', 3: 'flop', 4: 'turn', 5: 'river'}
POSITIONS_6MAX = ['BTN', 'SB', 'BB', 'UTG', 'MP', 'CO']

CANON_DIGIT = (20, 28)


# --------------------------------------------------------------------------
# цветовые маски
# --------------------------------------------------------------------------
def yellow_mask(bgr):
    """Жёлтый текст (суммы, банк): r>140, g>110, b<90."""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return ((r > 140) & (g > 110) & (b < 90)).astype(np.uint8) * 255


def gold_mask(bgr):
    """Золотистый маркер дилера: r>180, g>130, b<120, r>b+40."""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return ((r > 180) & (g > 130) & (b < 120) & (r > b + 40)).astype(np.uint8) * 255


def stack_mask(bgr):
    """Голубая подпись стека («259 ББ») — ею помечено каждое ЗАНЯТОЕ место."""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return ((b > 150) & (g > 120) & (r < 140) & (b > r + 50)).astype(np.uint8) * 255


def gray_button_mask(bgr):
    """Нейтрально-серая заливка кнопок действий (не цветная, не чёрная)."""
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    return ((abs(r - g) < 14) & (abs(g - b) < 14) & (r > 25) & (r < 95)).astype(np.uint8) * 255


def bright_mask(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    return (gray > 170).astype(np.uint8) * 255


INK_MASKS = {'yellow': yellow_mask, 'bright': bright_mask, 'gold': gold_mask}


def _crop(img, rect):
    """rect в долях (x0,y0,x1,y1) -> (кроп, смещение)."""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = config.zone_px(rect, W, H)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(W, x1), min(H, y1)
    return img[y0:y1, x0:x1], (x0, y0)


# --------------------------------------------------------------------------
# кнопки действий и мой ход
# --------------------------------------------------------------------------
def detect_action_buttons(img):
    """Настоящие кнопки действий в нижней полосе (правее автодействия).

    Возвращает список dict: x, y, x0, x1, w (координаты экрана, центр — x,y).
    Ложные «кнопки» отсекаются: фон панели/меню паузы — это широкий серый
    прямоугольник (на 1080 ~560px), который покрывает сразу оба центра (Колл и
    Бет); настоящая кнопка уже MAX_BTN_W и покрывает ровно один центр.
    """
    H, W = img.shape[:2]
    y0, y1 = int(H * config.ACTION_BAR_Y[0]), int(H * config.ACTION_BAR_Y[1])
    x_min = int(config.ACTION_BAR_X0 * W / config.REF_W)
    call_x = config.BTN_CALL[0] * W / config.REF_W
    raise_x = config.BTN_RAISE[0] * W / config.REF_W
    strip = img[y0:y1, :]
    mask = gray_button_mask(strip)
    col = (mask > 0).sum(axis=0)
    min_col = max(4, int((y1 - y0) * 0.25))     # столбец внутри кнопки — заметно заполнен
    filled = col >= min_col

    runs, run = [], None
    for x in range(W):
        if filled[x] and x >= x_min:
            run = x if run is None else run
        elif run is not None:
            runs.append((run, x - 1))
            run = None
    if run is not None:
        runs.append((run, W - 1))

    buttons = []
    for x0, x1 in runs:
        w = x1 - x0
        if w <= W * 0.09:                       # слишком узко
            continue
        if w > MAX_BTN_W * W:                   # слишком широко — фон панели, не кнопка
            continue
        has_call = x0 <= call_x <= x1
        has_raise = x0 <= raise_x <= x1
        if has_call == has_raise:               # ни одного центра ИЛИ оба сразу — мусор
            continue
        buttons.append({'x0': x0, 'x1': x1, 'w': w,
                        'x': (x0 + x1) // 2, 'y': (y0 + y1) // 2})
    return buttons


def is_my_turn(img):
    """Мой ход = в правой части нижней полосы есть хотя бы одна кнопка действия."""
    return len(detect_action_buttons(img)) >= 1


def hero_has_cards(img):
    """Есть ли у героя карманные карты (он в раздаче).

    Важно для игрового цикла: кнопки внизу могут появиться от анимации или чужого
    хода, а тапать, не будучи в раздаче, нельзя.
    """
    return len(card_reader.my_index_rects(img)) >= 1


def has_bet(img):
    """Жёлтая сумма на кнопке колла = оппонент поставил, чек недоступен."""
    H, W = img.shape[:2]
    x0 = int(config.CALL_AMOUNT_X[0] * W / config.REF_W)
    x1 = int(config.CALL_AMOUNT_X[1] * W / config.REF_W)
    y0, y1 = int(H * config.ACTION_BAR_Y[0]), int(H * config.ACTION_BAR_Y[1])
    zone = img[y0:y1, x0:x1]
    if zone.size == 0:
        return False
    return int((yellow_mask(zone) > 0).sum()) >= 60


def action_points(img):
    """Куда тапать: {'fold': (x,y), 'call': (x,y), 'raise': (x,y)}.

    Найденную кнопку привязываем к действию по эталонному центру, который в неё
    попал (по порядку нельзя: поиск идёт только правее ACTION_BAR_X0, поэтому
    «Фолд» слева не детектится и btns[0] — это «Колл»). Что не нашлось —
    остаётся эталонной координатой из config.
    """
    H, W = img.shape[:2]
    ref = {'fold': config.scale(config.BTN_FOLD, W, H),
           'call': config.scale(config.BTN_CALL, W, H),
           'raise': config.scale(config.BTN_RAISE, W, H)}
    pts = dict(ref)
    x_min = int(config.ACTION_BAR_X0 * W / config.REF_W)
    for b in detect_action_buttons(img):
        for name, (rx, _) in ref.items():
            if b['x0'] <= rx <= b['x1']:
                # левый край кнопки обрезан зоной поиска — её центр смещён, берём эталонный x
                pts[name] = (rx if b['x0'] <= x_min else b['x'], b['y'])
                break
    return pts


# --------------------------------------------------------------------------
# плашки игроков: кто сидит за столом и кто в раздаче
# --------------------------------------------------------------------------
# Глиф подписи стека в долях кадра: цифра/буква «259 ББ». Нижняя граница
# отсекает мусор, верхняя — фишки ставок (круглая голубая фишка ~38x37 на 1080).
GLYPH_W = (0.008, 0.030)
GLYPH_H = (0.005, 0.015)
GLYPH_MIN_AREA = 60
PANEL_MIN_GLYPHS = 3        # минимум «0 ББ»
CARD_BACK_MIN = 0.12        # доля серого (рубашки карт) в окне над плашкой
MAX_BTN_W = 0.34            # кнопка действия не шире 34% экрана (фон панели шире)


def _stack_glyphs(img):
    """Буквы/цифры голубых подписей стека: список (x, y, w, h)."""
    H, W = img.shape[:2]
    mask = stack_mask(img)
    n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < GLYPH_MIN_AREA:
            continue
        if not GLYPH_W[0] * W <= w <= GLYPH_W[1] * W:
            continue
        if not GLYPH_H[0] * H <= h <= GLYPH_H[1] * H:
            continue
        out.append((int(x), int(y), int(w), int(h)))
    return out


def find_player_panels(img):
    """Плашки занятых мест: [{'x','y','box'}] — по центру подписи стека.

    Глифы склеиваются раздутием в группы: плашка = подпись стека, иногда вместе
    с всплывающим ярлыком действия («Бет») над ней. Ярлык рисуется выше, поэтому
    за саму плашку берётся НИЖНЯЯ строка глифов группы.
    """
    H, W = img.shape[:2]
    glyphs = _stack_glyphs(img)
    if not glyphs:
        return []
    ink = np.zeros((H, W), np.uint8)
    for x, y, w, h in glyphs:
        ink[y:y + h, x:x + w] = 255
    kx, ky = int(W * 0.09) | 1, int(H * 0.05) | 1
    n, labels, _, _ = cv2.connectedComponentsWithStats(
        cv2.dilate(ink, np.ones((ky, kx), np.uint8)), 8)

    groups = {}
    for g in glyphs:
        x, y, w, h = g
        groups.setdefault(int(labels[y + h // 2, x + w // 2]), []).append(g)

    panels = []
    for group in groups.values():
        if len(group) < PANEL_MIN_GLYPHS:
            continue
        line_h = max(g[3] for g in group)
        bottom = max(g[1] + g[3] / 2 for g in group)
        row = [g for g in group if g[1] + g[3] / 2 > bottom - 0.6 * line_h]
        if len(row) < 2:
            continue
        x0 = min(g[0] for g in row)
        y0 = min(g[1] for g in row)
        x1 = max(g[0] + g[2] for g in row)
        y1 = max(g[1] + g[3] for g in row)
        panels.append({'x': (x0 + x1) // 2, 'y': (y0 + y1) // 2, 'box': (x0, y0, x1, y1)})
    return panels


def panel_has_cards(img, panel):
    """Есть ли у места карты: над плашкой лежат серые рубашки.

    Отличает игрока в раздаче от сидящего вне игры (у того на месте аватар/видео,
    а стек 0 ББ) и от уже сбросившего карты.
    """
    H, W = img.shape[:2]
    dx0, dy0, dx1, dy1 = config.CARD_BACK_PROBE
    x0 = max(0, panel['x'] + int(dx0 * W / config.REF_W))
    x1 = min(W, panel['x'] + int(dx1 * W / config.REF_W))
    y0 = max(0, panel['y'] + int(dy0 * H / config.REF_H))
    y1 = min(H, panel['y'] + int(dy1 * H / config.REF_H))
    patch = img[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    hi = patch.max(axis=2).astype(int)
    lo = patch.min(axis=2).astype(int)
    grey = (hi - lo < 30) & (hi > 90) & (hi < 220)
    return float(grey.mean()) >= CARD_BACK_MIN


def player_panels_ordered(img):
    """Плашки по часовой стрелке начиная с героя.

    К каждой добавлены: is_hero, in_hand (герой — всегда, оппонент — если видны
    его карты), angle (угол вокруг центра стола). Порядок по кругу нужен для
    позиций: за героем идёт место слева от него, дальше по кругу.
    """
    H, W = img.shape[:2]
    panels = find_player_panels(img)
    if not panels:
        return []
    hx0, hy0, hx1, hy1 = config.zone_px(config.HERO_PANEL_ZONE, W, H)
    hero = None
    for p in panels:
        if hx0 <= p['x'] <= hx1 and hy0 <= p['y'] <= hy1:
            if hero is None or p['y'] > hero['y']:
                hero = p
    cx, cy = W * config.TABLE_CENTER[0], H * config.TABLE_CENTER[1]
    for p in panels:
        p['angle'] = math.degrees(math.atan2(p['y'] - cy, p['x'] - cx)) % 360
        p['is_hero'] = p is hero
        p['in_hand'] = True if p is hero else panel_has_cards(img, p)
    base = (hero or panels[0])['angle']
    panels.sort(key=lambda p: (p['angle'] - base) % 360)
    return panels


def count_players(img, panels=None):
    """Игроки в раздаче: (сколько, места оппонентов по кругу, все плашки).

    Места оппонентов нумеруются по часовой стрелке от героя: 0 — следующий за
    ним. Сидящие вне раздачи (нет карт) не считаются: позиции и блайнды их
    пропускают.
    """
    panels = player_panels_ordered(img) if panels is None else panels
    in_hand = [p for p in panels if p['in_hand']]
    return max(1, len(in_hand)), list(range(max(0, len(in_hand) - 1))), panels


def find_dealer(img, panels=None, min_area=500):
    """Кнопка D: dict(x, y, where='me'/'opp', seat=место оппонента) либо None.

    Маркер — золотой кружок примерно 46x40 на 1080. От жёлтых цифр (банк, пресеты
    рейза) отличается шириной и близкой к единице пропорцией.
    """
    H, W = img.shape[:2]
    mask = gold_mask(img)
    mask[:int(H * 0.08), :] = 0                 # статус-бар телефона
    mask[int(H * 0.88):, :] = 0                 # полоса кнопок
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or w == 0 or h == 0:
            continue
        if w < 0.028 * W:                       # цифры суммы заметно уже маркера
            continue
        if not 0.75 < w / h < 1.6:              # маркер круглый
            continue
        if area < 0.35 * w * h:                 # кружок, а не буква
            continue
        if best is None or area > best[0]:
            best = (area, cents[i])
    if best is None:
        return None
    cx, cy = int(best[1][0]), int(best[1][1])
    seat, where = dealer_seat(cx, cy, img, panels)
    return {'x': cx, 'y': cy, 'where': where, 'seat': seat}


def dealer_seat(x, y, img, panels=None):
    """Чья кнопка D: (место оппонента по кругу или None, 'me'/'opp').

    Маркер лежит вплотную к плашке своего места, поэтому место — ближайшее.
    Плашек не нашлось — падаем на грубое правило «низ экрана = моя кнопка».
    """
    panels = player_panels_ordered(img) if panels is None else panels
    nearest = min(panels, key=lambda p: (p['x'] - x) ** 2 + (p['y'] - y) ** 2)
    if nearest['is_hero']:
        return None, 'me'
    opponents = [p for p in panels if not p['is_hero']]
    return opponents.index(nearest), 'opp'


# --------------------------------------------------------------------------
# чтение чисел (банк, сумма колла) — при наличии эталонов цифр
# --------------------------------------------------------------------------
def segment_text_glyphs(img, rect, ink='yellow'):
    """Глифы текста в прямоугольнике: [(x, нормализованный глиф)] слева направо."""
    crop, _ = _crop(img, rect)
    if crop.size == 0:
        return []
    mask = INK_MASKS[ink](crop)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    comps = [stats[i] for i in range(1, n) if stats[i][4] >= 12]
    if not comps:
        return []
    max_h = max(c[3] for c in comps)
    out = []
    for x, y, w, h, area in sorted(comps, key=lambda c: c[0]):
        if h < 0.25 * max_h and w > 0.9 * max_h:
            continue                             # длинная черта/подчёркивание
        out.append((int(x), card_reader._norm_glyph(mask, (x, y, w, h), CANON_DIGIT)))
    return out


def load_digit_templates(tpl_dir=None):
    tpl_dir = tpl_dir or config.TEMPLATES_DIR
    digits = {}
    if not os.path.isdir(tpl_dir):
        return digits
    for f in os.listdir(tpl_dir):
        if f.startswith('digit_') and f.endswith('.png'):
            img = cv2.imread(os.path.join(tpl_dir, f), cv2.IMREAD_GRAYSCALE)
            if img is not None:
                digits[f[6:-4]] = img
    return digits


def read_number(img, rect, ink='yellow', digits=None, tpl_dir=None):
    """Прочитать число в зоне. Без эталонов цифр возвращает None."""
    digits = load_digit_templates(tpl_dir) if digits is None else digits
    if not digits:
        return None
    glyphs = segment_text_glyphs(img, rect, ink)
    if not glyphs:
        return None
    text = ''
    for _, g in glyphs:
        name, score = card_reader.match_best(g, digits)
        if name is None or score < 0.45:
            continue
        text += '.' if name == 'dot' else name
    try:
        return float(text)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# позиции и очерёдность
# --------------------------------------------------------------------------
def position_names(n_players):
    """Названия позиций по кругу, начиная с баттона."""
    if n_players <= 2:
        return ['SB', 'BB']                      # хедз-ап: у баттона малый блайнд
    tails = {0: [], 1: ['CO'], 2: ['UTG', 'CO'], 3: ['UTG', 'MP', 'CO'],
             4: ['UTG', 'UTG+1', 'MP', 'CO'], 5: ['UTG', 'UTG+1', 'MP', 'HJ', 'CO'],
             6: ['UTG', 'UTG+1', 'MP', 'MP+1', 'HJ', 'CO']}
    return ['BTN', 'SB', 'BB'] + tails.get(n_players - 3, ['?'] * (n_players - 3))


def _seat_order(occupied):
    """Порядок хода по кругу: сначала герой (0), затем занятые места по config.SEATS."""
    return [None] + sorted(occupied)


def hero_position(dealer_where, n_players, dealer_seat=None, occupied=None):
    """Позиция героя: 'SB'/'BB' (хедз-ап) или 'BTN'/'SB'/'BB'/'UTG'/.../'CO'."""
    if dealer_where is None:
        return None
    names = position_names(n_players)
    if n_players <= 2:
        return 'SB' if dealer_where == 'me' else 'BB'
    if dealer_where == 'me':
        return 'BTN'
    if dealer_seat is None or occupied is None or dealer_seat not in occupied:
        return None
    order = _seat_order(occupied)
    if len(order) != n_players:
        return None
    idx_d = order.index(dealer_seat)
    return names[(0 - idx_d) % n_players]


def first_to_act(street, n_players, hero_is_dealer, dealer_seat=None, occupied=None):
    """Кто говорит первым на улице: 'me' / 'opp' / None.

    Хедз-ап: префлоп первым SB (у него кнопка D), постфлоп — первым BB.
    За полным столом: префлоп первым UTG (третий после баттона), постфлоп — SB.
    """
    if n_players <= 2:
        if street == 'preflop':
            return 'me' if hero_is_dealer else 'opp'
        return 'opp' if hero_is_dealer else 'me'
    order = _seat_order(occupied or [])
    if len(order) != n_players:
        return None
    idx_d = 0 if hero_is_dealer else (order.index(dealer_seat)
                                      if dealer_seat in order else None)
    if idx_d is None:
        return None
    step = 3 if street == 'preflop' else 1
    return 'me' if order[(idx_d + step) % n_players] is None else 'opp'


# --------------------------------------------------------------------------
# полный снимок состояния
# --------------------------------------------------------------------------
def read_state(img, tpl_dir=None):
    """Полное состояние стола по скриншоту (dict)."""
    cards = card_reader.read_table(img, tpl_dir=tpl_dir)
    board = cards['board']
    hole = cards['hole']
    panels = player_panels_ordered(img)
    n_players, occupied, _ = count_players(img, panels=panels)
    seated = len(panels)                     # сидят за столом (включая вне раздачи)
    opponents_all = [p for p in panels if not p['is_hero']]
    occupied_all = list(range(len(opponents_all)))   # места всех оппонентов по кругу
    dealer = find_dealer(img, panels=panels)
    where = dealer['where'] if dealer else None
    buttons = detect_action_buttons(img)
    my_turn = len(buttons) >= 1
    bet = has_bet(img)
    street = STREETS.get(len(board), 'unknown')
    d_seat = dealer['seat'] if dealer else None
    # без плашки героя круг мест не привязан к нему — позицию считать нельзя
    hero_seated = any(p['is_hero'] for p in panels)
    # позиция и очерёдность считаются по числу СИДЯЩИХ за столом (seated), а не
    # по числу в раздаче: если один сфолдил, стол всё равно 4-max, а не HU.
    # Иначе бот на столе с 3-4 игроками переключался на хедз-ап тактику
    # (живой тест: «в раздаче=2, сидят=3» -> играл HU_SB против 3-max стола).
    pos = hero_position(where, seated, d_seat, occupied_all) if hero_seated else None

    digits = load_digit_templates(tpl_dir)
    pot_bb = read_number(img, config.POT_ZONE, 'yellow', digits) if digits else None
    to_call_bb = None
    if digits and bet:
        rect = (config.CALL_AMOUNT_X[0] / config.REF_W, config.ACTION_BAR_Y[0],
                config.CALL_AMOUNT_X[1] / config.REF_W, config.ACTION_BAR_Y[1])
        to_call_bb = read_number(img, rect, 'yellow', digits)

    return {
        'my_turn': my_turn,
        'in_hand': len(cards['detail']['hole']) >= 1,
        'buttons': buttons,
        'n_buttons': len(buttons),
        'has_bet': bet,
        'hole': hole,
        'board': board,
        'street': street,
        'players': n_players,
        'players_seated': seated,
        'occupied_seats': occupied_all,
        'seats': [{'x': p['x'], 'y': p['y'], 'hero': p['is_hero'], 'in_hand': p['in_hand']}
                  for p in panels],
        'dealer': where,
        'dealer_seat': d_seat,
        'position': pos,
        'hero_is_dealer': where == 'me',
        'first_to_act': (first_to_act(street, seated, where == 'me', d_seat, occupied_all)
                         if where and hero_seated else None),
        'pot_bb': pot_bb,
        'to_call_bb': to_call_bb,
        'taps': action_points(img),
        'cards_detail': cards['detail'],
    }


if __name__ == '__main__':
    path = sys.argv[1] if len(sys.argv) > 1 else None
    if not path:
        print('использование: python table_state.py <screenshot.png> [--seats]')
        sys.exit(2)
    image = cv2.imread(path)
    if image is None:
        print('ERR: не читается', path)
        sys.exit(2)
    if '--seats' in sys.argv:
        for i, p in enumerate(player_panels_ordered(image)):
            who = 'герой' if p['is_hero'] else f'оппонент {i - 1}'
            print(f'место {i}: {who} ({p["x"]},{p["y"]}) угол {p["angle"]:.0f}° '
                  f'{"в раздаче" if p["in_hand"] else "вне раздачи"}')
        sys.exit(0)
    state = read_state(image)
    for k in ('my_turn', 'in_hand', 'n_buttons', 'has_bet', 'hole', 'board', 'street', 'players',
              'players_seated', 'dealer', 'dealer_seat', 'position', 'first_to_act',
              'pot_bb', 'to_call_bb', 'taps'):
        print(f'{k}: {state[k]}')
