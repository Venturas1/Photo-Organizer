@echo off
chcp 65001 >nul
title Smart Photo Organizer

echo ========================================================
echo        Запуск Smart Photo Organizer...
echo ========================================================

REM Check if input and output folders exist, create them if not
if not exist "input" mkdir "input"
if not exist "output" mkdir "output"

REM Check if virtual environment exists
if exist "organizer\.venv\Scripts\activate.bat" goto ACTIVATE_VENV

echo [!] Віртуальне середовище не знайдено. Створюємо...
python -m venv organizer\.venv
echo [!] Встановлюємо залежності (це займе кілька хвилин)...
organizer\.venv\Scripts\pip.exe install -r organizer\requirements.txt
goto RUN_APP

:ACTIVATE_VENV
call organizer\.venv\Scripts\activate.bat

:RUN_APP
echo [OK] Запуск інтерфейсу...
organizer\.venv\Scripts\python.exe organizer\main.py

REM If program crashes, keep the window open
if %errorlevel% neq 0 (
    echo.
    echo [ПОМИЛКА] Робота завершилася аварійно. Дивіться логи в organizer/data/logs/
    pause
)
