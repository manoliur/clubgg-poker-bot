#!/usr/bin/env python3
"""Веб-панель управления ботами ClubGG. Запуск: python panel.py [--port 8090]

Открыть http://127.0.0.1:8090 — список телефонов (adb), настройки
(чарт, агрессия, защита), кнопки Старт/Стоп, живой лог.

Только стандартная библиотека: http.server + subprocess + json.
"""
import argparse
import json
import os
import signal
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategy                    # noqa: E402  (свой модуль, сторонних библиотек нет)

BASE = os.path.dirname(os.path.abspath(__file__))
DEVICES_FILE = os.path.join(BASE, 'devices.json')
LOGS_DIR = os.path.join(BASE, 'logs')
PYTHON = sys.executable
ADB = os.environ.get('CLUBGG_ADB', r'E:/down/platform-tools/platform-tools/adb.exe')

# имя -> (serial по умолчанию, описание)
DEFAULT_DEVICES = [
    {'name': 'Телефон 1', 'serial': '1cf5db29', 'chart': 'charts/6max_standard.json',
     'aggression': 1.0, 'defense': 1.0, 'stack': 69.6, 'live_stack': True,
     'style': strategy.DEFAULT_STYLE},
]

# Переключатели «вкл/выкл»: ключ -> подпись в панели.
FLAGS = [
    ('bet_sizing', 'Размеры ставок по силе/улице'),
    ('multiway_tight', 'Мультипот — тайтовее'),
    ('short_stack_mode', 'Короткий стек — push/fold'),
    ('blocker_bluff', 'Блеф с блокерами (ривер)'),
    ('position_aware', 'Учитывать позицию (OOP)'),
]

# Переключатели самого БОТА (в настройки стратегии не входят, живут только в
# записи устройства). live_stack: бот читает свой стек с экрана раз в раздачу и
# сам обновляет поле stack; выключено — играет по числу из панели, как раньше.
BOT_FLAGS = [
    ('live_stack', 'Живой стек — читать с экрана'),
]
BOT_FLAG_KEYS = [f[0] for f in BOT_FLAGS]

# Ползунки порогов: ключ, подпись, min, max, шаг.
SLIDERS = [
    ('cbet_pot', 'Ставка велью', 0.3, 1.0, 0.05),
    ('nuts_pot', 'Ставка натсом', 0.4, 1.0, 0.05),
    ('medium_max_price', 'Порог колла средней', 0.1, 0.7, 0.02),
    ('draw_min_equity', 'Эквити дро', 0.20, 0.50, 0.01),
    ('short_stack_bb', 'Короткий стек, ББ', 10, 60, 1),
    ('bet_nuts', 'Размер: натс', 0.4, 1.0, 0.05),
    ('bet_strong', 'Размер: сильная', 0.3, 1.0, 0.05),
    ('bet_medium', 'Размер: средняя', 0.2, 0.9, 0.05),
    ('bet_draw', 'Размер: дро', 0.2, 0.9, 0.05),
]
SLIDER_KEYS = [s[0] for s in SLIDERS]
FLAG_KEYS = [f[0] for f in FLAGS]

# ---------------------------------------------------------------------------
# процессы ботов
# ---------------------------------------------------------------------------
class BotManager:
    _mtime = None             # mtime devices.json на момент последнего чтения/записи

    def __init__(self):
        self.procs = {}       # serial -> Popen
        self.log_files = {}   # serial -> file handle
        self.load_devices()
        self.resume()

    def pid_path(self, serial):
        return os.path.join(LOGS_DIR, f'{serial}.pid')

    def resume(self):
        """Восстановить привязку к уже запущенным ботам (панель перезапускалась).
        Пишется pid-файл при старте бота; если процесс жив — считаем его своим."""
        os.makedirs(LOGS_DIR, exist_ok=True)
        for d in self.devices:
            serial = d['serial']
            path = self.pid_path(serial)
            try:
                with open(path) as f:
                    pid = int(f.read().strip())
            except (OSError, ValueError):
                continue
            if self._pid_alive(pid):
                self.procs[serial] = pid
                self.log_files[serial] = open(os.path.join(LOGS_DIR, f'{serial}.log'),
                                              'a', encoding='utf-8')
                print(f'панель: бот {serial} (pid {pid}) уже запущен — подхвачен')

    @staticmethod
    def _pid_alive(pid):
        """Жив ли процесс (Windows: os.kill(pid,0) не поддерживается — WinError 87)."""
        if sys.platform == 'win32':
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,
                                                   False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def devices_path(self):
        return DEVICES_FILE

    def load_devices(self):
        if os.path.exists(DEVICES_FILE):
            try:
                with open(DEVICES_FILE, encoding='utf-8') as f:
                    self.devices = json.load(f)
                self._mtime = self._file_mtime()
                return
            except (OSError, ValueError):
                pass
        self.devices = [dict(d) for d in DEFAULT_DEVICES]   # копия: записи правятся на месте
        self.save_devices()

    @staticmethod
    def _file_mtime():
        try:
            return os.path.getmtime(DEVICES_FILE)
        except OSError:
            return None

    def reload_if_changed(self):
        """Перечитать devices.json, если его правил кто-то ещё.

        Этот «кто-то» — бот: он пишет в свою запись прочитанный с экрана стек.
        Без перечитывания панель показывала бы значение, снятое при её запуске, а
        первое же «Применить» вернуло бы в файл устаревшую константу.
        """
        mtime = self._file_mtime()
        if mtime is None or mtime == getattr(self, '_mtime', None):
            return False
        self.load_devices()
        return True

    def save_devices(self):
        with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.devices, f, ensure_ascii=False, indent=2)
        self._mtime = self._file_mtime()

    def device(self, serial):
        for d in self.devices:
            if d['serial'] == serial:
                return d
        return None

    def adb_online(self):
        """Серийники, которые видит adb сейчас."""
        try:
            out = subprocess.run([ADB, 'devices'], capture_output=True, text=True,
                                 timeout=15).stdout
            return [line.split()[0] for line in out.splitlines()[1:]
                    if line.strip() and 'device' in line and 'offline' not in line]
        except Exception:
            return []

    def running(self, serial):
        p = self.procs.get(serial)
        if isinstance(p, int):                 # подхваченный по pid
            return self._pid_alive(p)
        return p is not None and p.poll() is None

    def status(self, serial):
        self.reload_if_changed()      # бот мог обновить свой стек в файле
        d = self.device(serial) or {}
        online = serial in self.adb_online()
        run = self.running(serial)
        tail = self.tail(serial, 6)
        # что реально применит бот: стиль + отдельные ключи записи (без ползунков —
        # их множители применяются поверх и в поля панели попадать не должны)
        settings = strategy.device_settings(strategy.DEFAULT_SETTINGS, d, sliders=False)
        flags = {k: bool(settings[k]) for k in FLAG_KEYS}
        # переключатели бота живут прямо в записи устройства (в стратегию не идут)
        flags.update({k: bool(d.get(k, True)) for k in BOT_FLAG_KEYS})
        return {'serial': serial, 'name': d.get('name', serial),
                'online': online, 'running': run,
                'chart': d.get('chart'), 'aggression': d.get('aggression', 1.0),
                'defense': d.get('defense', 1.0), 'stack': d.get('stack', 69.6),
                # стек прочитан ботом с экрана, а не задан человеком
                'stack_auto': bool(d.get('stack_auto')),
                'style': str(d.get('style') or strategy.DEFAULT_STYLE).lower(),
                'flags': flags,
                'sliders': {k: settings[k] for k in SLIDER_KEYS},
                'log': tail}

    @staticmethod
    def styles():
        """Пресеты для выпадашки: подпись + пороги (по ним панель освежает ползунки)."""
        return {key: {'title': strategy.STYLE_TITLES.get(key, key),
                      'sliders': {k: strategy.style_settings(key)[k] for k in SLIDER_KEYS}}
                for key in strategy.STYLE_PRESETS}

    def tail(self, serial, n=6):
        path = os.path.join(LOGS_DIR, f'{serial}.log')
        try:
            with open(path, encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
            return ''.join(lines[-n:])
        except OSError:
            return ''

    def start(self, serial):
        if self.running(serial):
            return False, 'уже запущен'
        d = self.device(serial)
        if d is None:
            return False, 'нет такого устройства'
        os.makedirs(LOGS_DIR, exist_ok=True)
        log_path = os.path.join(LOGS_DIR, f'{serial}.log')
        cmd = [PYTHON, os.path.join(BASE, 'main.py'), '--serial', serial]
        if d.get('chart'):
            cmd += ['--chart', os.path.join(BASE, d['chart'])]
        cmd += ['--aggression', str(d.get('aggression', 1.0))]
        cmd += ['--defense', str(d.get('defense', 1.0))]
        cmd += ['--stack', str(d.get('stack', 69.6))]
        cmd += ['--style', str(d.get('style') or strategy.DEFAULT_STYLE)]
        if not d.get('live_stack', True):
            cmd += ['--no-live-stack']
        if d.get('name'):
            cmd += ['--name', d['name']]
        f = open(log_path, 'a', encoding='utf-8')
        f.write(f'\n[{time.strftime("%Y-%m-%d %H:%M:%S")}] === СТАРТ ===\n')
        f.flush()
        try:
            p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT,
                                 cwd=BASE, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            f.close()
            return False, f'не запустился: {e}'
        self.procs[serial] = p
        self.log_files[serial] = f
        with open(self.pid_path(serial), 'w') as pf:
            pf.write(str(p.pid))
        return True, 'запущен'

    def stop(self, serial):
        p = self.procs.get(serial)
        if p is None or not self.running(serial):
            return False, 'не запущен'
        try:
            if isinstance(p, int):
                os.kill(p, signal.SIGTERM)       # подхваченный по pid
            else:
                p.terminate()
                p.wait(timeout=5)
        except Exception:
            pass
        self.procs.pop(serial, None)
        f = self.log_files.pop(serial, None)
        if f:
            f.close()
        try:
            os.remove(self.pid_path(serial))
        except OSError:
            pass
        return True, 'остановлен'

    def save_config(self, serial, data):
        """Сохранить настройки устройства. Бот подхватит их со следующего решения.

        Кривые значения (чужой стиль, буквы вместо числа) молча пропускаем:
        панель не должна писать в devices.json то, на чём бот споткнётся.
        """
        self.reload_if_changed()          # бот мог обновить стек, пока правили форму
        d = self.device(serial)
        if d is None:
            d = {'serial': serial, 'name': serial}
            self.devices.append(d)
        for key in ('name', 'chart', 'aggression', 'defense', 'stack'):
            if key in data:
                d[key] = data[key]
        if 'stack' in data:
            # стек ввёл человек — с этого момента он не «авто», и бот считает от
            # него границы здравого чтения (пока сам не прочитает новое значение)
            d['stack_auto'] = False
        style = str(data.get('style', '')).lower().strip()
        if style in strategy.STYLE_PRESETS:
            d['style'] = style
        for key in FLAG_KEYS + BOT_FLAG_KEYS:
            if key in data:
                d[key] = bool(data[key])
        for key in SLIDER_KEYS:
            if key in data:
                try:
                    d[key] = float(data[key])
                except (TypeError, ValueError):
                    pass
        self.save_devices()
        return d

    def charts(self):
        charts_dir = os.path.join(BASE, 'charts')
        try:
            return sorted(f for f in os.listdir(charts_dir) if f.endswith('.json'))
        except OSError:
            return []


MANAGER = BotManager()


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
PAGE = """<!DOCTYPE html>
<html lang="ru"><head><meta charset="utf-8">
<title>ClubGG панель</title>
<style>
 body{font-family:Segoe UI,Arial;margin:0;background:#14181c;color:#e8e8e8}
 .wrap{max-width:1000px;margin:0 auto;padding:16px}
 h1{font-size:20px;color:#ffd75e}
 .dev{background:#1e242b;border:1px solid #2c3644;border-radius:10px;padding:14px;margin:12px 0}
 .dev h2{font-size:16px;margin:0 0 8px;display:flex;gap:10px;align-items:center}
 .badge{padding:2px 8px;border-radius:10px;font-size:12px}
 .on{background:#1f6f3a;color:#c8ffd8}.off{background:#6f2a2a;color:#ffd0d0}
 .row{display:flex;gap:12px;flex-wrap:wrap;margin:8px 0;align-items:center}
 label{font-size:13px;color:#9fb0c3}
 select,input[type=number]{background:#0f1419;color:#e8e8e8;border:1px solid #3a4a5c;border-radius:6px;padding:4px 6px}
 input[type=range]{width:140px}
 button{background:#2c6fbb;color:#fff;border:0;border-radius:6px;padding:7px 14px;cursor:pointer;font-size:13px}
 button.stop{background:#b3442c} button:disabled{opacity:.4;cursor:default}
 pre.log{background:#0c1014;border:1px solid #2c3644;border-radius:8px;padding:8px;
  font-size:12px;max-height:180px;overflow:auto;white-space:pre-wrap;color:#9fe8a0}
 .hint{font-size:12px;color:#6f8299;margin-top:6px}
 .hint.auto{margin:0;color:#9fe8a0}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:6px 14px}
 .cell{display:flex;gap:8px;align-items:center}
 .cell label{flex:1 0 150px}
 .val{font-size:12px;color:#ffd75e;min-width:34px;text-align:right}
 .flags label{color:#e8e8e8;display:flex;gap:6px;align-items:center;cursor:pointer}
 .dirty{color:#ffd75e}
</style></head><body><div class="wrap">
<h1>🎰 ClubGG — панель управления ботами</h1>
<div id="list"></div>
<div class="hint">Автообновление каждые 3с. «Применить» действует сразу — бот
перечитывает настройки перед каждым решением, перезапуск не нужен.</div>
</div>
<script>
let editing = null;          // пока правим настройки — не перерисовываем список
async function api(path, method, body){
  const r = await fetch(path, {method, headers:{'Content-Type':'application/json'},
    body: body?JSON.stringify(body):undefined});
  return r.json();
}
function esc(s){ return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
async function refresh(){
  if (editing) return;       // иначе автообновление сотрёт несохранённые правки
  const data = await api('/api/devices');
  const el = document.getElementById('list');
  el.innerHTML = (data.devices||[]).map(d => `
   <div class="dev" data-serial="${d.serial}"><h2>${esc(d.name)||d.serial}
     <span class="badge ${d.online?'on':'off'}">${d.online?'в сети':'нет связи'}</span>
     <span class="badge ${d.running?'on':'off'}">${d.running?'ИГРАЕТ':'остановлен'}</span></h2>
   <div class="row"><label>Чарт</label>
     <select data-k="chart">${(data.charts||[]).map(c =>
       `<option ${d.chart==='charts/'+c?'selected':''}>charts/${c}</option>`).join('')}</select>
     <label>Стиль</label>
     <select data-k="style">${Object.entries(data.styles||{}).map(([k,s]) =>
       `<option value="${k}" ${d.style===k?'selected':''}>${s.title}</option>`).join('')}</select>
     <label>Стек, ББ</label><input type="number" data-k="stack" step="0.1" value="${d.stack}" style="width:70px">
     <span class="hint auto">${d.stack_auto?`стек: ${d.stack} ББ (авто)`:'стек задан вручную'}</span>
   </div>
   <div class="row">
     <label>Агрессия</label><input type="range" data-k="aggression" min="0.5" max="2" step="0.1" value="${d.aggression}">
     <span class="val">${d.aggression}</span>
     <label>Защита</label><input type="range" data-k="defense" min="0.5" max="2" step="0.1" value="${d.defense}">
     <span class="val">${d.defense}</span>
   </div>
   <div class="row flags grid">${(data.flags||[]).map(([k,title]) =>
     `<label><input type="checkbox" data-k="${k}" ${d.flags[k]?'checked':''}>${title}</label>`).join('')}</div>
   <div class="grid">${(data.sliders||[]).map(([k,title,lo,hi,step]) =>
     `<div class="cell"><label>${title}</label>
       <input type="range" data-k="${k}" min="${lo}" max="${hi}" step="${step}" value="${d.sliders[k]}">
       <span class="val">${d.sliders[k]}</span></div>`).join('')}</div>
   <div class="row">
     <button class="go" data-a="start" ${d.running?'disabled':''}>▶ Старт</button>
     <button class="stop" data-a="stop" ${d.running?'':'disabled'}>■ Стоп</button>
     <button data-a="save">💾 Применить</button>
     <span class="hint state">применяется сразу, без перезапуска</span>
   </div>
   <pre class="log">${esc(d.log)}</pre>
  </div>`).join('');
  document.querySelectorAll('.dev').forEach(dev => setup(dev, data));
}
function setup(dev, data){
  const serial = dev.dataset.serial;
  const state = dev.querySelector('.state');
  const touch = () => { editing = serial; state.textContent = 'не сохранено — нажмите «Применить»';
                        state.classList.add('dirty'); };
  dev.querySelectorAll('input[type=range]').forEach(r => {
    r.oninput = () => { r.nextElementSibling.textContent = r.value; touch(); };
  });
  dev.querySelectorAll('input[type=checkbox],input[type=number]').forEach(i => i.onchange = touch);
  const style = dev.querySelector('[data-k=style]');
  style.onchange = () => {           // стиль перезаписывает связанные пороги
    const preset = (data.styles[style.value]||{}).sliders||{};
    dev.querySelectorAll('.cell input[type=range]').forEach(r => {
      if (preset[r.dataset.k] !== undefined){
        r.value = preset[r.dataset.k];
        r.nextElementSibling.textContent = r.value;
      }
    });
    touch();
  };
  dev.querySelector('[data-k=chart]').onchange = touch;
  dev.querySelectorAll('button[data-a]').forEach(b => {
    b.onclick = async () => {
      if (b.dataset.a === 'save'){
        const cfg = {serial};
        dev.querySelectorAll('[data-k]').forEach(i => {
          cfg[i.dataset.k] = i.type==='checkbox' ? i.checked
                           : i.tagName==='SELECT' ? i.value : parseFloat(i.value);
        });
        await api('/api/device/'+serial+'/config', 'POST', cfg);
      } else {
        await api('/api/device/'+serial+'/'+b.dataset.a, 'POST', {});
      }
      editing = null;
      refresh();
    };
  });
}
setInterval(refresh, 3000);
refresh();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype='application/json'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith('text') else ''))
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ('/', '/index.html'):
            self._send(200, PAGE, 'text/html')
            return
        if u.path == '/api/devices':
            MANAGER.reload_if_changed()      # бот пишет сюда живой стек
            devices = [MANAGER.status(d['serial']) for d in MANAGER.devices]
            self._send(200, {'devices': devices, 'charts': MANAGER.charts(),
                             'styles': MANAGER.styles(), 'flags': FLAGS + BOT_FLAGS,
                             'sliders': SLIDERS, 'total': len(devices)})
            return
        self._send(404, {'error': 'not found'})

    def do_POST(self):
        u = urlparse(self.path)
        parts = u.path.strip('/').split('/')
        # /api/device/<serial>/start|stop|config
        if len(parts) == 4 and parts[0] == 'api' and parts[1] == 'device':
            serial, action = parts[2], parts[3]
            length = int(self.headers.get('Content-Length') or 0)
            data = {}
            if length:
                raw = self.rfile.read(length)
                for enc in ('utf-8', 'cp1251', 'cp866'):
                    try:
                        data = json.loads(raw.decode(enc))
                        break
                    except (ValueError, UnicodeDecodeError):
                        continue
            if action == 'start':
                ok, msg = MANAGER.start(serial)
                self._send(200, {'ok': ok, 'msg': msg})
                return
            if action == 'stop':
                ok, msg = MANAGER.stop(serial)
                self._send(200, {'ok': ok, 'msg': msg})
                return
            if action == 'config':
                d = MANAGER.save_config(serial, data)
                self._send(200, {'ok': True, 'device': d})
                return
        self._send(404, {'error': 'not found'})


def main():
    ap = argparse.ArgumentParser(description='Веб-панель ClubGG')
    ap.add_argument('--port', type=int, default=8090)
    args = ap.parse_args()
    print(f'Панель: http://127.0.0.1:{args.port}  (Ctrl+C — выход)')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
