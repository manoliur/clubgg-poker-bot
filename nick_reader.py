#!/usr/bin/env python3
"""Чтение ников игроков с экрана: вырезка зоны плашки -> tesseract -> чистый ник.

Зачем: раньше оппонент опознавался МЕСТОМ по кругу от героя («Оппонент 2»).
Стоит игроку встать — места за ним сдвигаются, и статистика двух разных людей
складывается в один профиль. Ник с экрана такой привязки лишён: он держится за
человека, а не за стул.

Tesseract живёт только на компе с ботом (C:\\Program Files\\Tesseract-OCR), на
сервере и в тестах его нет. Поэтому:

* путь к exe передаётся параметром (config.TESSERACT), None — режим без OCR,
  read_nick честно возвращает None и бот играет по местам, как раньше;
* сам запуск вынесен в run_tesseract() — тесты подменяют её и проверяют разбор
  и чистку результата, не имея tesseract'а;
* любая ошибка запуска (нет файла, таймаут, ненулевой код) — тоже None: чтение
  ника не должно ронять игровой цикл.
"""
import os
import re
import subprocess
import tempfile

import config

try:                                     # на сервере cv2 есть, но пусть модуль
    import cv2                           # импортируется и без него
except ImportError:                      # pragma: no cover
    cv2 = None

OCR_TIMEOUT = 8.0            # секунды на один вызов tesseract
OCR_PSM = '7'                # «одна строка текста» — плашка ника ровно такая
NICK_SCALE = 3               # вырезка мелкая (~26px на 1080), OCR любит покрупнее

# Всё, что не буква (любого алфавита), не цифра, не «_», не «-», «.» и пробел, —
# мусор OCR: рамка плашки, полоса таймера, край флажка. Подчёркивание и точка
# остаются: ники вида «EPT_38» встречаются за столом сплошь и рядом.
JUNK_RE = re.compile(r'[^\w \-.]', re.UNICODE)
EDGE_CHARS = ' _-.'          # с краёв они почти всегда мусор, внутри — нет
MAX_JUNK = 0.34              # больше трети мусорных символов = зону не прочитали
MIN_LEN = 2                  # ник короче двух символов ClubGG не даёт


def clean_nick(text, max_len=None):
    """Сырой вывод OCR -> ник либо None, если это мусор.

    Схлопывает пробелы, выкидывает символы, которых в никах не бывает, и режет
    длину. Возвращает None, если после чистки не осталось ничего осмысленного
    или мусора было слишком много (зона попала не на текст).
    """
    if not text:
        return None
    raw = ' '.join(str(text).split())
    if not raw:
        return None
    junk = sum(1 for ch in raw if JUNK_RE.match(ch))
    if junk / len(raw) > MAX_JUNK:
        return None
    nick = ' '.join(JUNK_RE.sub(' ', raw).split()).strip(EDGE_CHARS)
    nick = nick[:int(max_len or config.NICK_MAX_LEN)].strip(EDGE_CHARS)
    if len(nick) < MIN_LEN or not any(ch.isalnum() for ch in nick):
        return None
    return nick


def crop_nick(img, seat):
    """Вырезка зоны ника места. None — места нет или зона вышла за кадр."""
    zone = config.nick_zone(seat)
    if img is None or zone is None:
        return None
    h, w = img.shape[:2]
    x0, y0, x1, y1 = config.zone_px(zone, w, h)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return img[y0:y1, x0:x1]


def prepare(patch, scale=NICK_SCALE):
    """Вырезка -> картинка, удобная OCR: серая, крупнее и в инверсии.

    Ник написан светло-серым по тёмной плашке, а tesseract обучен на чёрном по
    белому — без инверсии он теряет половину букв.
    """
    if cv2 is None or patch is None:
        return patch
    gray = cv2.cvtColor(patch, cv2.COLOR_BGR2GRAY) if patch.ndim == 3 else patch
    big = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return 255 - big


def tesseract_cmd(tesseract, path, lang=None):
    """Командная строка вызова: вырезка -> stdout, одна строка, eng+rus."""
    return [str(tesseract), str(path), 'stdout',
            '--psm', OCR_PSM, '-l', str(lang or config.TESSERACT_LANG)]


def run_tesseract(path, tesseract, lang=None):
    """Запустить OCR над файлом и вернуть СЫРОЙ текст (или None).

    Отдельная функция — точка подмены: на сервере tesseract'а нет, тесты
    мокают именно её.
    """
    try:
        done = subprocess.run(tesseract_cmd(tesseract, path, lang),
                              capture_output=True, timeout=OCR_TIMEOUT)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    out = done.stdout
    return out.decode('utf-8', 'replace') if isinstance(out, bytes) else out


def read_nick(img, seat, tesseract=None, lang=None, max_len=None):
    """Ник игрока на месте seat (0 — герой, 1..5 по кругу) либо None.

    tesseract — путь к exe; None означает «OCR выключен», и функция сразу
    возвращает None, ничего не запуская.
    """
    if not tesseract:
        return None
    patch = crop_nick(img, seat)
    if patch is None or cv2 is None or getattr(patch, 'size', 0) == 0:
        return None
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(prefix='clubgg_nick_', suffix='.png')
        os.close(fd)
        if not cv2.imwrite(tmp, prepare(patch)):
            return None
        text = run_tesseract(tmp, tesseract, lang)
    except OSError:
        return None
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return clean_nick(text, max_len)


def read_nicks(img, seats, tesseract=None, lang=None):
    """Ники занятых мест: {номер по кругу от героя (1..): ник}.

    seats — state['seats'] из table_state.read_state, то есть плашки ПО КРУГУ,
    начиная с героя. Ключ — тот же номер, которым HandObserver считает
    статистику, а вот ЗОНА ника берётся по фактическому положению плашки на
    экране (config.seat_at): клиент сажает игроков по-разному, и «второй по
    кругу» вполне может сидеть справа внизу.

    Пустые места пропускаются — OCR по сукну лишний вызов подпроцесса.
    """
    if not tesseract or img is None or not seats:
        return {}
    h, w = img.shape[:2]
    out, i = {}, 0
    for panel in seats:
        if panel.get('hero'):
            continue
        i += 1
        where = config.seat_at(panel.get('x'), panel.get('y'), w, h)
        if where is None or where == 0:
            continue                     # плашка не легла ни на одно место
        nick = read_nick(img, where, tesseract=tesseract, lang=lang)
        if nick:
            out[i] = nick
    return out


def main(argv=None):
    """CLI: показать ники, прочитанные на скриншоте (python nick_reader.py shot.png)."""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print('использование: python nick_reader.py <screenshot.png> [путь-к-tesseract]')
        return 2
    if cv2 is None:
        print('нет cv2')
        return 2
    img = cv2.imread(argv[0])
    if img is None:
        print('ERR: не читается', argv[0])
        return 2
    exe = argv[1] if len(argv) > 1 else config.TESSERACT
    for seat in range(len(config.NICK_ZONES)):
        who = 'герой' if seat == 0 else f'место {seat}'
        print(f'{who}: {read_nick(img, seat, tesseract=exe) or "-"}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
