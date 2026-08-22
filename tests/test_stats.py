#!/usr/bin/env python3
"""Тесты статистики: как из hand_history.jsonl получаются победы, поражения и фишки.

Синтетика: сами пишем записи с нужными стеками и временем, потом сверяем, что
stats.py посчитал ровно то, что человек увидит на вкладке «Статистика».
"""
import datetime
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import stats                               # noqa: E402

TS = '2026-08-22 12:00:00'


def rec(hand_id, stack_bb, ts=TS, **kw):
    """Одна строка hand_history.jsonl — решение бота в раздаче."""
    out = {'hand_id': hand_id, 'stack_bb': stack_bb, 'ts': ts,
           'street': 'preflop', 'action': 'call'}
    out.update(kw)
    return out


def to_hands(records):
    """Записи -> раздачи с результатами (то же, что load_hands, но без файла)."""
    return stats.fill_results(stats.group_hands(records))


def hands_from(*stacks):
    """Раздачи со стеками входа: 50, 55, 52 -> три раздачи подряд."""
    return to_hands([rec(i + 1, s) for i, s in enumerate(stacks)])


class DeltaTest(unittest.TestCase):
    """Результат раздачи — это разница стеков между началом её и следующей."""

    def test_win_loss_and_push(self):
        hands = hands_from(50.0, 55.0, 52.0, 52.0, 60.0)
        self.assertEqual([h['result'] for h in hands],
                         ['win', 'loss', 'push', 'win', 'open'])
        self.assertEqual([h['delta_bb'] for h in hands[:4]], [5.0, -3.0, 0.0, 8.0])

    def test_the_last_hand_has_no_result_yet(self):
        """У последней раздачи нет «следующего» стека — она ещё не сыграна до конца."""
        hands = hands_from(50.0, 55.0)
        self.assertEqual(hands[-1]['result'], 'open')
        self.assertIsNone(hands[-1]['delta_bb'])
        self.assertEqual(len(stats.counted(hands)), 1)

    def test_blinds_are_already_inside_the_delta(self):
        """Сбросили в блайнде: минус блайнд — это поражение, отдельно его не считаем."""
        hands = hands_from(50.0, 49.0, 49.0)
        self.assertEqual(hands[0]['result'], 'loss')
        self.assertEqual(hands[0]['delta_bb'], -1.0)

    def test_a_rebuy_is_not_a_win(self):
        """Стек вырос вшестеро — это докупка фишек, а не выигранный банк."""
        hands = hands_from(10.0, 100.0, 100.0)
        self.assertEqual(hands[0]['result'], 'skip')
        self.assertIsNone(hands[0]['delta_bb'])
        self.assertEqual(stats.aggregate(hands)['hands'], 1, 'докупка в счёт не идёт')

    def test_a_zero_stack_means_the_bot_could_not_read_it(self):
        """Ноль фишек — это не проигрыш всего стека, а нечитаемый экран.

        За столом с нулём не сидят: раз пришёл ноль, стек не распознался. Такую
        раздачу оставляем без результата, а не записываем выдуманное поражение.
        """
        hands = hands_from(50.0, 0.0, 48.0)
        self.assertEqual([h['result'] for h in hands], ['open', 'open', 'open'])
        self.assertEqual(stats.aggregate(hands)['hands'], 0)

    def test_a_hand_is_a_run_of_records_with_one_id(self):
        """Бот перезапустился, нумерация пошла заново — это разные раздачи."""
        hands = to_hands([
            rec(1, 50.0), rec(1, 50.0, action='raise'), rec(2, 55.0), rec(1, 53.0)])
        self.assertEqual(len(hands), 3)
        self.assertEqual([h['result'] for h in hands], ['win', 'loss', 'open'])

    def test_records_without_a_stack_are_left_open(self):
        hands = to_hands([rec(1, None), rec(2, 50.0)])
        self.assertEqual(hands[0]['result'], 'open')


class ChipsTest(unittest.TestCase):
    """Человек видит фишки, бот считает в ББ."""

    def test_bb_turn_into_chips(self):
        self.assertEqual(stats.to_chips(50.0, 20), 1000)
        self.assertEqual(stats.to_chips(-2.5, 20), -50)
        self.assertIsNone(stats.to_chips(None, 20))

    def test_bad_bb_value_falls_back_to_the_default(self):
        for bad in (None, 'много', 0, -5, 10 ** 9):
            self.assertEqual(stats.clean_bb_value(bad), stats.BB_VALUE_DEFAULT)
        self.assertEqual(stats.clean_bb_value('25'), 25.0)

    def test_profit_is_reported_in_chips(self):
        hands = hands_from(50.0, 55.0, 53.0, 53.0)
        row = stats.aggregate(hands, bb_value=20)
        self.assertEqual(row['pl_bb'], 3.0)
        self.assertEqual(row['pl_chips'], 60)
        self.assertEqual(row['best_chips'], 100)
        self.assertEqual(row['worst_chips'], -40)


class AggregateTest(unittest.TestCase):
    def test_counts_and_percentages(self):
        hands = hands_from(50.0, 55.0, 53.0, 58.0, 58.0)
        row = stats.aggregate(hands)
        self.assertEqual((row['hands'], row['wins'], row['losses']), (4, 2, 1))
        self.assertEqual(row['pushes'], 1)
        self.assertEqual((row['win_pct'], row['loss_pct']), (50, 25))

    def test_empty_history_is_zeroes_not_a_crash(self):
        row = stats.aggregate([])
        self.assertEqual((row['hands'], row['pl_chips'], row['win_pct']), (0, 0, 0))
        self.assertEqual(row['streak'], {'kind': None, 'count': 0})

    def test_streak_counts_the_tail(self):
        """Три поражения подряд в конце — серия 3 (последняя раздача ещё открыта)."""
        hands = hands_from(50.0, 48.0, 46.0, 44.0)
        self.assertEqual(stats.streak(hands), {'kind': 'loss', 'count': 3})

    def test_a_streak_stops_at_the_first_other_result(self):
        hands = hands_from(50.0, 48.0, 53.0, 51.0, 49.0)
        self.assertEqual(stats.streak(hands), {'kind': 'loss', 'count': 2})

    def test_a_hand_without_a_bet_breaks_the_streak(self):
        """Раздача «в ноль» — не победа, серия побед на ней обрывается."""
        hands = hands_from(50.0, 55.0, 60.0, 60.0, 60.0)
        self.assertEqual(stats.streak(hands), {'kind': None, 'count': 0})


class PeriodTest(unittest.TestCase):
    """«Сегодня», «Неделя», «За игру» — что попадает в каждый период."""

    def hands(self):
        def at(days_ago, hour=12):
            t = datetime.datetime(2026, 8, 22, hour) - datetime.timedelta(days=days_ago)
            return t.strftime('%Y-%m-%d %H:%M:%S')
        return to_hands([
            rec(1, 50.0, ts=at(40)),      # +5, месяц назад
            rec(2, 55.0, ts=at(20)),      # +5, в этом месяце, но не на неделе
            rec(3, 60.0, ts=at(3)),       # -2, на этой неделе
            rec(4, 58.0, ts=at(0, 9)),    # +4, сегодня утром
            rec(5, 62.0, ts=at(0, 18)),   # последняя — без результата
        ])

    def now(self):
        return datetime.datetime(2026, 8, 22, 20, 0, 0)

    def since(self, key, session_start=None):
        return stats.period_since(key, self.now(), session_start)

    def test_each_period_takes_its_own_hands(self):
        hands = self.hands()
        got = {k: stats.aggregate(hands, since=self.since(k))['hands']
               for k in ('today', 'week', 'month', 'all')}
        self.assertEqual(got, {'today': 1, 'week': 2, 'month': 3, 'all': 4})

    def test_all_time_has_no_boundary(self):
        self.assertIsNone(self.since('all'))

    def test_today_starts_at_midnight(self):
        self.assertEqual(self.since('today'), datetime.datetime(2026, 8, 22, 0, 0, 0))

    def test_session_starts_when_the_bot_was_started(self):
        start = self.now() - datetime.timedelta(hours=12)
        self.assertEqual(self.since('session', start), start)
        self.assertEqual(stats.aggregate(self.hands(), since=start)['hands'], 1)

    def test_without_a_start_the_session_is_unknown_not_zero(self):
        """Панель не запускала бота — честное «—», а не «0 раздач за игру»."""
        out = stats.summary(self.hands(), now=self.now(), session_start=None)
        session = next(p for p in out['periods'] if p['key'] == 'session')
        self.assertTrue(session['unknown'])
        self.assertEqual(session['hands'], 0)


class SummaryTest(unittest.TestCase):
    def test_summary_has_everything_the_tab_shows(self):
        out = stats.summary(hands_from(50.0, 55.0, 53.0), bb_value=20)
        self.assertEqual([p['key'] for p in out['periods']],
                         ['session', 'today', 'week', 'month', 'all'])
        self.assertTrue(all(p['title'] for p in out['periods']))
        self.assertEqual(out['bb_value'], 20.0)
        self.assertEqual((out['hands_total'], out['hands_counted']), (3, 2))

    def test_chart_points_are_in_chips(self):
        out = stats.summary(hands_from(50.0, 55.0, 53.0), bb_value=20)
        self.assertEqual([p['stack'] for p in out['chart']], [1000, 1100, 1060])
        self.assertEqual([p['result'] for p in out['chart']], ['win', 'loss', 'open'])

    def test_the_chart_keeps_only_the_last_hands(self):
        hands = hands_from(*[50.0 + i for i in range(150)])
        self.assertEqual(len(stats.chart_points(hands, limit=100)), 100)
        self.assertEqual(stats.chart_points(hands, limit=100)[0]['stack_bb'], 100.0)

    def test_live_hand_is_the_last_decision_in_chips(self):
        hands = to_hands([
            rec(1, 50.0), rec(2, 48.0, action='raise', amount_bb=6.0, pot_bb=9.0,
                hole=['As', 'Kd'], board=['2h', '7c', 'Ts'], street='flop',
                reason='топ-пара, ставим на велью')])
        live = stats.live_hand(hands, bb_value=20)
        self.assertEqual(live['action'], 'raise')
        self.assertEqual(live['amount_chips'], 120)
        self.assertEqual(live['pot_chips'], 180)
        self.assertEqual(live['stack_chips'], 960)
        self.assertEqual(live['hole'], ['As', 'Kd'])
        self.assertIn('велью', live['reason'])

    def test_no_hands_no_live_block(self):
        self.assertIsNone(stats.live_hand([]))


class FileTest(unittest.TestCase):
    """Чтение файла: битые строки, отсутствие файла и кэш по mtime."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='clubgg_stats_')
        self.path = os.path.join(self.tmp, 'hand_history.jsonl')

    def write(self, *records, mode='w'):
        with open(self.path, mode, encoding='utf-8') as f:
            for r in records:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')

    def test_a_missing_file_is_an_empty_history(self):
        self.assertEqual(stats.load_hands(self.path), [])
        self.assertEqual(stats.History(self.path).summary()['hands_total'], 0)

    def test_a_broken_line_does_not_lose_the_rest(self):
        with open(self.path, 'w', encoding='utf-8') as f:
            f.write(json.dumps(rec(1, 50.0)) + '\n')
            f.write('{это не json\n\n')
            f.write(json.dumps(rec(2, 55.0)) + '\n')
        hands = stats.load_hands(self.path)
        self.assertEqual(len(hands), 2)
        self.assertEqual(hands[0]['delta_bb'], 5.0)

    def test_the_file_is_read_again_only_when_it_changed(self):
        self.write(rec(1, 50.0), rec(2, 55.0))
        hist = stats.History(self.path)
        self.assertEqual(len(hist.hands()), 2)
        first = hist.hands()
        self.assertIs(hist.hands(), first, 'файл не менялся — разбор не повторяем')
        self.write(rec(3, 53.0), mode='a')
        os.utime(self.path, (0, os.stat(self.path).st_mtime + 10))
        self.assertEqual(len(hist.hands()), 3, 'бот дописал раздачу — перечитали')

    def test_the_history_survives_a_file_that_appears_later(self):
        hist = stats.History(self.path)
        self.assertEqual(hist.hands(), [])
        self.write(rec(1, 50.0), rec(2, 55.0))
        self.assertEqual(len(hist.hands()), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
