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
PANEL = (55, 45, 40)
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


def draw_card(img, x, y, w, h, card):
    """Карта лицом вверх: белый прямоугольник + индекс (ранг над мастью) в углу."""
    _rounded_rect(img, x, y, x + w, y + h, WHITE, r=10)
    if card is None:
        return
    rank, suit = card[0], card[1]
    color = RED if suit in ('h', 'd') else BLACK
    text = '10' if rank == 'T' else rank
    scale = h / 145.0
    thick = max(2, int(3 * scale))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale * 1.3, thick)
    tx, ty = x + int(w * 0.10), y + int(h * 0.06) + th
    cv2.putText(img, text, (tx, ty), cv2.FONT_HERSHEY_DUPLEX, scale * 1.3, color, thick)
    draw_suit(img, tx + tw // 2, ty + int(h * 0.16), int(h * 0.085), suit, color)
    # центральный рисунок (крупный значок масти) — не должен мешать распознаванию угла
    draw_suit(img, x + w // 2, y + int(h * 0.68), int(h * 0.15), suit, color)


def draw_button(img, center, w, h, label_yellow=False):
    cx, cy = center
    _rounded_rect(img, cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2, BTN_GRAY, r=16)
    if label_yellow:
        cv2.putText(img, '2.5', (cx - 40, cy + 15), cv2.FONT_HERSHEY_SIMPLEX, 1.3, YELLOW, 3)


def render(hole=None, board=None, buttons=True, call_amount=False,
           dealer='me', players=2, pot_bb=3.0, size=(config.REF_W, config.REF_H)):
    """Собрать кадр. hole/board — списки строк карт ('Ah'), None = рубашка."""
    W, H = size
    img = np.zeros((H, W, 3), np.uint8)
    img[:, :] = FELT
    cv2.ellipse(img, (W // 2, int(H * 0.45)), (int(W * 0.46), int(H * 0.20)),
                0, 0, 360, (70, 120, 55), -1)

    # банк
    if pot_bb is not None:
        cv2.putText(img, f'Obshiy bank {pot_bb} BB', (int(W * 0.28), int(H * 0.355)),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.1, YELLOW, 3)

    # доска
    board = board or []
    for i, card in enumerate(board):
        x = BOARD_X0 + i * (BOARD_CARD[0] + BOARD_GAP)
        draw_card(img, x, BOARD_Y, BOARD_CARD[0], BOARD_CARD[1], card)

    # мои карты веером (перекрываются -> контур слипается)
    hole = hole or []
    for i, card in enumerate(hole[:2]):
        x = HOLE_X0 + i * (HOLE_CARD[0] - HOLE_OVERLAP)
        draw_card(img, x, HOLE_Y, HOLE_CARD[0], HOLE_CARD[1], card)

    # панели игроков (герой + оппоненты по местам из config.SEATS)
    seats = config.SEATS[:max(0, players - 1)]
    for (sx0, sy0, sx1, sy1) in seats:
        x0, y0, x1, y1 = config.zone_px((sx0, sy0, sx1, sy1), W, H)
        _rounded_rect(img, x0, y0, x1, y1, PANEL, r=14)
        cv2.circle(img, (x0 + (y1 - y0) // 2, (y0 + y1) // 2), (y1 - y0) // 3, (150, 140, 130), -1)
        cv2.putText(img, 'Player', (x0 + (y1 - y0), (y0 + y1) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (230, 230, 230), 2)
    hx0, hy0, hx1, hy1 = config.zone_px(config.HERO_SEAT, W, H)
    _rounded_rect(img, hx0, hy0, hx1, hy1, PANEL, r=14)
    cv2.circle(img, (hx0 + (hy1 - hy0) // 2, (hy0 + hy1) // 2), (hy1 - hy0) // 3, (150, 140, 130), -1)

    # кнопка D
    if dealer == 'me':
        dx, dy = int(W * 0.30), int(H * 0.775)
    elif dealer == 'opp':
        dx, dy = int(W * 0.30), int(H * 0.25)
    else:
        dx = dy = None
    if dx is not None:
        cv2.circle(img, (dx, dy), 26, GOLD, -1)
        cv2.putText(img, 'D', (dx - 10, dy + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (30, 30, 30), 2)

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
