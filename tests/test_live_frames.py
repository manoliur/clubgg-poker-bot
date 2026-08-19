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
import config                           # noqa: E402
import card_reader                      # noqa: E402
import table_state as ts                # noqa: E402
from build_templates import digit_labels  # noqa: E402

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOTS = os.path.join(BASE, 'shots_audit')

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

    def test_numbers_are_read(self):
        """Банк читается на каждом живом кадре — ради этого и собирались эталоны."""
        for name, pot in POTS.items():
            with self.subTest(name):
                s = ts.read_state(self.frame(name))
                self.assertEqual(s['pot_bb'], pot)


# кадр -> банк (прочитано глазами с оригиналов 1080px)
POTS = {
    '20260818_154349_raise.png': 4.0,
    '20260818_154732_raise.png': 1.5,
    '20260818_154820_raise.png': 4.0,
    '20260818_154841_raise.png': 2.0,
    '20260818_154915_check.png': 2.0,
    '20260818_154925_raise.png': 2.0,
}


# кадр из shots_digits/ -> ранги моих карт, прочитанные ГЛАЗАМИ с оригинала.
# Здесь собраны все кадры сессии 18.08, где у героя есть 2 или 7: до фикса
# resolve_2_vs_7 восемь семёрок (все на ПРАВОЙ карте, она лежит в веере с
# наклоном) читались двойками. Масти не проверяем — у карт этой сессии
# встречается отдельная путаница d/h и c/s, к рангам она отношения не имеет.
HOLE_RANKS = {
    '20260818_125126_fold.jpg': 'J7',
    '20260818_125251_fold.jpg': '42',
    '20260818_125321_fold.jpg': '75',
    '20260818_125505_fold.jpg': 'A7',
    '20260818_125616_fold.jpg': 'J7',
    '20260818_133152_fold_badcards.jpg': 'T7',
    '20260818_133623_fold.jpg': '87',
    '20260818_133825_fold.jpg': '62',
    '20260818_134539_fold.jpg': '75',
    '20260818_134610_fold.jpg': '52',
    '20260818_135003_fold.jpg': 'Q7',
    '20260818_135440_fold.jpg': '82',
    '20260818_135546_fold.jpg': '62',
    '20260818_135637_fold.jpg': '76',
    '20260818_135658_call.jpg': 'A7',
    '20260818_135743_fold.jpg': '72',
    '20260818_135801_fold.jpg': 'Q7',
}

DIGIT_SHOTS = os.path.join(BASE, 'shots_digits')


@unittest.skipUnless(os.path.isdir(DIGIT_SHOTS), 'нет папки shots_digits/ с живыми кадрами')
class LiveRanksTest(unittest.TestCase):
    """Ранги моих карт на живых кадрах: регрессия на путаницу 2 и 7."""

    def test_two_and_seven_not_confused(self):
        bad = []
        for name, want in HOLE_RANKS.items():
            path = os.path.join(DIGIT_SHOTS, name)
            if not os.path.exists(path):
                continue
            img = cv2.imread(path)
            self.assertIsNotNone(img, path)
            got = ''.join((c or '?')[0] for c in card_reader.read_table(img)['hole'])
            if got != want:
                bad.append(f'{name}: {want} -> {got}')
        self.assertEqual(bad, [], f'неверно прочитаны ранги на {len(bad)} кадрах')


class DigitLabelsTest(unittest.TestCase):
    """Каждое размеченное число (банк, пресеты, колл) читается обратно эталонами.

    Разметка живёт в build_templates.DIGIT_FRAMES; здесь она работает как
    регрессия распознавания: 392 числа с 86 живых кадров.
    """

    def setUp(self):
        self.labels = digit_labels()
        if not self.labels:
            self.skipTest('нет живых кадров (shots_digits/, shots_audit/)')
        self.digits = ts.load_digit_templates()

    def test_all_labelled_numbers_read_back(self):
        bad, total = [], 0
        cache = {}
        for item in self.labels:
            img = cache.get(item['file'])
            if img is None:
                img = cache[item['file']] = cv2.imread(os.path.join(BASE, item['file']))
            self.assertIsNotNone(img, item['file'])
            # погашенная строка на СЖАТОМ кадре: точка «0.6» тонет в jpeg-шуме,
            # и число читается как 6. На кадрах 1080px (как у бота) она видна.
            if item['dim'] and img.shape[1] < config.REF_W:
                continue
            total += 1
            want = float(item['text'].replace('ББ', ''))
            got = ts.read_number(img, item['rect'], item['ink'], self.digits)
            if got != want:
                bad.append(f'{item["file"]} {want} -> {got}')
        self.assertGreater(total, 300, 'разметка не должна усыхать')
        self.assertEqual(bad, [], f'не прочитано {len(bad)} из {total}')


if __name__ == '__main__':
    unittest.main(verbosity=2)
