#!/usr/bin/env python3
"""Тесты подробного лога решений: строка контекста и новые поля hand_history."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import main as main_mod                      # noqa: E402
import strategy                              # noqa: E402
from main import Bot, pretty_cards           # noqa: E402


class StubScreen:
    def __init__(self):
        self.taps = []

    def grab(self):
        return None

    def tap(self, x, y):
        self.taps.append((x, y))


def state(**kw):
    base = {'my_turn': True, 'in_hand': True,
            'hole': ['As', 'Qh'], 'board': ['8s', '8d', '4s', '6h', 'Kd'],
            'street': 'river', 'players': 2, 'players_seated': 2,
            'position': 'BTN', 'dealer': 'me', 'first_to_act': 'opp',
            'pot_bb': 24.0, 'to_call_bb': 8.0, 'has_bet': True,
            'cards_detail': {'hole': [{'card': 'As', 'rank_score': 1.0},
                                      {'card': 'Qh', 'rank_score': 1.0}]},
            'raise_presets': [{'i': 0, 'x': 880, 'y': 2315, 'enabled': True}],
            'chevron': None, 'presets_collapsed': False, 'showdown': False,
            'seats': [{'x': 1, 'y': 1, 'hero': True, 'in_hand': True},
                      {'x': 2, 'y': 2, 'hero': False, 'in_hand': True}],
            'taps': {'fold': (185, 2315), 'call': (535, 2315), 'raise': (880, 2315)},
            'call_fp': None}
    base.update(kw)
    return base


class DecisionLogTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_log_')
        sleep = mock.patch.object(main_mod.time, 'sleep')   # без настоящих пауз
        sleep.start()
        self.addCleanup(sleep.stop)

    def bot(self, **cfg):
        self.screen = StubScreen()
        return Bot(self.screen, stack_bb=61.0,
                   players_path=os.path.join(self.tmp, 'players.json'),
                   log_path=os.path.join(self.tmp, 'bot.log'),
                   history_path=os.path.join(self.tmp, 'h.jsonl'), cfg=cfg)

    def play(self, bot, **kw):
        entry = bot.step(state=state(**kw))
        with open(bot.log_path, encoding='utf-8') as f:
            return entry, f.read().strip().splitlines()[-1]

    def test_pretty_cards(self):
        self.assertEqual(pretty_cards(['As', 'Qh']), 'A♠Q♥')
        self.assertEqual(pretty_cards(['8s', '8d', '4s', '6h', 'Kd']), '8♠8♦4♠6♥K♦')
        self.assertEqual(pretty_cards([None, '2c']), '??2♣')

    def test_the_whole_context_is_in_one_line(self):
        entry, line = self.play(self.bot())
        self.assertEqual(len(line.splitlines()), 1)
        for part in ('[#1 river]', 'A♠Q♥', 'доска 8♠8♦4♠6♥K♦', 'банк 24.0ББ',
                     'колл 8.0ББ (25%)', 'стек 61.0ББ', 'сделано:', 'пара 8',
                     f"решение: {entry['action']}", 'причина:'):
            with self.subTest(part=part):
                self.assertIn(part, line)

    def test_the_reason_is_kept_as_it_was(self):
        entry, line = self.play(self.bot())
        self.assertTrue(line.endswith('причина: ' + entry['reason']))

    def test_made_class_is_named(self):
        _, line = self.play(self.bot())
        self.assertIn('(weak)', line, 'категория руки видна прямо в строке')

    def test_preflop_line_has_no_board(self):
        _, line = self.play(self.bot(), board=[], street='preflop',
                            has_bet=False, to_call_bb=None, pot_bb=3.0)
        self.assertNotIn('доска', line)
        self.assertIn('ставки нет', line)
        self.assertIn('сделано: AQo (префлоп)', line)

    def test_unknown_numbers_do_not_break_the_line(self):
        _, line = self.play(self.bot(), pot_bb=None, to_call_bb=None)
        self.assertIn('банк ?', line)
        self.assertIn('колл ?ББ', line)

    def test_history_keeps_the_old_fields(self):
        entry, _ = self.play(self.bot())
        for key in ('ts', 'hand_id', 'street', 'hole', 'board', 'players',
                    'players_seated', 'position', 'dealer', 'first_to_act',
                    'pot_bb', 'to_call_bb', 'has_bet', 'action', 'amount_bb',
                    'reason', 'tap', 'dry_run'):
            with self.subTest(key=key):
                self.assertIn(key, entry)

    def test_history_gets_the_new_fields(self):
        entry, _ = self.play(self.bot(style='tighty', blocker_bluff=True))
        self.assertEqual(entry['made'], 'weak')
        self.assertEqual(entry['pot_odds_pct'], 25)
        self.assertEqual(entry['equity_pct'], 30)
        self.assertEqual(entry['stack_bb'], 61.0)
        self.assertEqual(entry['style'], 'tighty')
        self.assertIn('blocker_bluff', entry['flags'])
        self.assertIn('human_timing', entry['flags'])
        self.assertNotIn('bet_sizing', entry['flags'], 'выключенные флаги не пишем')

    def test_new_fields_land_in_the_file(self):
        bot = self.bot()
        self.play(bot)
        with open(bot.history_path, encoding='utf-8') as f:
            saved = json.loads(f.readlines()[-1])
        for key in ('made', 'made_note', 'pot_odds_pct', 'equity_pct', 'style',
                    'flags', 'stack_bb', 'think_s'):
            with self.subTest(key=key):
                self.assertIn(key, saved)

    def test_equity_of_a_draw_is_counted_by_outs(self):
        entry, _ = self.play(self.bot(), board=['8s', '2d', '4s'], street='flop',
                             hole=['As', 'Qs'], pot_bb=6.0, to_call_bb=2.0)
        self.assertEqual(entry['made'], 'draw')
        self.assertGreater(entry['equity_pct'], 30)

    def test_hand_note_is_safe_on_unreadable_cards(self):
        note = strategy.hand_note([None, 'Qh'], ['8s'], 'flop', 2.0, 6.0)
        self.assertEqual(note['made'], 'unknown')
        self.assertEqual(note['pot_odds'], 0.25)

    def test_size_of_the_bet_is_shown(self):
        _, line = self.play(self.bot(), hole=['8c', '8h'], has_bet=False,
                            to_call_bb=None)
        self.assertIn('решение: raise', line)
        self.assertRegex(line, r'решение: raise \d')


if __name__ == '__main__':
    unittest.main(verbosity=2)
