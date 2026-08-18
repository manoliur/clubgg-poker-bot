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

FELT = (60, 105, 45)          # BGR тёмно-зелёное сукно
WHITE = (250, 250, 250)
RED = (60, 60, 200)
BLACK = (35, 35, 35)
GOLD = (60, 190, 235)         # BGR золотистый (r>180,g>130,b<120)
YELLOW = (60, 220, 245)
CYAN = (245, 200, 90)         # BGR голубой — подпись стека («259 ББ»)
PANEL = (55, 45, 40)
CARD_BACK = (150, 150, 150)   # серая рубашка карты оппонента
BTN_GRAY = (55, 55, 55)

BOARD_CARD = (123, 175)       # w, h
HOLE_CARD = (112, 165)
BOARD_Y = 1080
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


def draw_button(img, center, w, h, label_yellow=False):
    cx, cy = center
    _rounded_rect(img, cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2, BTN_GRAY, r=16)
    if label_yellow:
        cv2.putText(img, '2.5', (cx - 40, cy + 15), cv2.FONT_HERSHEY_SIMPLEX, 1.3, YELLOW, 3)


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


def render(hole=None, board=None, buttons=True, call_amount=False,
           dealer='me', players=2, sitting_out=0, pot_bb=3.0,
           size=(config.REF_W, config.REF_H)):
    """Собрать кадр. hole/board — списки строк карт ('Ah'), None = рубашка.

    players — сколько игроков В РАЗДАЧЕ (включая героя), sitting_out — сколько
    ещё занятых мест без карт (сидят вне раздачи). dealer: 'me', 'opp' (первое
    место по кругу за героем), номер места оппонента или None.
    """
    W, H = size
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = FELT
    cv2.ellipse(img, (W // 2, int(H * 0.45)), (int(W * 0.46), int(H * 0.20)),
                0, 0, 360, (70, 120, 55), -1)

    # банк
    if pot_bb is not None:
        cv2.putText(img, f'Obshiy bank {pot_bb} BB', (int(W * 0.28), int(H * 0.355)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, YELLOW, 3)

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
    if buttons:
        for name, center in (('fold', config.BTN_FOLD), ('call', config.BTN_CALL),
                             ('raise', config.BTN_RAISE)):
            c = config.scale(center, W, H)
            draw_button(img, c, int(W * 0.30), int(H * 0.048),
                        label_yellow=(name == 'call' and call_amount))
    return img


def save(path, **kw):
    img = render(**kw)
    cv2.imwrite(path, img)
    return img


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else 'synth_table.png'
    save(out, hole=['Ah', 'Kd'], board=['Tc', '7s', '2h'], call_amount=True)
    print('saved', out)
