#!/usr/bin/env python3
"""Состояние стола ClubGG по скриншоту: чей ход, кнопки, банк, позиции, игроки.

Всё локально, без ИИ. Основные функции:
    read_state(img)      -> полный снимок состояния (dict)
    is_my_turn(img)      -> появились ли настоящие кнопки действий
    find_dealer(img)     -> где кнопка D ('me' / 'opp' / номер места)
    count_players(img)   -> сколько игроков за столом
    read_number(img, ..) -> число (банк, сумма колла), если собраны эталоны цифр

Числа читаются только при наличии templates/digit_*.png (см. build_templates.py).
Без них бот работает по факту наличия жёлтой суммы на кнопке колла: есть жёлтое —
перед нами ставка, нет — можно чекать.
"""
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
    """
    H, W = img.shape[:2]
    y0, y1 = int(H * config.ACTION_BAR_Y[0]), int(H * config.ACTION_BAR_Y[1])
    x_min = int(config.ACTION_BAR_X0 * W / config.REF_W)
    strip = img[y0:y1, :]
    mask = gray_button_mask(strip)
    col = (mask > 0).sum(axis=0)
    min_col = max(4, int((y1 - y0) * 0.25))     # столбец внутри кнопки — заметно заполнен
    filled = col >= min_col

    buttons, run = [], None
    for x in range(W):
        if filled[x] and x >= x_min:
            run = x if run is None else run
        elif run is not None:
            if x - run > W * 0.09:              # кнопка шире ~100px на 1080
                buttons.append({'x0': run, 'x1': x - 1})
            run = None
    if run is not None and W - run > W * 0.09:
        buttons.append({'x0': run, 'x1': W - 1})

    for b in buttons:
        b['w'] = b['x1'] - b['x0']
        b['x'] = (b['x0'] + b['x1']) // 2
        b['y'] = (y0 + y1) // 2
    return buttons


def is_my_turn(img):
    """Мой ход = в правой части нижней полосы есть хотя бы одна кнопка действия."""
    return len(detect_action_buttons(img)) >= 1


def hero_has_cards(img):
    """Есть ли у героя карманные карты (он в раздаче).

    Важно для игрового цикла: кнопки внизу могут появиться от анимации или чужого
    хода, а тапать, не будучи в раздаче, нельзя.
    """
    return len(card_reader.my_card_boxes(img)) >= 1


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
# кнопка дилера и места
# --------------------------------------------------------------------------
def find_dealer(img, min_area=800):
    """Кнопка D: dict(x, y, where='me'/'opp', seat=индекс места или None) либо None."""
    H, W = img.shape[:2]
    mask = gold_mask(img)
    mask[:int(H * 0.08), :] = 0                 # статус-бар телефона
    mask[int(H * 0.88):, :] = 0                 # полоса кнопок
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or w == 0 or h == 0:
            continue
        if not 0.6 < w / h < 1.7:               # маркер круглый
            continue
        if area < 0.5 * w * h:                  # заполненный круг, а не буква
            continue
        if best is None or area > best[0]:
            best = (area, cents[i])
    if best is None:
        return None
    cx, cy = int(best[1][0]), int(best[1][1])
    where = 'me' if cy > H * (config.HERO_D_ZONE[1]) else 'opp'
    return {'x': cx, 'y': cy, 'where': where, 'seat': nearest_seat(cx, cy, W, H)}


def nearest_seat(x, y, W, H):
    """Индекс ближайшего места оппонента (config.SEATS) или None, если это я."""
    hx0, hy0, hx1, hy1 = config.zone_px(config.HERO_SEAT, W, H)
    hero_d = ((x - (hx0 + hx1) / 2) ** 2 + (y - (hy0 + hy1) / 2) ** 2) ** 0.5
    best, best_d = None, hero_d
    for i, seat in enumerate(config.SEATS):
        sx0, sy0, sx1, sy1 = config.zone_px(seat, W, H)
        d = ((x - (sx0 + sx1) / 2) ** 2 + (y - (sy0 + sy1) / 2) ** 2) ** 0.5
        if d < best_d:
            best, best_d = i, d
    return best


def felt_color(img):
    """Средний цвет сукна — по центральной полосе стола."""
    H, W = img.shape[:2]
    patch = img[int(H * 0.42):int(H * 0.46), int(W * 0.30):int(W * 0.70)]
    return np.median(patch.reshape(-1, 3), axis=0)


def seat_occupancy(img, exclude=None):
    """Доля «не сукна» в каждом месте: список float (0..1) по config.SEATS.

    Крайние места из config.SEATS заходят на зону доски, поэтому карты доски
    (exclude — их боксы, по умолчанию найденные на кадре) из подсчёта исключаются:
    иначе белая карта читается как «место занято» и игроков выходит больше.
    """
    H, W = img.shape[:2]
    felt = felt_color(img)
    ignore = np.zeros((H, W), bool)
    boxes = card_reader.find_board_cards(img) if exclude is None else exclude
    for bx0, by0, bx1, by1 in boxes:
        ignore[max(0, by0):by1, max(0, bx0):bx1] = True
    scores = []
    for seat in config.SEATS:
        x0, y0, x1, y1 = config.zone_px(seat, W, H)
        patch = img[y0:y1, x0:x1]
        if patch.size == 0:
            scores.append(0.0)
            continue
        keep = ~ignore[y0:y1, x0:x1]
        if not keep.any():
            scores.append(0.0)
            continue
        dist = np.linalg.norm(patch.astype(float) - felt, axis=2)
        scores.append(float((dist[keep] > 45).mean()))
    return scores


def count_players(img, threshold=0.35, exclude=None):
    """Сколько игроков за столом (включая меня) + список занятых мест."""
    scores = seat_occupancy(img, exclude=exclude)
    occupied = [i for i, s in enumerate(scores) if s >= threshold]
    return 1 + len(occupied), occupied, scores


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
    board_boxes = [d['box'] for d in cards['detail']['board']]
    n_players, occupied, seat_scores = count_players(img, exclude=board_boxes)
    dealer = find_dealer(img)
    where = dealer['where'] if dealer else None
    buttons = detect_action_buttons(img)
    my_turn = len(buttons) >= 1
    bet = has_bet(img)
    street = STREETS.get(len(board), 'unknown')
    dealer_seat = dealer['seat'] if dealer else None
    pos = hero_position(where, n_players, dealer_seat, occupied)

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
        'occupied_seats': occupied,
        'seat_scores': [round(s, 3) for s in seat_scores],
        'dealer': where,
        'dealer_seat': dealer_seat,
        'position': pos,
        'hero_is_dealer': where == 'me',
        'first_to_act': (first_to_act(street, n_players, where == 'me', dealer_seat, occupied)
                         if where else None),
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
        for i, s in enumerate(seat_occupancy(image)):
            print(f'место {i}: заполненность {s:.3f} {"занято" if s >= 0.35 else "пусто"}')
        sys.exit(0)
    state = read_state(image)
    for k in ('my_turn', 'in_hand', 'n_buttons', 'has_bet', 'hole', 'board', 'street', 'players',
              'dealer', 'position', 'first_to_act', 'pot_bb', 'to_call_bb', 'taps'):
        print(f'{k}: {state[k]}')
