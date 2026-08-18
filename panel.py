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

BASE = os.path.dirname(os.path.abspath(__file__))
DEVICES_FILE = os.path.join(BASE, 'devices.json')
LOGS_DIR = os.path.join(BASE, 'logs')
PYTHON = sys.executable
ADB = os.environ.get('CLUBGG_ADB', r'E:/down/platform-tools/platform-tools/adb.exe')

# имя -> (serial по умолчанию, описание)
DEFAULT_DEVICES = [
    {'name': 'Телефон 1', 'serial': '1cf5db29', 'chart': 'charts/6max_standard.json',
     'aggression': 1.0, 'defense': 1.0, 'stack': 69.6},
]

# ---------------------------------------------------------------------------
# процессы ботов
# ---------------------------------------------------------------------------
class BotManager:
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
                return
            except (OSError, ValueError):
                pass
        self.devices = DEFAULT_DEVICES
        self.save_devices()

    def save_devices(self):
        with open(DEVICES_FILE, 'w', encoding='utf-8') as f:
            json.dump(self.devices, f, ensure_ascii=False, indent=2)

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
        d = self.device(serial) or {}
        online = serial in self.adb_online()
        run = self.running(serial)
        tail = self.tail(serial, 6)
        return {'serial': serial, 'name': d.get('name', serial),
                'online': online, 'running': run,
                'chart': d.get('chart'), 'aggression': d.get('aggression', 1.0),
                'defense': d.get('defense', 1.0), 'stack': d.get('stack', 69.6),
                'log': tail}

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
        d = self.device(serial)
        if d is None:
            d = {'serial': serial, 'name': serial}
            self.devices.append(d)
        for key in ('name', 'chart', 'aggression', 'defense', 'stack'):
            if key in data:
                d[key] = data[key]
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
</style></head><body><div class="wrap">
<h1>🎰 ClubGG — панель управления ботами</h1>
<div id="list"></div>
<div class="hint">Автообновление каждые 3с. Настройки применяются при следующем старте бота.</div>
</div>
<script>
async function api(path, method, body){
  const r = await fetch(path, {method, headers:{'Content-Type':'application/json'},
    body: body?JSON.stringify(body):undefined});
  return r.json();
}
async function refresh(){
  const data = await api('/api/devices');
  const el = document.getElementById('list');
  el.innerHTML = (data.devices||[]).map(d => `
   <div class="dev" data-serial="${d.serial}"><h2>${d.name||d.serial}
     <span class="badge ${d.online?'on':'off'}">${d.online?'в сети':'нет связи'}</span>
     <span class="badge ${d.running?'on':'off'}">${d.running?'ИГРАЕТ':'остановлен'}</span></h2>
   <div class="row"><label>Чарт</label>
     <select data-k="chart">${(data.charts||[]).map(c =>
       `<option ${d.chart==='charts/'+c?'selected':''}>charts/${c}</option>`).join('')}</select>
     <label>Агрессия</label><input type="range" data-k="aggression" min="0.5" max="2" step="0.1" value="${d.aggression}">
     <span id="agg_${d.serial}">${d.aggression}</span>
     <label>Защита</label><input type="range" data-k="defense" min="0.5" max="2" step="0.1" value="${d.defense}">
     <span id="def_${d.serial}">${d.defense}</span>
   </div>
   <div class="row">
     <button class="go" data-a="start" ${d.running?'disabled':''}>▶ Старт</button>
     <button class="stop" data-a="stop" ${d.running?'':'disabled'}>■ Стоп</button>
     <button data-a="save">💾 Сохранить настройки</button>
   </div>
   <pre class="log">${(d.log||'').replace(/&/g,'&amp;').replace(/</g,'&lt;')}</pre>
  </div>`).join('');
  document.querySelectorAll('.dev').forEach(dev => {
    const serial = dev.dataset.serial;
    dev.querySelectorAll('button[data-a]').forEach(b => {
      b.dataset.serial = serial;
      b.onclick = async () => {
        if (b.dataset.a === 'save'){
          const cfg = {serial, name: dev.querySelector('h2').textContent.trim()};
          dev.querySelectorAll('[data-k]').forEach(i => {
            cfg[i.dataset.k] = i.tagName==='SELECT' ? i.value : parseFloat(i.value);
          });
          await api('/api/device/'+serial+'/config', 'POST', cfg);
        } else {
          await api('/api/device/'+serial+'/'+b.dataset.a, 'POST', {});
        }
        refresh();
      };
    });
    dev.querySelectorAll('input[type=range]').forEach(r => {
      r.oninput = () => document.getElementById((r.dataset.k==='aggression'?'agg_':'def_')+serial).textContent = r.value;
    });
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
            devices = [MANAGER.status(d['serial']) for d in MANAGER.devices]
            self._send(200, {'devices': devices, 'charts': MANAGER.charts(),
                             'total': len(devices)})
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
