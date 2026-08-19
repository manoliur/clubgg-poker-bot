#!/usr/bin/env python3
"""Синтетический рендер стола ClubGG для тестов (телефона на сервере нет).

Рисует кадр 1080x2400, похожий по геометрии на реальный: сукно, карты доски,
мои карты веером (перекрываются), панели игроков, кнопка D, банк, кнопки действий.
Это позволяет прогонять весь конвейер (детекция -> эталоны -> распознавание ->
состояние стола -> решение) без реального устройства.
"""
import os
import sys
import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: E402
import card_reader  # noqa: E402
import table_state as ts  # noqa: E402

FELT = (60, 105, 45)          # BGR тёмно-зелёное сукно
WHITE = (250, 250, 250)
RED = (60, 60, 200)
BLACK = (35, 35, 35)
GOLD = (60, 190, 235)         # BGR золотистый (r>180,g>130,b<120)
YELLOW = (60, 220, 245)
CYAN = (245, 200, 90)         # BGR голубой — подпись стека («259 ББ»)
PANEL = (55, 45, 40)
CARD_BACK = (150, 150, 150)   # серая рубашка карты оппонента
BTN_GRAY = (38, 38, 38)       # живая кнопка действия (в клиенте серый ~37)
BTN_DIM = (64, 64, 64)        # погашенная кнопка — светлее живой (в клиенте ~62)
YELLOW_DIM = (110, 150, 155)  # погашенная сумма: тусклая, вне жёлтой маски

BOARD_CARD = (123, 175)       # w, h
HOLE_CARD = (112, 165)
BOARD_Y = 1120                # карты доски начинаются ПОД плашкой банка (config.POT_ZONE)
BOARD_X0 = 250
BOARD_GAP = 10
HOLE_Y = 1730
HOLE_X0 = 40
HOLE_OVERLAP = 14


def _rounded_rect(img, x0, y0, x1, y1, color, r=12):
    cv2.rectangle(img, (x0 + r, y0), (x1 - r, y1), color, -1)
    cv2.rectangle(img, (x0, y0 + r), (x1, y1 - r), color, -1)
    for cx, cy in ((x0 + r, y0 + r), (x1 - r, y0 + r), (x0 + r, y1 - r), (x1 - r, y1 - r)):
        cv2.circle(img, (cx, cy), r, color, -1)


def draw_suit(img, cx, cy, size, suit, color):
    """Значок масти. Формы намеренно различимы по силуэту."""
    s = size
    if suit == 'd':
        pts = np.array([[cx, cy - s], [cx + int(s * 0.7), cy], [cx, cy + s],
                        [cx - int(s * 0.7), cy]], np.int32)
        cv2.fillPoly(img, [pts], color)
    elif suit == 'h':
        r = int(s * 0.55)
        cv2.circle(img, (cx - r // 2, cy - r // 2), r, color, -1)
        cv2.circle(img, (cx + r // 2, cy - r // 2), r, color, -1)
        pts = np.array([[cx - r, cy - r // 4], [cx + r, cy - r // 4], [cx, cy + s]], np.int32)
        cv2.fillPoly(img, [pts], color)
    elif suit == 's':
        pts = np.array([[cx, cy - s], [cx + s, cy + s // 3], [cx - s, cy + s // 3]], np.int32)
        cv2.fillPoly(img, [pts], color)
        r = int(s * 0.5)
        cv2.circle(img, (cx - r // 2, cy + s // 4), r, color, -1)
        cv2.circle(img, (cx + r // 2, cy + s // 4), r, color, -1)
        cv2.rectangle(img, (cx - 2, cy + s // 3), (cx + 2, cy + s), color, -1)
    elif suit == 'c':
        r = int(s * 0.5)
        cv2.circle(img, (cx, cy - r), r, color, -1)
        cv2.circle(img, (cx - r, cy + r // 2), r, color, -1)
        cv2.circle(img, (cx + r, cy + r // 2), r, color, -1)
        cv2.rectangle(img, (cx - 2, cy + r // 2), (cx + 2, cy + s), color, -1)


def draw_card(img, x, y, w, h, card, index_rect=None, center_picture=True):
    """Карта лицом вверх: белый прямоугольник + индекс (ранг над мастью) в углу.

    index_rect (x0,y0,x1,y1, экранные пиксели) — куда положить индекс: для моих
    карт это фиксированные окна config.HERO_INDEX_RECTS (подогнаны под реальную
    раскладку ClubGG), поэтому глифы рисуются ВНУТРИ окна с отступом 6-8px.
    Без index_rect (карты доски) индекс рисуется в левом верхнем углу карты.
    center_picture=False — не рисовать крупный значок масти в центре: у моих
    карт он попал бы в фиксированное окно индекса и был бы принят за ранг.
    """
    _rounded_rect(img, x, y, x + w, y + h, WHITE, r=10)
    if card is None:
        return
    rank, suit = card[0], card[1]
    color = RED if suit in ('h', 'd') else BLACK
    text = '10' if rank == 'T' else rank
    scale = h / 145.0
    thick = max(2, int(3 * scale))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale * 1.3, thick)
    if index_rect is not None:
        tx, ty = index_rect[0] + 6, index_rect[1] + 8 + th
    else:
        tx, ty = x + int(w * 0.10), y + int(h * 0.06) + th
    cv2.putText(img, text, (tx, ty), cv2.FONT_HERSHEY_DUPLEX, scale * 1.3, color, thick)
    draw_suit(img, tx + tw // 2, ty + int(h * 0.16), int(h * 0.085), suit, color)
    # центральный рисунок (крупный значок масти) — не должен мешать распознаванию угла
    if center_picture:
        draw_suit(img, x + w // 2, y + int(h * 0.68), int(h * 0.15), suit, color)


def rank_glyph(rank, slant=0.0, size=160):
    """Нормализованный глиф ранга — такой, каким его отдаёт extract_glyphs.

    slant — наклон глифа: низ уезжает влево относительно верха. Мои карты лежат
    веером под углом, и у наклонённой СЕМЁРКИ диагональ уходит влево так же, как
    у двойки: корреляция начинает выбирать «2» (живые кадры 18.08 — 8 семёрок из
    13 прочитались двойками). Разводит их только нижняя черта двойки, см.
    card_reader.resolve_2_vs_7 — этот рендер и даёт тестам такой спорный глиф.
    """
    text = '10' if rank == 'T' else rank
    scale = size / 145.0 * 1.3
    thick = max(2, int(3 * size / 145.0))
    (_, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, thick)
    pad = int(size * 0.6)
    img = np.zeros((size + 2 * pad, size + 2 * pad), np.uint8)
    cv2.putText(img, text, (pad, pad + th), cv2.FONT_HERSHEY_DUPLEX, scale, 255, thick)
    if slant:
        cy = pad + th / 2.0
        m = np.float32([[1, -slant, slant * cy], [0, 1, 0]])
        img = cv2.warpAffine(img, m, img.shape[::-1], flags=cv2.INTER_NEAREST)
    bbox = card_reader._ink_bbox(img)
    return card_reader._norm_glyph(img, bbox, card_reader.CANON_RANK)


def draw_amount(img, center, text, height, color=YELLOW):
    """Сумма («2.5 BB») шрифтом клиента: глифы берутся из templates/digit_*.png.

    cv2.putText для сумм не годится: его цифры не совпадают со шрифтом ClubGG,
    и синтетический кадр читался бы иначе, чем живой («2.5» вместо 2.5 давало
    2.0). Точку рисуем кружком у базовой линии: в эталоне она нормализована на
    всю клетку, и вставленная как есть выглядела бы кругом с цифру ростом.
    """
    digits = ts.load_digit_templates()
    cx, cy = center
    gap = max(2, height // 8)
    items = []
    for ch in text:
        if ch == ' ':
            items.append((None, gap * 2))
            continue
        key = {'.': 'dot', ',': 'dot', 'B': 'bb', 'Б': 'bb'}.get(ch, ch)
        tpl = digits.get(key)
        if tpl is None:
            return False
        if key == 'dot':
            items.append(('dot', max(3, height // 5)))
            continue
        w = max(1, int(round(height * tpl.shape[1] / tpl.shape[0])))
        items.append((cv2.resize(tpl, (w, height), interpolation=cv2.INTER_NEAREST), w))
    total = sum(w for _, w in items) + gap * (len(items) - 1)
    x = cx - total // 2
    top, bottom = cy - height // 2, cy + height // 2
    for glyph, w in items:
        if glyph is None:
            pass
        elif isinstance(glyph, str):             # точка
            cv2.circle(img, (x + w // 2, bottom - w // 2), w // 2, color, -1)
        else:
            box = img[top:top + height, x:x + w]
            if box.shape[:2] == glyph.shape[:2]:
                box[glyph > 127] = color
        x += w + gap
    return True


def draw_button(img, center, w, h, amount=None, disabled=False):
    """Кнопка полосы действий. amount — жёлтая сумма на ней («Колл 2.5», «Бет 1.3»).

    disabled=True — как гасит кнопку клиент: заливка СВЕТЛЕЕТ, а сумма перестаёт
    быть ярко-жёлтой. По этим двум признакам table_state.raise_presets и отличает
    живую кнопку от мёртвой, тап по которой ничего не делает.
    """
    cx, cy = center
    fill = BTN_DIM if disabled else BTN_GRAY
    _rounded_rect(img, cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2, fill, r=16)
    if amount is not None:
        color = YELLOW_DIM if disabled else YELLOW
        if not draw_amount(img, (cx, cy + 10), f'{amount} BB', max(12, h // 3), color):
            cv2.putText(img, amount, (cx - 40, cy + 15), cv2.FONT_HERSHEY_SIMPLEX,
                        1.3, color, 3)


def draw_seat(img, zone, stack='259', cards=True, name='Player'):
    """Плашка занятого места: аватар, имя, ГОЛУБАЯ подпись стека, рубашки карт.

    По голубой подписи table_state и находит занятые места, а по рубашкам над
    плашкой отличает игрока в раздаче от сидящего вне игры — поэтому в синтетике
    рисуется именно то и другое, а не просто прямоугольник.
    """
    W, H = img.shape[1], img.shape[0]
    x0, y0, x1, y1 = config.zone_px(zone, W, H)
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    if cards:
        px0, py0, px1, py1 = (config.scale((-100, -230), W, H) + config.scale((100, -70), W, H))
        for dx in (-1, 1):
            _rounded_rect(img, cx + px0 + dx * 20, cy + py0, cx + px1 + dx * 20, cy + py1,
                          CARD_BACK, r=10)
    _rounded_rect(img, x0, y0, x1, y1, PANEL, r=14)
    cv2.circle(img, (x0 + (y1 - y0) // 2, cy), (y1 - y0) // 3, (150, 140, 130), -1)
    cv2.putText(img, name, (x0 + (y1 - y0), cy - 6), cv2.FONT_HERSHEY_SIMPLEX,
                0.7, (230, 230, 230), 2)
    cv2.putText(img, f'{stack} BB', (x0 + (y1 - y0), cy + 11), cv2.FONT_HERSHEY_SIMPLEX,
                0.85, CYAN, 2)


def draw_dealer(img, zone):
    """Кнопка D рядом с плашкой места (снизу-справа, как в клиенте)."""
    W, H = img.shape[1], img.shape[0]
    x0, y0, x1, y1 = config.zone_px(zone, W, H)
    dx, dy = config.scale((48, 70), W, H)
    cx = min(W - 30, (x0 + x1) // 2 + dx)
    cy = min(int(H * 0.87), (y0 + y1) // 2 + dy)
    cv2.circle(img, (cx, cy), 26, GOLD, -1)
    cv2.putText(img, 'D', (cx - 10, cy + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)


def draw_chevron(img):
    """Кнопка-шеврон «^» слева от столбца ставки (config.CHEVRON).

    Ею столбец сворачивается и раскрывается; бот ищет её по жёлтой галке.
    """
    W, H = img.shape[1], img.shape[0]
    cx, cy = config.scale(config.CHEVRON, W, H)
    w, h = config.scale(config.CHEVRON_BOX, W, H)
    _rounded_rect(img, cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2, BTN_GRAY, r=10)
    arm, thick = w // 4, max(3, h // 8)
    cv2.line(img, (cx - arm, cy + arm // 2), (cx, cy - arm // 2), YELLOW, thick)
    cv2.line(img, (cx, cy - arm // 2), (cx + arm, cy + arm // 2), YELLOW, thick)


def render(hole=None, board=None, buttons=True, call_amount=False,
           dealer='me', players=2, sitting_out=0, pot_bb=3.0, presets=0,
           dim_presets=(), chevron=False, showdown=False,
           size=(config.REF_W, config.REF_H)):
    """Собрать кадр. hole/board — списки строк карт ('Ah'), None = рубашка.

    call_amount — сумма на кнопке «Колл»: True даёт 2.5ББ, число — сколько
    нужно (алл-ин оппонента в тестах — это 23.7ББ при банке 51.7).

    players — сколько игроков В РАЗДАЧЕ (включая героя), sitting_out — сколько
    ещё занятых мест без карт (сидят вне раздачи). dealer: 'me', 'opp' (первое
    место по кругу за героем), номер места оппонента или None.

    presets — сколько пресетов ставки нарисовать НАД кнопкой «Бет» (клиент
    показывает их, пока столбец раскрыт шевроном), dim_presets — номера
    погашенных строк столбца снизу вверх (0 — сама кнопка «Бет»).
    chevron=True — нарисовать кнопку «^»; вместе с presets<3 это и есть
    свёрнутый столбец, который бот должен сначала раскрыть.
    showdown=True — вскрытие: вместо кнопок действий ряд плашек «Показать» с
    лицами карт.
    """
    W, H = size
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = FELT
    cv2.ellipse(img, (W // 2, int(H * 0.45)), (int(W * 0.46), int(H * 0.20)),
                0, 0, 360, (70, 120, 55), -1)

    # банк — жёлтым в плашке над доской (config.POT_ZONE); подпись «Общий банк»
    # клиент пишет серым выше, в жёлтую маску она не попадает
    if pot_bb is not None:
        cv2.putText(img, 'Obshiy bank', (int(W * 0.40), int(H * 0.428)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (150, 150, 150), 1)
        if not draw_amount(img, (int(W * 0.50), int(H * 0.447)), f'{pot_bb} BB',
                           int(H * 0.014)):
            cv2.putText(img, f'{pot_bb} BB', (int(W * 0.44), int(H * 0.455)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, YELLOW, 2)

    # мои карты веером (перекрываются -> контур слипается)
    hole = hole or []
    for i, card in enumerate(hole[:2]):
        x = HOLE_X0 + i * (HOLE_CARD[0] - HOLE_OVERLAP)
        rect = (config.rect_px(config.HERO_INDEX_RECTS[i], W, H)
                if i < len(config.HERO_INDEX_RECTS) else None)
        draw_card(img, x, HOLE_Y, HOLE_CARD[0], HOLE_CARD[1], card,
                  index_rect=rect, center_picture=False)

    # плашки мест: сначала играющие оппоненты по кругу, за ними — вне раздачи
    n_opp = max(0, players - 1)
    for i, seat in enumerate(config.SEATS[:n_opp + sitting_out]):
        playing = i < n_opp
        draw_seat(img, seat, stack='259' if playing else '0', cards=playing,
                  name=f'Opp{i}')
    draw_seat(img, config.HERO_SEAT, stack='59.8', cards=False, name='Hero')

    # доска — поверх панелей: на реальном столе карты лежат на сукне и панели
    # игроков их не закрывают (крайние места у config.SEATS заходят на зону доски)
    board = board or []
    for i, card in enumerate(board):
        x = BOARD_X0 + i * (BOARD_CARD[0] + BOARD_GAP)
        draw_card(img, x, BOARD_Y, BOARD_CARD[0], BOARD_CARD[1], card)

    # кнопка D — у своего места
    if dealer == 'me':
        draw_dealer(img, config.HERO_SEAT)
    elif dealer is not None:
        seat = 0 if dealer == 'opp' else int(dealer)
        if 0 <= seat < n_opp:
            draw_dealer(img, config.SEATS[seat])

    # автодействие «Чек/Фолд» с галочкой — слева, НЕ кнопка действия
    cv2.rectangle(img, (45, int(H * 0.90)), (520, int(H * 0.95)), (48, 48, 48), 2)

    # кнопки действий
    if showdown:
        # вскрытие: плашки «Показать» стоят ровно на местах кнопок действий,
        # отличает их только белое лицо карты внутри
        for center in (config.BTN_FOLD, config.BTN_CALL, config.BTN_RAISE):
            cx, cy = config.scale(center, W, H)
            draw_button(img, (cx, cy), int(W * 0.30), int(H * 0.048))
            draw_card(img, cx + 20, cy - int(H * 0.018), 125, 85, None)
        return img

    if buttons:
        for name, center in (('fold', config.BTN_FOLD), ('call', config.BTN_CALL),
                             ('raise', config.BTN_RAISE)):
            c = config.scale(center, W, H)
            amount = None
            if name == 'call' and call_amount:
                # call_amount=True — прежние 2.5ББ, числом — любая сумма колла
                amount = '2.5' if call_amount is True else str(call_amount)
            if name == 'raise':
                amount = '1.3'        # клиент всегда пишет размер на кнопке «Бет»
            draw_button(img, c, int(W * 0.30), int(H * 0.048), amount=amount,
                        disabled=(name == 'raise' and 0 in dim_presets))
        # пресеты покрупнее над кнопкой «Бет» (строки config.PRESET_ROWS снизу вверх)
        for i in range(1, min(presets + 1, len(config.PRESET_ROWS))):
            x0, y0, x1, y1 = config.rect_px(
                config.PRESET_X[:1] + config.PRESET_ROWS[i][:1]
                + config.PRESET_X[1:] + config.PRESET_ROWS[i][1:], W, H)
            draw_button(img, ((x0 + x1) // 2, (y0 + y1) // 2), x1 - x0, y1 - y0,
                        amount=str(i + 1), disabled=(i in dim_presets))
        if chevron:
            draw_chevron(img)
    return img


def save(path, **kw):
    img = render(**kw)
    cv2.imwrite(path, img)
    return img


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else 'synth_table.png'
    save(out, hole=['Ah', 'Kd'], board=['Tc', '7s', '2h'], call_amount=True)
    print('saved', out)
