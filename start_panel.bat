@echo off
rem Запуск веб-панели ClubGG. После старта открой в браузере: http://127.0.0.1:8090
cd /d "%~dp0"
start "" /min pythonw panel.py --port 8090
if errorlevel 1 (
  echo Не удалось запустить через pythonw, пробую python...
  start "" /min python panel.py --port 8090
)
echo Панель запущена: http://127.0.0.1:8090
timeout /t 3 >nul
