#!/usr/bin/env python3
"""Тесты памяти оппонентов: наблюдения по кадрам -> players.json -> адаптация.

Кадры здесь не рисуются: наблюдатель работает с уже прочитанным состоянием
стола, поэтому состояния собираются словарями — так видно, что именно бот
считает «наблюдаемым».
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config                                # noqa: E402
import opponents                             # noqa: E402
from main import Bot                         # noqa: E402


def state(street='preflop', hole=('Ah', 'Kd'), seated=1, live=None, my_turn=False,
          has_bet=False, to_call=None, first_to_act='opp'):
    """Состояние стола: seated — оппонентов за столом, live — из них с картами."""
    live = seated if live is None else live
    seats = [{'x': 100, 'y': 900, 'hero': True, 'in_hand': True}]
    for i in range(seated):
        seats.append({'x': 200 + i, 'y': 100, 'hero': False, 'in_hand': i < live})
    board = {'preflop': [], 'flop': ['2c', '7d', 'Ts'], 'turn': ['2c', '7d', 'Ts', '3h'],
             'river': ['2c', '7d', 'Ts', '3h', '9s']}[street]
    return {'hole': list(hole), 'board': board, 'street': street, 'seats': seats,
            'my_turn': my_turn, 'in_hand': True, 'has_bet': has_bet,
            'to_call_bb': to_call, 'first_to_act': first_to_act, 'pot_bb': 3.0}


class ObserverTest(unittest.TestCase):
    """Что именно бот записывает оппоненту по наблюдаемым кадрам."""

    def setUp(self):
        # confirm=1: подтверждение карт проверяется отдельным тестом, а здесь
        # каждая раздача задаётся кадрами явно
        self.obs = opponents.HandObserver(confirm=1)

    def hand(self, frames, hero_actions=()):
        """Прогнать кадры одной раздачи и закрыть её. hero_actions: (улица, действие)."""
        actions = list(hero_actions)
        for frame in frames:
            self.obs.observe(frame)
            while actions and actions[0][0] == frame['street'] and frame['my_turn']:
                street, action = actions.pop(0)
                self.obs.note_action(action, street)
        return self.obs.finish()

    def test_opponent_who_saw_the_flop_gets_vpip(self):
        out = self.hand([state('preflop'), state('flop')])
        self.assertTrue(out[1]['vpip'])

    def test_fold_before_the_flop_is_not_vpip(self):
        """Оппонент выбросил карты до флопа — раздача считается, VPIP нет."""
        out = self.hand([state('preflop'), state('preflop', live=0)])
        self.assertTrue(out[1]['seen'])
        self.assertFalse(out[1]['vpip'])

    def test_raise_before_us_is_pfr(self):
        out = self.hand([state('preflop', my_turn=True, has_bet=True, to_call=2.0)])
        self.assertTrue(out[1]['pfr'])
        self.assertTrue(out[1]['vpip'])

    def test_blind_is_not_a_raise(self):
        """Колл в пол-блайнда с малого блайнда — это не рейз оппонента."""
        out = self.hand([state('preflop', my_turn=True, has_bet=True, to_call=0.5)])
        self.assertFalse(out[1]['pfr'])

    def test_reraise_after_our_raise_is_a_three_bet(self):
        frames = [state('preflop', my_turn=True, has_bet=False),
                  state('preflop', my_turn=True, has_bet=True, to_call=6.0)]
        out = self.hand(frames, hero_actions=[('preflop', 'raise')])
        self.assertTrue(out[1]['three_bet'])
        self.assertTrue(out[1]['three_bet_spot'], 'наш рейз = спот для 3-бета')

    def test_three_bet_seen_without_digits(self):
        """Сумма колла не читается (нет эталонов цифр) — ререйз всё равно виден."""
        frames = [state('preflop', my_turn=True),
                  state('preflop', my_turn=True, has_bet=True, to_call=None)]
        out = self.hand(frames, hero_actions=[('preflop', 'raise')])
        self.assertTrue(out[1]['three_bet'])

    def test_postflop_bet_counts_as_aggression(self):
        frames = [state('flop', my_turn=True, has_bet=True, to_call=2.0),
                  state('turn', my_turn=True, has_bet=True, to_call=4.0)]
        out = self.hand(frames)
        self.assertEqual(out[1]['bets'], 2)
        self.assertEqual(out[1]['passive'], 0)

    def test_the_same_bet_is_counted_once(self):
        """За один ход состояние читается несколько раз — ставка одна."""
        frame = state('flop', my_turn=True, has_bet=True, to_call=2.0)
        out = self.hand([frame, frame, frame])
        self.assertEqual(out[1]['bets'], 1)

    def test_check_to_us_is_passive(self):
        out = self.hand([state('flop', my_turn=True, first_to_act='opp')])
        self.assertEqual(out[1]['passive'], 1)
        self.assertEqual(out[1]['bets'], 0)

    def test_we_bet_first_is_not_an_opponent_check(self):
        out = self.hand([state('flop', my_turn=True, first_to_act='me')])
        self.assertEqual(out[1]['passive'], 0)

    def test_call_of_our_bet_is_passive(self):
        """Мы поставили на флопе, оппонент доехал до тёрна с картами — он коллировал."""
        frames = [state('flop', my_turn=True, first_to_act='me'), state('turn')]
        out = self.hand(frames, hero_actions=[('flop', 'raise')])
        self.assertEqual(out[1]['passive'], 1)
        self.assertEqual(out[1]['bets'], 0)

    def test_two_opponents_share_vpip_but_not_bets(self):
        """Кто из двоих поставил — не видно; в статистику агрессии это не идёт."""
        out = self.hand([state('flop', seated=2, my_turn=True, has_bet=True, to_call=2.0)])
        self.assertEqual(sorted(out), [1, 2])
        self.assertTrue(out[1]['vpip'] and out[2]['vpip'])
        self.assertEqual(out[1]['bets'] + out[2]['bets'], 0)

    def test_new_hole_cards_close_the_hand(self):
        self.assertIsNone(self.obs.observe(state('preflop')))
        self.assertIsNone(self.obs.observe(state('flop')))
        done = self.obs.observe(state('preflop', hole=('7s', '7d')))
        self.assertIsNotNone(done, 'смена карманных карт = раздача закрыта')
        self.assertTrue(done[1]['vpip'])

    def test_one_misread_card_does_not_start_a_hand(self):
        """Карту прочитали неверно на одном кадре — лишней раздачи не появилось."""
        obs = opponents.HandObserver()          # как в игре: подтверждение двумя кадрами
        for frame in (state('preflop'), state('preflop'), state('flop')):
            obs.observe(frame)
        self.assertIsNone(obs.observe(state('flop', hole=('Ah', '2c'))), 'мусорный кадр')
        obs.observe(state('flop'))
        self.assertEqual(obs.hole, ['Ah', 'Kd'], 'раздача та же')
        self.assertTrue(obs.finish()[1]['vpip'])

    def test_frames_without_hero_are_ignored(self):
        """Без плашки героя круг мест не привязан к нему — считать нельзя."""
        s = state('flop')
        s['seats'] = [{'x': 1, 'y': 1, 'hero': False, 'in_hand': True}]
        self.assertIsNone(self.hand([s]))

    def test_table_reset_closes_the_hand_too(self):
        """Стол обнулился — раздача закрыта, хотя карманные карты и не менялись."""
        self.obs.observe(state('preflop'))
        self.obs.observe(state('flop'))
        done = self.obs.table_reset()
        self.assertIsNotNone(done)
        self.assertTrue(done[1]['vpip'])
        self.assertIsNone(self.obs.table_reset(), 'закрывать больше нечего')


class ProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_opp_')
        self.path = os.path.join(self.tmp, 'players.json')

    def test_counters_turn_into_shares(self):
        p = opponents.Profiles(self.path)
        for vpip in (True, True, False, False):
            p.update('Оппонент 1', {'vpip': vpip, 'pfr': vpip, 'bets': 1, 'passive': 2})
        prof = p.profile('Оппонент 1')
        self.assertEqual(prof['hands'], 4)
        self.assertAlmostEqual(prof['vpip'], 0.5)
        self.assertAlmostEqual(prof['pfr'], 0.5)
        self.assertAlmostEqual(prof['agg'], 0.5, places=2)

    def test_three_bet_is_a_share_of_spots(self):
        p = opponents.Profiles(self.path)
        p.update('Оппонент 1', {'three_bet_spot': True, 'three_bet': True})
        p.update('Оппонент 1', {'three_bet_spot': True})
        p.update('Оппонент 1', {})               # спота не было — знаменатель не растёт
        self.assertAlmostEqual(p.profile('Оппонент 1')['three_bet'], 0.5)

    def test_old_record_keeps_its_stats(self):
        """Запись прежней версии (только доли) не обнуляется, а дополняется."""
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump({'X': {'hands': 10, 'vpip': 0.4, 'pfr': 0.2, 'three_bet': 0.1,
                             'agg': 1.0}}, f)
        p = opponents.Profiles(self.path)
        p.update('X', {'vpip': True, 'pfr': True})
        prof = p.profile('X')
        self.assertEqual(prof['hands'], 11)
        self.assertAlmostEqual(prof['vpip'], round(5 / 11, 3))

    def test_saved_file_is_valid_json_with_several_players(self):
        p = opponents.Profiles(self.path)
        p.update_all({1: {'vpip': True}, 2: {'vpip': False, 'bets': 3}})
        self.assertTrue(p.save())
        with open(self.path, encoding='utf-8') as f:
            db = json.load(f)
        self.assertEqual(sorted(db), ['Оппонент 1', 'Оппонент 2'])
        self.assertEqual(db['Оппонент 2']['agg_bets'], 3)

    def test_hero_is_not_an_opponent(self):
        p = opponents.Profiles(self.path, db={config.HERO_NAME: opponents.blank(),
                                              'Оппонент 1': opponents.blank()})
        self.assertEqual([o['name'] for o in p.opponents()], ['Оппонент 1'])

    def test_summary_line(self):
        line = opponents.summary_line('Оппонент 1', {'hands': 12, 'vpip': 0.34,
                                                     'pfr': 0.18, 'agg': 1.83})
        self.assertEqual(line, 'Оппонент 1 — 12 рук, VPIP 34%, PFR 18%, Agg 1.8')

    def test_hands_are_counted_in_russian(self):
        for n, word in ((1, 'рука'), (2, 'руки'), (5, 'рук'), (11, 'рук'),
                        (21, 'рука'), (114, 'рук')):
            with self.subTest(n=n):
                self.assertEqual(opponents.hands_word(n), word)


class GhostSeatTest(unittest.TestCase):
    """Пустое место: клиент рисует плашку, а сидеть за ней некому.

    Живой случай игры один на один: в players.json завёлся «Оппонент 2» — 5 рук,
    VPIP 0%, PFR 0%, за все раздачи ни одного действия. Такое место в статистику
    попадать не должно вовсе.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_ghost_')
        self.p = opponents.Profiles(os.path.join(self.tmp, 'players.json'), db={})

    def empty(self):
        """Наблюдения за местом, которое ничего не сделало (и не сидит)."""
        return {'seen': True, 'in_hand': False, 'vpip': False, 'pfr': False,
                'three_bet': False, 'three_bet_spot': False, 'bets': 0, 'passive': 0}

    def test_empty_seat_creates_no_profile(self):
        self.assertEqual(self.p.update_all({1: self.empty(), 2: self.empty()}), [])
        self.assertEqual(self.p.db, {})

    def test_seat_that_played_gets_a_profile(self):
        for obs in ({'vpip': True}, {'pfr': True}, {'bets': 1}, {'passive': 1}):
            with self.subTest(obs=obs):
                p = opponents.Profiles(self.p.path, db={})
                self.assertEqual(p.update_all({1: dict(self.empty(), **obs)}),
                                 ['Оппонент 1'])
                self.assertEqual(p.db['Оппонент 1']['hands'], 1)

    def test_read_nick_keeps_the_profile_without_actions(self):
        """Человек сидит и всё сбрасывает — ник прочитан, раздача считается."""
        names = self.p.update_all({1: self.empty()}, nicks={1: 'PokerPro88'})
        self.assertEqual(names, ['PokerPro88'])
        self.assertEqual(self.p.db['PokerPro88']['hands'], 1)
        self.assertEqual(self.p.db['PokerPro88']['vpip'], 0.0)

    def test_hands_of_a_known_folder_keep_counting(self):
        """Место уже играло — его нулевые раздачи считаются, иначе VPIP уедет вверх."""
        self.p.update_all({1: dict(self.empty(), vpip=True)})
        self.p.update_all({1: self.empty()})
        prof = self.p.db['Оппонент 1']
        self.assertEqual(prof['hands'], 2)
        self.assertAlmostEqual(prof['vpip'], 0.5)

    def test_old_ghost_is_forgotten_on_the_next_empty_hand(self):
        """Пустышка прошлых версий (5 рук, всюду нули) уходит из базы."""
        self.p.db['Оппонент 2'] = dict(opponents.blank(opponents.SEAT_NOTE), hands=5)
        self.assertEqual(self.p.update_all({2: self.empty()}), [])
        self.assertNotIn('Оппонент 2', self.p.db)
        self.assertEqual(self.p.dropped, ['Оппонент 2'])

    def test_ghost_with_hand_written_notes_stays(self):
        """В записи есть вписанное руками — стирать нельзя, даже пустую."""
        for manual in ({'leaks': ['коллит всё подряд']}, {'fold_to_3bet': 0.4},
                       {'notes': 'это Вася с работы'}):
            with self.subTest(manual=manual):
                p = opponents.Profiles(self.p.path, db={})
                p.db['Оппонент 2'] = dict(opponents.blank(opponents.SEAT_NOTE),
                                          hands=5, **manual)
                self.assertEqual(p.update_all({2: self.empty()}), [])
                self.assertIn('Оппонент 2', p.db)
                self.assertEqual(p.db['Оппонент 2']['hands'], 5, 'руки не растут')
                self.assertEqual(p.dropped, [])

    def test_profile_with_actions_is_not_a_ghost(self):
        self.assertTrue(opponents.is_ghost(opponents.blank()))
        self.assertTrue(opponents.is_ghost(None))
        self.assertFalse(opponents.is_ghost(dict(opponents.blank(), agg_calls=1)))
        # спот 3-бета создаём мы своим рейзом — оппонент в нём мог не сделать ничего
        self.assertTrue(opponents.is_ghost(dict(opponents.blank(), three_bet_spots=2)))


class StubScreen:
    def grab(self):
        return None

    def tap(self, x, y):
        pass


class SoloOpponentTest(unittest.TestCase):
    """Под кого именно подстраивается решение, когда профилей в базе несколько.

    Подстройка применима только против одного оппонента (чьи цифры брать в
    мультипоте — непонятно), но раньше «один» определялось по базе: ровно один
    профиль в players.json. С приходом ников база копит всех, кого бот когда-либо
    видел, и подстройка тихо умирала со второго знакомого игрока. Теперь
    оппонента называют плашки кадра.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_solo_')

    def bot(self, db=None, nicks=None, **cfg):
        b = Bot(StubScreen(), players_db=db if db is not None else {},
                players_path=os.path.join(self.tmp, 'players.json'),
                log_path=os.path.join(self.tmp, 'bot.log'),
                history_path=os.path.join(self.tmp, 'h.jsonl'), cfg=cfg)
        b.nicks = dict(nicks or {})
        return b

    @staticmethod
    def prof(hands=30, vpip=0.55):
        return dict(opponents.blank(opponents.NICK_NOTE), hands=hands, vpip=vpip, agg=0.5)

    def db(self):
        return {'Вася': self.prof(vpip=0.55), 'Петя': self.prof(vpip=0.15)}

    def test_the_nick_of_the_live_seat_picks_the_profile(self):
        """Двое в базе, за столом один — берём цифры того, кто в раздаче."""
        bot = self.bot(db=self.db(), nicks={1: 'Вася', 2: 'Петя'})
        self.assertEqual(bot.solo_opponent(state(seated=2, live=1)), 'Вася')
        self.assertEqual(bot.opponent_profile(state(seated=2, live=1)),
                         bot.players_db['Вася'])

    def test_the_second_seat_is_counted_the_way_the_observer_counts(self):
        """Место по кругу — порядок плашек без героя, как в HandObserver."""
        bot = self.bot(db=self.db(), nicks={1: 'Вася', 2: 'Петя'})
        s = state(seated=2, live=2)
        s['seats'][1]['in_hand'] = False          # первый оппонент сбросил
        self.assertEqual(bot.solo_opponent(s), 'Петя')
        self.assertEqual(bot.opponent_profile(s)['vpip'], 0.15)

    def test_a_multiway_pot_has_no_profile(self):
        bot = self.bot(db=self.db(), nicks={1: 'Вася', 2: 'Петя'})
        self.assertIsNone(bot.solo_opponent(state(seated=2, live=2)))
        self.assertIsNone(bot.opponent_profile(state(seated=2, live=2)))

    def test_nobody_in_the_hand_has_no_profile(self):
        bot = self.bot(db=self.db(), nicks={1: 'Вася'})
        self.assertIsNone(bot.solo_opponent(state(seated=2, live=0)))

    def test_a_seat_without_a_nick_falls_back_to_its_number(self):
        """Ники не прочитались — статистика писалась по местам, по ним и берём."""
        bot = self.bot(db={'Оппонент 1': self.prof()})
        self.assertEqual(bot.solo_opponent(state(seated=2, live=1)), 'Оппонент 1')
        self.assertEqual(bot.opponent_profile(state(seated=2, live=1))['hands'], 30)

    def test_an_unknown_opponent_has_no_profile_yet(self):
        """За столом новый человек — цифры соседа к нему не применяются."""
        bot = self.bot(db=self.db(), nicks={1: 'Коля'})
        self.assertIsNone(bot.opponent_profile(state(seated=2, live=1)))

    def test_a_merged_nick_leads_to_the_surviving_profile(self):
        """Ник склеен с дублем — решение правится по тому профилю, куда всё свели."""
        db = {'Вася': self.prof(hands=40), 'Bacя': {'merged_into': 'Вася'}}
        bot = self.bot(db=db, nicks={1: 'Bacя'})
        self.assertEqual(bot.opponent_profile(state(seated=2, live=1))['hands'], 40)

    def test_a_multiway_frame_by_the_player_count_has_no_profile(self):
        """Плашек в состоянии нет, но игроков больше двух — подстройки нет."""
        bot = self.bot(db={'Вася': self.prof()})
        self.assertIsNone(bot.opponent_profile({'players': 6}))
        self.assertEqual(bot.opponent_profile({'players': 2}), bot.players_db['Вася'])

    def test_without_seats_the_only_profile_is_still_used(self):
        """Разбор кадра без плашек — прежнее правило: единственный профиль в базе."""
        bot = self.bot(db={'Вася': self.prof()})
        self.assertEqual(bot.opponent_profile(), bot.players_db['Вася'])
        self.assertIsNone(self.bot(db=self.db()).opponent_profile())

    def test_the_flag_still_switches_everything_off(self):
        bot = self.bot(db=self.db(), nicks={1: 'Вася'}, opponent_memory=False)
        self.assertIsNone(bot.opponent_profile(state(seated=2, live=1)))

    def test_both_entry_points_number_the_seats_the_same(self):
        """Правило нумерации одно на всех: и наблюдатель, и выбор профиля — из opponents.

        Раньше оно было выписано дважды (HandObserver и Bot.solo_opponent), и
        разойтись им ничего не мешало — а разошлись бы, статистика писалась бы
        одному оппоненту, а решение правилось бы по цифрам другого.
        """
        bot = self.bot(db=self.db())
        for seat in (1, 2, 3):
            with self.subTest(seat=seat):
                frame = state(seated=3, live=3)
                for i, s in enumerate(frame['seats'][1:], 1):
                    s['in_hand'] = i == seat
                obs = opponents.HandObserver(confirm=1)
                obs.observe(frame)
                self.assertEqual(obs.solo, seat, 'наблюдатель')
                self.assertEqual(opponents.solo_seat(frame), seat, 'общая функция')
                self.assertEqual(bot.solo_opponent(frame), opponents.seat_name(seat))


class BotFixture(unittest.TestCase):
    """Общая обвязка: бот на заглушке экрана и своей базе во временной папке."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_mem_')
        self.players = os.path.join(self.tmp, 'players.json')

    def bot(self, db=None, **cfg):
        return Bot(StubScreen(), players_db=db if db is not None else {},
                   players_path=self.players,
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'), cfg=cfg)

    def play(self, bot, frames):
        """Каждый кадр показывается дважды: карты бот подтверждает вторым кадром."""
        for frame in frames:
            bot.observe(frame)
            bot.observe(frame)


class BotMemoryTest(BotFixture):
    """Игровой цикл: бот сам пишет профили и сам под них подстраивается."""

    def test_profiles_are_written_after_the_hand(self):
        bot = self.bot()
        self.play(bot, [state('preflop'), state('flop'),
                        state('preflop', hole=('7s', '7d'))])
        with open(self.players, encoding='utf-8') as f:
            db = json.load(f)
        self.assertEqual(db['Оппонент 1']['hands'], 1)
        self.assertAlmostEqual(db['Оппонент 1']['vpip'], 1.0)

    def test_stats_accumulate_over_hands(self):
        bot = self.bot()
        self.play(bot, [state('preflop', hole=('Ah', 'Kd')), state('flop', hole=('Ah', 'Kd')),
                        state('preflop', hole=('7s', '7d')),      # вторая раздача: фолд
                        state('preflop', hole=('7s', '7d'), live=0),
                        state('preflop', hole=('2c', '2d'))])
        prof = bot.players_db['Оппонент 1']
        self.assertEqual(prof['hands'], 2)
        self.assertAlmostEqual(prof['vpip'], 0.5)

    def test_unfinished_hand_is_saved_on_stop(self):
        bot = self.bot()
        self.play(bot, [state('preflop'), state('flop')])
        self.assertFalse(os.path.exists(self.players), 'раздача ещё идёт')
        bot.summary()
        self.assertTrue(os.path.exists(self.players))

    def test_flag_off_writes_nothing(self):
        bot = self.bot(opponent_memory=False)
        self.play(bot, [state('preflop'), state('flop'),
                        state('preflop', hole=('7s', '7d'))])
        bot.summary()
        self.assertFalse(os.path.exists(self.players))
        self.assertEqual(bot.players_db, {})

    def loose_db(self):
        prof = opponents.blank()
        prof.update({'hands': 30, 'vpip': 0.55, 'pfr': 0.1, 'agg': 0.5})
        return {'Оппонент 1': prof}

    def flop_bluff(self):
        return {'hole': ['7h', '2c'], 'board': ['Ad', 'Ks', '9c'], 'street': 'flop',
                'has_bet': False, 'to_call_bb': None, 'pot_bb': 6.0, 'players': 2,
                'position': 'BTN', 'first_to_act': 'opp'}

    def test_live_stats_cancel_the_bluff(self):
        """Лузовый оппонент из players.json — конт-бет воздухом отменяется."""
        self.assertEqual(self.bot().decide(self.flop_bluff())['action'], 'raise')
        bot = self.bot(db=self.loose_db())
        decision = bot.decide(self.flop_bluff())
        self.assertEqual(decision['action'], 'check')
        self.assertIn('оппонент лузовый', decision['reason'])

    def test_flag_off_stops_the_adjustment(self):
        bot = self.bot(db=self.loose_db(), opponent_memory=False)
        self.assertIsNone(bot.opponent_profile())
        self.assertEqual(bot.decide(self.flop_bluff())['action'], 'raise')

    def test_start_line_shows_what_the_bot_remembers(self):
        bot = self.bot(db=self.loose_db())
        bot.log_memory()
        with open(bot.log_path, encoding='utf-8') as f:
            log = f.read()
        self.assertIn('память оппонентов: Оппонент 1 — 30 рук, VPIP 55%', log)

    def test_empty_table_writes_no_file(self):
        """Плашки нарисованы, играть некому — players.json бот не трогает."""
        bot = self.bot()
        frames = [state('preflop', seated=2, live=0), state('preflop', hole=('7s', '7d'))]
        self.play(bot, frames)
        self.assertFalse(os.path.exists(self.players))
        self.assertEqual(bot.players_db, {})

    def test_dropped_ghost_is_logged(self):
        bot = self.bot(db={'Оппонент 1': dict(opponents.blank(opponents.SEAT_NOTE),
                                              hands=5)})
        bot.save_profiles({1: {'vpip': False, 'bets': 0, 'passive': 0}})
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('«Оппонент 1» — пустое место, запись стёрта', f.read())
        self.assertNotIn('Оппонент 1', bot.players_db)

    def test_a_broken_frame_does_not_break_the_loop(self):
        bot = self.bot()
        with mock.patch.object(bot.observer, 'observe', side_effect=ValueError('кадр')):
            self.assertIsNone(bot.observe(state('flop')))
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('кадр не учтён', f.read())


class HandBoundaryTest(BotFixture):
    """Своя граница раздачи: пустой стол между раздачами, а не смена карт.

    Признаком новой раздачи была смена карманных карт. Две раздачи подряд с
    одной парой (1 к 1326) шли за одну: raised_preflop оставался взведённым, а
    с ним порог «перед нами ререйз» падал с three_bet_mult до 1.6 открытия — и
    бот перефолдил весь диапазон колла против чужого обычного открытия.
    """

    @staticmethod
    def empty():
        """Кадр между раздачами: карт нет ни у героя, ни на столе."""
        return {'hole': [], 'board': [], 'seats': []}

    def blank_frames(self, bot, n=None):
        n = Bot.HAND_OVER_FRAMES if n is None else n
        for _ in range(n):
            bot.track_hand(self.empty())

    def test_an_empty_table_closes_the_hand(self):
        bot = self.bot()
        bot.raised_preflop, bot.last_hole = True, ['Ah', 'Kd']
        self.blank_frames(bot)
        self.assertFalse(bot.raised_preflop, 'свой рейз живёт одну раздачу')
        self.assertIsNone(bot.last_hole, 'следующие карты — уже новая раздача')

    def test_one_misread_frame_is_not_the_end_of_the_hand(self):
        """Карты не прочитались на паре кадров — раздача не должна закрываться."""
        bot = self.bot()
        bot.raised_preflop, bot.last_hole = True, ['Ah', 'Kd']
        self.blank_frames(bot, Bot.HAND_OVER_FRAMES - 1)
        self.assertTrue(bot.raised_preflop)
        bot.track_hand(state('preflop'))          # карты вернулись — счётчик с нуля
        self.blank_frames(bot, Bot.HAND_OVER_FRAMES - 1)
        self.assertTrue(bot.raised_preflop, 'та же раздача')

    def test_a_board_that_disappeared_closes_the_hand(self):
        """Доска была и пропала — раздачу сдали заново, даже если карты «видны»."""
        bot = self.bot()
        bot.raised_preflop = True
        bot.track_hand(state('river'))
        for _ in range(Bot.HAND_OVER_FRAMES):
            bot.track_hand(state('preflop'))      # карты есть, а доски уже нет
        self.assertFalse(bot.raised_preflop)

    def test_the_same_frame_is_counted_once(self):
        """run и step смотрят на один и тот же кадр — считать его дважды нельзя."""
        bot = self.bot()
        bot.raised_preflop = True
        frame = self.empty()
        for _ in range(Bot.HAND_OVER_FRAMES * 2):
            bot.track_hand(frame)
        self.assertTrue(bot.raised_preflop, 'кадр один, раздача не закрывалась')

    def test_the_same_cards_twice_are_two_hands_in_the_stats(self):
        """Наблюдателю граница нужна ровно так же: иначе две раздачи слипнутся в одну."""
        bot = self.bot()
        self.play(bot, [state('preflop'), state('flop')])
        self.blank_frames(bot)
        self.play(bot, [state('preflop'), state('flop')])
        bot.summary()                             # закрыть последнюю раздачу
        self.assertEqual(bot.players_db['Оппонент 1']['hands'], 2)

    def test_a_broken_close_does_not_break_the_loop(self):
        bot = self.bot()
        with mock.patch.object(bot.observer, 'table_reset', side_effect=ValueError('кадр')):
            self.blank_frames(bot)
        self.assertFalse(bot.raised_preflop, 'состояние раздачи обнулено всё равно')
        with open(bot.log_path, encoding='utf-8') as f:
            self.assertIn('раздача не закрыта', f.read())


if __name__ == '__main__':
    unittest.main(verbosity=2)
