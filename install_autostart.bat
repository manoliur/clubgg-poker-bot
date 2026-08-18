@echo off
rem Установка автозапуска панели ClubGG (добавляет ярлык в папку Автозагрузка).
rem Запусти один раз — после этого панель будет стартовать вместе с Windows.
cd /d "%~dp0"
set STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
copy /y "%~dp0start_panel.bat" "%STARTUP%\clubgg_panel.bat" >nul
echo Панель добавлена в автозагрузку: %STARTUP%\clubgg_panel.bat
pause
