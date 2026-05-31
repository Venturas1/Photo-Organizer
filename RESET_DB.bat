@echo off
chcp 65001 >nul
title Очищення бази даних Smart Photo Organizer

echo ========================================================
echo        Очищення бази даних Smart Photo Organizer...
echo ========================================================

if exist "organizer\data\database.db" (
    del /f /q "organizer\data\database.db"
    echo [OK] Базу даних успішно видалено. При наступному запуску вона буде створена порожньою.
) else (
    echo [!] Файл бази даних за шляхом organizer\data\database.db не знайдено.
)

pause
