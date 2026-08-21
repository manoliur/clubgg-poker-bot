#!/usr/bin/env python3
"""Тесты чтения ников: зоны на живых кадрах, нормализация, привязка профилей.

Tesseract'а на сервере нет, и ставить его ради тестов незачем: запуск вынесен в
nick_reader.run_tesseract, и тесты подменяют именно её. Так проверяется всё,
кроме самого распознавания, — вырезка зоны, чистка результата, поведение при
сбое OCR, слияние профилей и флаг read_nicks.
"""
import glob
import os
import sys
import tempfile
import unittest
from unittest import mock

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                # noqa: E402
import nick_reader as nr                     # noqa: E402
import opponents                             # noqa: E402
from main import Bot                         # noqa: E402
from tests.test_opponents import state       # noqa: E402

SHOTS = sorted(glob.glob(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'shots_stack', '*.jpg')))

EXE = r'C:\Program Files\Tesseract-OCR\tesseract.exe'   # в тестах не запускается


def text_ink(patch):
    """Доля светло-серых пикселей — так выглядит ник на тёмной плашке."""
    hi = patch.max(axis=2).astype(int)
    lo = patch.min(axis=2).astype(int)
    return float((((hi - lo) < 45) & (hi > 140)).mean())


def ocr(text):
    """Подмена запуска tesseract: что бы ни попросили, вернуть этот текст."""
    return mock.patch.object(nr, 'run_tesseract', lambda *a, **kw: text)


class ZoneTest(unittest.TestCase):
    """Зоны ников: не выходят за кадр, не лезут в соседние элементы плашки."""

    def test_zones_are_sane_fractions(self):
        for seat, zone in enumerate(config.NICK_ZONES):
            with self.subTest(seat=seat):
                x0, y0, x1, y1 = zone
                self.assertTrue(0.0 <= x0 < x1 <= 1.0, zone)
                self.assertTrue(0.0 <= y0 < y1 <= 1.0, zone)
                self.assertLess(y1 - y0, 0.05, 'строка ника узкая')

    def test_hero_nick_zone_sits_above_stack_zone(self):
        """Ник над суммой и не перекрывает её — иначе OCR читает «246.6 ББ»."""
        nick, stack = config.NICK_ZONES[0], config.HERO_STACK_ZONE
        self.assertLessEqual(nick[3], stack[1])
        self.assertGreater(nick[3], stack[1] - 0.01, 'зона не должна отрываться от плашки')

    def test_crop_stays_inside_any_frame(self):
        """Вырезка не выходит за кадр ни для одного места, ни на одном размере."""
        for w, h in ((400, 888), (1080, 2400), (720, 1600)):
            img = cv2.imread(SHOTS[0])
            img = cv2.resize(img, (w, h))
            for seat in range(len(config.NICK_ZONES)):
                with self.subTest(size=(w, h), seat=seat):
                    patch = nr.crop_nick(img, seat)
                    self.assertIsNotNone(patch)
                    self.assertGreater(patch.size, 0)

    def test_unknown_seat_has_no_zone(self):
        for seat in (-1, 6, 99, None, 'x'):
            self.assertIsNone(config.nick_zone(seat))
            self.assertIsNone(nr.crop_nick(cv2.imread(SHOTS[0]), seat))

    def test_hero_zone_lands_on_text_on_every_live_frame(self):
        """На всех 32 живых кадрах в зоне героя есть светлый текст ника."""
        for path in SHOTS:
            with self.subTest(shot=os.path.basename(path)):
                self.assertGreater(text_ink(nr.crop_nick(cv2.imread(path), 0)), 0.03)

    def test_opponent_zone_lands_on_text_when_seat_is_taken(self):
        """Место слева снизу занято почти всегда — там тоже должен быть текст."""
        taken = [p for p in SHOTS if text_ink(nr.crop_nick(cv2.imread(p), 1)) > 0.05]
        self.assertGreaterEqual(len(taken), 25, 'зона места 1 промахивается мимо ника')

    def test_seat_at_recognises_own_plate(self):
        """Плашка в центре своей зоны опознаётся как это же место."""
        for seat, zone in enumerate(config.SEAT_ZONES):
            with self.subTest(seat=seat):
                x = (zone[0] + zone[2]) / 2 * 400
                y = (zone[1] + zone[3]) / 2 * 888
                self.assertEqual(config.seat_at(x, y, 400, 888), seat)

    def test_seat_at_ignores_far_points(self):
        self.assertIsNone(config.seat_at(200, 30, 400, 888))     # верх экрана
        self.assertIsNone(config.seat_at(200, 460, 400, 888))    # центр стола


class CleanTest(unittest.TestCase):
    """Нормализация: что бы ни выдал OCR, в профиль попадает ник или None."""

    def test_trims_and_collapses_spaces(self):
        self.assertEqual(nr.clean_nick('  Poker   Pro \n'), 'Poker Pro')

    def test_keeps_digits_underscore_and_dot(self):
        # «EPT_38» — живой ник с кадра 20260818_133555
        self.assertEqual(nr.clean_nick('EPT_38'), 'EPT_38')
        self.assertEqual(nr.clean_nick('mr.big-1'), 'mr.big-1')

    def test_keeps_cyrillic(self):
        self.assertEqual(nr.clean_nick('Вася Петров'), 'Вася Петров')

    def test_drops_single_junk_chars(self):
        self.assertEqual(nr.clean_nick('|PokerPro88'), 'PokerPro88')
        self.assertEqual(nr.clean_nick('Poker•Pro'), 'Poker Pro')
        self.assertEqual(nr.clean_nick('_Ace_ '), 'Ace')

    def test_all_junk_is_not_a_nick(self):
        for garbage in ('|||', '~ ^ ` |', '...', '   ', '', None, '\x0c\n'):
            with self.subTest(garbage=garbage):
                self.assertIsNone(nr.clean_nick(garbage))

    def test_mostly_junk_is_rejected(self):
        """Зона попала на рамку/сукно: букв мало, мусора много — это не ник."""
        self.assertIsNone(nr.clean_nick('|_|~a|_|~'))

    def test_too_short_is_rejected(self):
        self.assertIsNone(nr.clean_nick('a'))

    def test_length_is_capped(self):
        long = 'A' * 40
        self.assertEqual(len(nr.clean_nick(long)), config.NICK_MAX_LEN)
        self.assertEqual(len(nr.clean_nick(long, max_len=8)), 8)


class ReadNickTest(unittest.TestCase):
    """Чтение ника с живого кадра при подменённом запуске tesseract."""

    def setUp(self):
        self.img = cv2.imread(SHOTS[0])

    def test_no_tesseract_means_no_ocr(self):
        """tesseract=None — режим без OCR: ничего не запускаем и молчим."""
        with mock.patch.object(nr, 'run_tesseract') as run:
            self.assertIsNone(nr.read_nick(self.img, 0, tesseract=None))
            run.assert_not_called()

    def test_reads_and_cleans(self):
        with ocr(' Robert  Nikson \n'):
            self.assertEqual(nr.read_nick(self.img, 0, tesseract=EXE), 'Robert Nikson')

    def test_ocr_failure_returns_none(self):
        """Не запустился/упал/таймаут — None, а не исключение."""
        with ocr(None):
            self.assertIsNone(nr.read_nick(self.img, 0, tesseract=EXE))

    def test_broken_exe_path_does_not_raise(self):
        """Настоящий запуск несуществующего exe: None, цикл бота живёт дальше."""
        self.assertIsNone(nr.run_tesseract(SHOTS[0], '/nope/tesseract-does-not-exist'))
        self.assertIsNone(nr.read_nick(self.img, 0, tesseract='/nope/tesseract'))

    def test_command_line_asks_for_one_line_of_eng_rus(self):
        cmd = nr.tesseract_cmd(EXE, '/tmp/x.png')
        self.assertEqual(cmd[:3], [EXE, '/tmp/x.png', 'stdout'])
        self.assertIn('--psm', cmd)
        self.assertEqual(cmd[cmd.index('--psm') + 1], '7')
        self.assertEqual(cmd[cmd.index('-l') + 1], 'eng+rus')

    def test_temp_file_is_removed(self):
        before = set(glob.glob(os.path.join(nr.tempfile.gettempdir(), 'clubgg_nick_*')))
        with ocr('Nick'):
            nr.read_nick(self.img, 0, tesseract=EXE)
        after = set(glob.glob(os.path.join(nr.tempfile.gettempdir(), 'clubgg_nick_*')))
        self.assertEqual(before, after)

    def test_prepare_inverts_and_upscales(self):
        patch = nr.crop_nick(self.img, 0)
        out = nr.prepare(patch, scale=2)
        self.assertEqual(out.shape, (patch.shape[0] * 2, patch.shape[1] * 2))
        self.assertGreater(out.mean(), 128, 'тёмный текст на светлом фоне')


class ReadNicksTest(unittest.TestCase):
    """Ники всех занятых мест: ключ — номер по кругу, зона — по месту на экране."""

    def setUp(self):
        self.img = cv2.imread(SHOTS[0])
        self.h, self.w = self.img.shape[:2]

    def seats(self, *zones):
        """Плашки: герой + оппоненты в центрах перечисленных мест."""
        out = [{'x': 0, 'y': 0, 'hero': True, 'in_hand': True}]
        for z in zones:
            zone = config.SEAT_ZONES[z]
            out.append({'x': (zone[0] + zone[2]) / 2 * self.w,
                        'y': (zone[1] + zone[3]) / 2 * self.h,
                        'hero': False, 'in_hand': True})
        return out

    def test_keys_are_circle_order_not_screen_place(self):
        """Оппонент, сидящий справа снизу (место 5), — это «Оппонент 1» по кругу."""
        seen = []
        with mock.patch.object(nr, 'read_nick',
                               lambda img, seat, **kw: seen.append(seat) or 'Nick'):
            nicks = nr.read_nicks(self.img, self.seats(5, 2), tesseract=EXE)
        self.assertEqual(sorted(nicks), [1, 2])       # ключи — номера по кругу
        self.assertEqual(sorted(seen), [2, 5])        # зоны — места на экране

    def test_hero_is_skipped(self):
        with mock.patch.object(nr, 'read_nick', return_value='Nick') as read:
            nr.read_nicks(self.img, self.seats(1), tesseract=EXE)
        self.assertEqual(read.call_count, 1)

    def test_unreadable_seat_is_absent(self):
        with mock.patch.object(nr, 'read_nick', return_value=None):
            self.assertEqual(nr.read_nicks(self.img, self.seats(1), tesseract=EXE), {})

    def test_no_tesseract_no_calls(self):
        with mock.patch.object(nr, 'read_nick') as read:
            self.assertEqual(nr.read_nicks(self.img, self.seats(1), tesseract=None), {})
        read.assert_not_called()

    def test_empty_table_is_not_scanned(self):
        with mock.patch.object(nr, 'read_nick') as read:
            self.assertEqual(nr.read_nicks(self.img, [], tesseract=EXE), {})
        read.assert_not_called()


class NormTest(unittest.TestCase):
    """Нормализация и нестрогое сравнение ников — живые промахи OCR."""

    def test_case_spaces_and_junk_are_dropped(self):
        for raw, key in (('INeedAHero', 'ineedahero'), ('Г еедАНего', 'гееданего'),
                         ('EPT_38', 'ept38'), ('mr.big-1', 'mrbig1'),
                         ('Poker Pro', 'pokerpro'), ('•Ace|', 'ace'), (None, '')):
            with self.subTest(raw=raw):
                self.assertEqual(opponents.norm_nick(raw), key)

    def test_one_misread_letter_is_the_same_player(self):
        """«INeedAHero» и «TNeedAHero»: I прочиталось как T — 0.9, один игрок."""
        self.assertAlmostEqual(opponents.similar('INeedAHero', 'TNeedAHero'), 0.9)
        self.assertGreaterEqual(opponents.similar('МеедАНего', 'Г еедАНего'),
                                opponents.NICK_RATIO)

    def test_different_nicks_are_not_glued(self):
        self.assertLess(opponents.similar('INeedAHero', 'HerGlinoMes'),
                        opponents.NICK_RATIO)
        self.assertLess(opponents.similar('PokerPro88', 'RiverRat77'),
                        opponents.NICK_RATIO)

    def test_short_nicks_need_an_exact_key(self):
        """У «Ace1» и «Ace2» похожесть 0.75 — а это разные люди."""
        self.assertEqual(opponents.similar('Ace1', 'Ace2'), 0.0)
        self.assertEqual(opponents.similar('Ace 1', 'ace1'), 1.0)

    def test_normalised_key_is_an_exact_match(self):
        self.assertEqual(opponents.similar('Poker Pro', 'pokerpro'), 1.0)

    def test_seat_name_is_not_a_nick(self):
        self.assertTrue(opponents.is_seat_name('Оппонент 3'))
        self.assertFalse(opponents.is_seat_name('Оппонентик'))
        self.assertFalse(opponents.is_seat_name('PokerPro88'))

    def test_ratio_in_the_log_has_no_zero_tail(self):
        self.assertEqual(opponents.ratio_str(0.9), '0.9')
        self.assertEqual(opponents.ratio_str(0.833), '0.83')
        self.assertEqual(opponents.ratio_str(1.0), '1')


class SamePlayerTest(unittest.TestCase):
    """Profiles: новое написание знакомого ника не заводит второго профиля."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix='clubgg_same_')
        self.p = opponents.Profiles(path=os.path.join(tmp, 'players.json'), db={})

    def hero(self, hands=51, name='INeedAHero'):
        for _ in range(hands):
            self.p.update(name, {'vpip': True}, notes=opponents.NICK_NOTE)
        return self.p.db[name]

    def test_profile_keeps_the_normalised_key(self):
        self.assertEqual(self.hero(1)['nick_key'], 'ineedahero')
        self.assertIn('INeedAHero', self.p.db, 'оригинал — имя записи')

    def test_stats_go_to_the_similar_profile(self):
        self.hero()
        name, score = self.p.resolve('TNeedAHero')
        self.assertEqual(name, 'INeedAHero')
        self.assertAlmostEqual(score, 0.9)
        self.p.update_all({1: {'vpip': True}}, nicks={1: 'TNeedAHero'})
        self.assertNotIn('TNeedAHero', self.p.db)
        self.assertEqual(self.p.db['INeedAHero']['hands'], 52)

    def test_new_spelling_is_remembered_as_an_alias(self):
        self.hero()
        self.p.resolve('TNeedAHero')
        self.assertEqual(self.p.db['INeedAHero']['aliases'], ['TNeedAHero'])
        self.p.resolve('TNeedAHero')
        self.assertEqual(self.p.db['INeedAHero']['aliases'], ['TNeedAHero'], 'без дублей')

    def test_alias_matches_the_next_time(self):
        """Вариант уже записан — сравнение идёт и с ним тоже."""
        self.hero(3, name='МеедАНего')
        self.p.add_alias('МеедАНего', 'INeedAHero')
        self.assertEqual(self.p.resolve('TNeedAHero')[0], 'МеедАНего')

    def test_a_different_nick_gets_its_own_profile(self):
        self.hero()
        self.assertEqual(self.p.resolve('HerGlinoMes'), ('HerGlinoMes', 0.0))
        self.p.update_all({1: {'vpip': True}}, nicks={1: 'HerGlinoMes'})
        self.assertEqual(sorted(self.p.db), ['HerGlinoMes', 'INeedAHero'])

    def test_short_nick_needs_an_exact_match(self):
        self.hero(4, name='Ace1')
        self.assertEqual(self.p.resolve('Ace2'), ('Ace2', 0.0))
        self.assertEqual(self.p.resolve('ace 1')[0], 'Ace1')

    def test_seat_names_are_out_of_the_comparison(self):
        """«Оппонент 1» — стул, а не ник: ни цель сравнения, ни его источник."""
        self.p.update('Оппонент 1', {'vpip': True})
        self.assertEqual(self.p.match('Оппонент 2'), (None, 0.0), 'место не с чем сравнивать')
        self.assertEqual(self.p.resolve('Оппонент 2'), ('Оппонент 2', 0.0))
        # ник, похожий на имя места (0.84), в статистику стула не уходит
        self.assertGreater(opponents.similar('Оппонентус', 'Оппонент 1'),
                           opponents.NICK_RATIO)
        self.assertEqual(self.p.resolve('Оппонентус'), ('Оппонентус', 0.0))
        self.assertNotIn('aliases', self.p.db['Оппонент 1'])

    def test_merged_record_forwards_to_its_target(self):
        self.hero()
        self.p.db['TNeedAHero'] = dict(opponents.blank(), leaks=['лик'], hands=1)
        self.p.merge('TNeedAHero', 'INeedAHero')
        self.assertEqual(self.p.resolve('TNeedAHero')[0], 'INeedAHero')


class DuplicateMergeTest(unittest.TestCase):
    """Слияние дублей, накопившихся в players.json до нестрогого сравнения."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix='clubgg_dup_')
        self.p = opponents.Profiles(path=os.path.join(tmp, 'players.json'), db={})

    def rec(self, name, hands, **kw):
        rec = opponents.blank(opponents.NICK_NOTE)
        rec.update({'hands': hands, 'vpip_hands': hands, 'pfr_hands': 0,
                    'three_bet_spots': hands, 'three_bet_hands': 0,
                    'agg_bets': hands, 'agg_calls': hands,
                    'first_seen': '2026-02-01', 'last_seen': '2026-02-01'})
        rec.update(kw)
        self.p.db[name] = rec
        return rec

    def test_counters_add_up_and_the_dupe_disappears(self):
        self.rec('INeedAHero', 51)
        self.rec('TNeedAHero', 1, first_seen='2026-01-01')
        moves = self.p.merge_duplicates()
        self.assertEqual([(s, d, n) for s, d, n, _ in moves],
                         [('TNeedAHero', 'INeedAHero', 1)])
        got = self.p.db['INeedAHero']
        self.assertNotIn('TNeedAHero', self.p.db)
        self.assertEqual(got['hands'], 52)
        self.assertEqual(got['vpip_hands'], 52)
        self.assertEqual(got['vpip'], 1.0, 'доли пересчитаны, а не сложены')
        self.assertEqual(got['first_seen'], '2026-01-01', 'ранний first_seen')
        self.assertEqual(got['aliases'], ['TNeedAHero'])

    def test_a_smiley_over_a_letter_is_the_same_player(self):
        self.rec('МеедАНего', 13)
        self.rec('Г еедАНего', 1)
        self.p.merge_duplicates()
        self.assertEqual(sorted(self.p.db), ['МеедАНего'])
        self.assertEqual(self.p.db['МеедАНего']['hands'], 14)

    def test_different_players_are_left_alone(self):
        self.rec('INeedAHero', 51)
        self.rec('HerGlinoMes', 7)
        self.rec('Оппонент 2', 3)
        self.assertEqual(self.p.merge_duplicates(), [])
        self.assertEqual(sorted(self.p.db),
                         ['HerGlinoMes', 'INeedAHero', 'Оппонент 2'])

    def test_hand_written_notes_survive_the_merge(self):
        self.rec('INeedAHero', 51)
        self.rec('TNeedAHero', 1, leaks=['коллит всё подряд'], fold_to_3bet=0.4)
        self.p.merge_duplicates()
        old = self.p.db['TNeedAHero']
        self.assertEqual(old['merged_into'], 'INeedAHero')
        self.assertEqual(old['leaks'], ['коллит всё подряд'])
        self.assertEqual(old['hands'], 0, 'руки не должны посчитаться дважды')
        self.assertEqual(self.p.db['INeedAHero']['hands'], 52)

    def test_three_spellings_collapse_into_one(self):
        self.rec('INeedAHero', 51)
        self.rec('TNeedAHero', 1)
        self.rec('lNeedAHero', 2)
        self.p.merge_duplicates()
        self.assertEqual(sorted(self.p.db), ['INeedAHero'])
        self.assertEqual(self.p.db['INeedAHero']['hands'], 54)
        self.assertEqual(sorted(self.p.db['INeedAHero']['aliases']),
                         ['TNeedAHero', 'lNeedAHero'])

    def test_hero_and_junk_keys_are_untouched(self):
        self.p.db['_comment'] = 'это не профиль'
        self.p.db[config.HERO_NAME] = opponents.blank('Это я (герой)')
        self.rec(config.HERO_NAME + '1', 4)       # похоже на героя, но это не он
        self.assertEqual(self.p.merge_duplicates(), [])
        self.assertIn('_comment', self.p.db)
        self.assertEqual(self.p.db[config.HERO_NAME]['hands'], 0)

    def test_bot_merges_on_start_and_logs_it(self):
        tmp = tempfile.mkdtemp(prefix='clubgg_dup_bot_')
        path = os.path.join(tmp, 'players.json')
        bot = Bot(mock.Mock(), dry_run=True, players_db={},
                  players_path=path, log_path=os.path.join(tmp, 'bot.log'),
                  history_path=os.path.join(tmp, 'h.jsonl'))
        self.p = bot.profiles
        self.rec('INeedAHero', 51)
        self.rec('TNeedAHero', 1)
        lines = []
        bot.log = lines.append
        bot.log_memory()
        self.assertIn('слиты дубли: "TNeedAHero" -> "INeedAHero" (1 рука)', lines)
        self.assertEqual(bot.players_db['INeedAHero']['hands'], 52)
        self.assertTrue(os.path.exists(path), 'слитая база записана на диск')


class MergeTest(unittest.TestCase):
    """Слияние: накопленное на «Оппоненте N» достаётся человеку, а не стулу."""

    def setUp(self):
        tmp = tempfile.mkdtemp(prefix='clubgg_nick_')
        self.p = opponents.Profiles(path=os.path.join(tmp, 'players.json'), db={})

    def seat_stats(self, hands=10, **kw):
        rec = opponents.blank(opponents.SEAT_NOTE)
        rec.update({'hands': hands, 'vpip_hands': 6, 'pfr_hands': 3,
                    'three_bet_spots': 4, 'three_bet_hands': 1,
                    'agg_bets': 8, 'agg_calls': 4, 'first_seen': '2026-01-01',
                    'last_seen': '2026-02-01'})
        rec.update(kw)
        self.p.db['Оппонент 3'] = rec
        return rec

    def test_counters_move_and_source_disappears(self):
        self.seat_stats()
        self.assertEqual(self.p.merge('Оппонент 3', 'PokerPro88'), 10)
        self.assertNotIn('Оппонент 3', self.p.db)
        got = self.p.db['PokerPro88']
        self.assertEqual(got['hands'], 10)
        self.assertEqual(got['vpip_hands'], 6)
        self.assertEqual(got['agg_bets'], 8)
        self.assertEqual(got['vpip'], 0.6)
        self.assertEqual(got['agg'], 2.0)
        self.assertEqual(got['first_seen'], '2026-01-01')

    def test_counters_add_up_to_existing_nick_profile(self):
        """Ник уже знаком (тот же человек за другим столом) — цифры складываются."""
        self.p.update('PokerPro88', {'vpip': True, 'bets': 2, 'passive': 1})
        self.seat_stats(hands=10)
        self.p.merge('Оппонент 3', 'PokerPro88')
        got = self.p.db['PokerPro88']
        self.assertEqual(got['hands'], 11)
        self.assertEqual(got['vpip_hands'], 7)
        self.assertEqual(got['agg_bets'], 10)

    def test_hand_written_data_is_not_lost(self):
        """У места есть лики/fold_to_3bet — запись остаётся, но руки не двоятся."""
        self.seat_stats(leaks=['коллит слишком много'], fold_to_3bet=0.4)
        self.p.merge('Оппонент 3', 'PokerPro88')
        old = self.p.db['Оппонент 3']
        self.assertEqual(old['merged_into'], 'PokerPro88')
        self.assertEqual(old['hands'], 0)
        self.assertEqual(old['leaks'], ['коллит слишком много'])
        self.assertEqual(self.p.db['PokerPro88']['hands'], 10)

    def test_merged_record_is_hidden_from_panel_and_merged_once(self):
        self.seat_stats(leaks=['лик'])
        self.p.merge('Оппонент 3', 'PokerPro88')
        self.assertEqual(self.p.merge('Оппонент 3', 'PokerPro88'), 0)
        self.assertEqual(self.p.db['PokerPro88']['hands'], 10)
        self.assertEqual([o['name'] for o in self.p.opponents()], ['PokerPro88'])

    def test_nothing_to_merge(self):
        self.assertEqual(self.p.merge('Оппонент 3', 'PokerPro88'), 0)
        self.assertNotIn('PokerPro88', self.p.db)

    def test_merge_into_itself_is_noop(self):
        self.seat_stats()
        self.assertEqual(self.p.merge('Оппонент 3', 'Оппонент 3'), 0)
        self.assertEqual(self.p.db['Оппонент 3']['hands'], 10)

    def test_update_all_names_profiles_by_nick(self):
        # место 2 без ника, но со ставкой: без единого действия оно считалось бы
        # пустой плашкой и профиля бы не завело (см. GhostSeatTest)
        observed = {1: {'vpip': True}, 2: {'vpip': False, 'bets': 1}}
        names = self.p.update_all(observed, nicks={1: 'PokerPro88'})
        self.assertEqual(names, ['PokerPro88', 'Оппонент 2'])
        self.assertEqual(self.p.db['PokerPro88']['notes'], opponents.NICK_NOTE)
        self.assertEqual(self.p.db['Оппонент 2']['notes'], opponents.SEAT_NOTE)

    def test_update_all_without_nicks_keeps_old_behaviour(self):
        self.assertEqual(self.p.update_all({1: {'vpip': True}}), ['Оппонент 1'])


class BotNickTest(unittest.TestCase):
    """Бот: раз в раздачу, кэш, лог, флаг read_nicks."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_nick_')
        self.bot = Bot(mock.Mock(), dry_run=True, players_db={},
                       players_path=os.path.join(self.tmp, 'players.json'),
                       log_path=os.path.join(self.tmp, 'bot.log'),
                       history_path=os.path.join(self.tmp, 'h.jsonl'))
        self.bot.tesseract = EXE
        self.lines = []
        self.bot.log = self.lines.append
        self.img = cv2.imread(SHOTS[0])

    def state(self, seated=2):
        return state(seated=seated)

    def read(self, nicks):
        return mock.patch.object(nr, 'read_nicks', return_value=dict(nicks))

    def test_flag_off_keeps_seat_names(self):
        self.bot.read_nicks = False
        with mock.patch.object(nr, 'read_nicks') as read:
            self.assertEqual(self.bot.update_nicks(self.img, self.state()), {})
        read.assert_not_called()
        self.assertEqual(self.bot.profiles.update_all({1: {'vpip': True}},
                                                      nicks=self.bot.nicks),
                         ['Оппонент 1'])

    def test_nick_is_logged_and_stats_are_moved(self):
        self.bot.profiles.update('Оппонент 1', {'vpip': True})
        with self.read({1: 'PokerPro88'}):
            nicks = self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(nicks, {1: 'PokerPro88'})
        self.assertIn('оппонент на месте 1 → ник "PokerPro88" '
                      '(статистика перенесена: 1 рука)', self.lines)
        self.assertNotIn('Оппонент 1', self.bot.players_db)
        self.assertEqual(self.bot.players_db['PokerPro88']['hands'], 1)

    def test_unreadable_nick_is_logged_once(self):
        with self.read({}):
            self.bot.update_nicks(self.img, self.state(seated=1))
            self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(self.lines,
                         ['ник на месте 1 не прочитался — Оппонент 1 (место)'])

    def test_nick_is_logged_once_per_change(self):
        with self.read({1: 'PokerPro88'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
            self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(len(self.lines), 1)

    def test_cache_survives_a_hand_the_ocr_missed(self):
        """Смайлик закрыл плашку — ник берётся из прошлой раздачи, не теряется."""
        with self.read({1: 'PokerPro88'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
        with self.read({}):
            nicks = self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(nicks, {1: 'PokerPro88'})

    def test_empty_seat_drops_the_cache(self):
        """Место опустело — там сядет другой человек, старый ник не наследуем."""
        with self.read({1: 'PokerPro88'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
        with self.read({}):
            self.assertEqual(self.bot.update_nicks(self.img, self.state(seated=0)), {})
            self.assertEqual(self.bot.update_nicks(self.img, self.state(seated=1)), {})

    def test_new_spelling_of_a_known_nick_goes_to_the_same_profile(self):
        """OCR перепутал букву — профиль тот же, в логе видно почему."""
        for _ in range(51):
            self.bot.profiles.update('INeedAHero', {'vpip': True})
        with self.read({1: 'TNeedAHero'}):
            nicks = self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(nicks, {1: 'INeedAHero'})
        self.assertIn('ник "tneedahero" похож на "INeedAHero" (0.9) — статистика туда',
                      self.lines)
        self.assertNotIn('TNeedAHero', self.bot.players_db)
        self.assertEqual(self.bot.players_db['INeedAHero']['aliases'], ['TNeedAHero'])

    def test_seat_stats_move_even_when_the_nick_is_a_new_spelling(self):
        """Накопленное «Оппонентом 1» уходит человеку, а не второму написанию."""
        self.bot.profiles.update('Оппонент 1', {'vpip': True})
        for _ in range(51):
            self.bot.profiles.update('INeedAHero', {'vpip': True})
        with self.read({1: 'TNeedAHero'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertNotIn('Оппонент 1', self.bot.players_db)
        self.assertEqual(self.bot.players_db['INeedAHero']['hands'], 52)
        self.assertTrue(any('статистика перенесена: 1 рука' in s for s in self.lines))

    def test_seat_cache_holds_a_nick_read_in_another_alphabet(self):
        """«INeedAHero» и «МеедАНего» — одна плашка: по буквам не похожи совсем."""
        with self.read({1: 'INeedAHero'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
        self.bot.save_profiles({1: {'vpip': True}})
        self.assertLess(opponents.similar('INeedAHero', 'МеедАНего'),
                        opponents.NICK_RATIO)
        with self.read({1: 'МеедАНего'}):
            nicks = self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(nicks, {1: 'INeedAHero'}, 'место не пустело — игрок тот же')
        self.assertIn('место 1: ник "МеедАНего" → тот же игрок (кэш места), '
                      'статистика в "INeedAHero" (алиас добавлен)', self.lines)
        self.bot.save_profiles({1: {'vpip': True}})
        self.assertEqual(sorted(self.bot.players_db), ['INeedAHero'])
        self.assertEqual(self.bot.players_db['INeedAHero']['hands'], 2)
        self.assertEqual(self.bot.players_db['INeedAHero']['aliases'], ['МеедАНего'])

    def test_empty_seat_drops_the_cache_and_a_new_player_gets_a_profile(self):
        """Место опустело — там сядет другой человек, ник ему не наследуется."""
        with self.read({1: 'INeedAHero'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
        self.bot.save_profiles({1: {'vpip': True}})
        with self.read({}):
            self.bot.update_nicks(self.img, self.state(seated=0))
        with self.read({1: 'HerGlinoMes'}):
            nicks = self.bot.update_nicks(self.img, self.state(seated=1))
        self.assertEqual(nicks, {1: 'HerGlinoMes'})
        self.bot.save_profiles({1: {'vpip': True}})
        self.assertEqual(sorted(self.bot.players_db), ['HerGlinoMes', 'INeedAHero'])

    def test_reader_failure_does_not_break_the_hand(self):
        with mock.patch.object(nr, 'read_nicks', side_effect=RuntimeError('boom')):
            self.assertEqual(self.bot.update_nicks(self.img, self.state()), {})
        self.assertTrue(any('ники не прочитаны' in s for s in self.lines))

    def test_saved_profile_gets_the_nick(self):
        with self.read({1: 'PokerPro88'}):
            self.bot.update_nicks(self.img, self.state(seated=1))
        self.bot.save_profiles({1: {'vpip': True, 'bets': 0, 'passive': 0}})
        self.assertIn('PokerPro88', self.bot.players_db)

    def test_flag_is_reported_in_history(self):
        self.bot.read_nicks = True
        self.assertIn('read_nicks', self.bot.active_flags())
        self.bot.read_nicks = False
        self.assertNotIn('read_nicks', self.bot.active_flags())

    def test_devices_json_switches_the_flag(self):
        self.bot.apply_config({'read_nicks': False})
        self.assertFalse(self.bot.read_nicks)
        self.bot.apply_config({})
        self.assertTrue(self.bot.read_nicks)          # по умолчанию включено
        self.bot.apply_config({'tesseract': '/opt/tesseract'})
        self.assertEqual(self.bot.tesseract, '/opt/tesseract')


if __name__ == '__main__':
    unittest.main()
