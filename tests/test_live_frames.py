#!/usr/bin/env python3
"""Проверка на ЖИВЫХ кадрах телефона (shots_audit/), если они есть на диске.

Кадры не лежат в git (как и shots/ — они тяжёлые), поэтому тест пропускается
там, где папки нет. Он закрывает то, чего синтетика не покажет: реальные оттенки
погашенной кнопки и настоящие плашки «Показать» на вскрытии.

Кадры сняты в живой сессии 18.08.2026, каждый — состояние ровно в момент
решения бота (main._save_frame пишет их ПЕРЕД тапом):
    154349 — флоп, банк 4ББ: пресет «33% Бет 1.3ББ» живой, ставка прошла;
    154841 — флоп, банк 2ББ: «33% Бет 0.6ББ» погашен (меньше минимума 1ББ),
             тап не прошёл, ход сгорел по таймауту через 34 секунды;
    154925 — то же на ривере;
    154915 — тёрн, чек: кнопка «Чек» живая, ход прошёл при том же столбце;
    154732, 154820 — ВСКРЫТИЕ: внизу «Показать», а бот считал это своим ходом.
"""
import os
import sys
import unittest

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import table_state as ts                # noqa: E402

SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'shots_audit')

# кадр -> (вскрытие, живой ли НИЖНИЙ пресет, сколько живых пресетов всего)
FRAMES = {
    '20260818_154349_raise.png': (False, True, 4),
    '20260818_154841_raise.png': (False, False, 3),
    '20260818_154925_raise.png': (False, False, 3),
    '20260818_154915_check.png': (False, False, 3),
    '20260818_154732_raise.png': (True, None, 0),
    '20260818_154820_raise.png': (True, None, 0),
}


@unittest.skipUnless(os.path.isdir(SHOTS), 'нет папки shots_audit/ с живыми кадрами')
class LiveFramesTest(unittest.TestCase):
    def frame(self, name):
        path = os.path.join(SHOTS, name)
        if not os.path.exists(path):
            self.skipTest(f'нет кадра {name}')
        img = cv2.imread(path)
        self.assertIsNotNone(img, path)
        return img

    def test_frames(self):
        for name, (showdown, bottom_live, n_live) in FRAMES.items():
            with self.subTest(name):
                img = self.frame(name)
                self.assertEqual(ts.is_showdown(img), showdown)
                self.assertEqual(ts.is_my_turn(img), not showdown,
                                 'вскрытие ходом не считается')
                presets = ts.raise_presets(img)
                live = [p for p in presets if p['enabled']]
                self.assertEqual(len(live), n_live)
                if bottom_live is not None:
                    self.assertEqual(presets[0]['enabled'], bottom_live)


if __name__ == '__main__':
    unittest.main(verbosity=2)
