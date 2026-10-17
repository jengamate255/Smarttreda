@echo off
echo Removing Trading Bot from startup...
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\TradingBot.vbs" 2>nul
if %errorlevel%==0 (
    echo Bot removed from startup successfully.
) else (
    echo Bot was not in startup folder.
)
pause
