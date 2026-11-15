@echo off
cd /d "%~dp0"
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo Created .env - opening it in Notepad. Paste your keys, save, close Notepad, then run start.bat again.
    notepad ".env"
    pause
    exit /b
)
echo Installing/updating libraries (first run takes a minute)...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo.
    echo Python or pip not found. Install Python from python.org and tick "Add Python to PATH".
    pause
    exit /b
)
echo.
echo Starting bot... keep this window open. Press Ctrl+C to stop.
python bot.py
pause
