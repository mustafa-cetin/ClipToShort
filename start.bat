@echo off
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv || goto :err
    .venv\Scripts\python -m pip install -q -r requirements.txt || goto :err
)
.venv\Scripts\python app.py
goto :eof
:err
echo Setup failed. Is Python 3.10+ installed?
pause
