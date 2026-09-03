@echo off
rem Запуск ассистента одной командой: дважды кликните по этому файлу.
rem Скрипт идемпотентный: окружение и индекс создаются при первом запуске.
setlocal
cd /d "%~dp0"
chcp 65001 >nul

set "HOST=127.0.0.1"
set "PORT=8000"

where python >nul 2>nul
if errorlevel 1 (
    echo Не найден Python. Установите Python 3.10 или новее с https://www.python.org/downloads/
    echo При установке отметьте галочку "Add python.exe to PATH".
    pause
    exit /b 1
)

if not exist .venv (
    echo.
    echo ==^> Создаю виртуальное окружение
    python -m venv .venv
)
call .venv\Scripts\activate.bat

if not exist .venv\.deps-installed (
    echo.
    echo ==^> Ставлю зависимости, первый раз это пара минут
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
    if errorlevel 1 (
        echo Не удалось поставить зависимости.
        pause
        exit /b 1
    )
    echo ok> .venv\.deps-installed
)

if not exist .env (
    echo.
    echo ==^> Создаю .env
    copy /y .env.example .env >nul
    echo.
    echo Файл .env создан. Откройте его блокнотом и впишите два значения:
    echo.
    echo   ANTHROPIC_API_KEY=sk-ant-...   ключ с https://console.anthropic.com/settings/keys
    echo   ADMIN_TOKEN=...                любой пароль для входа в админ-панель
    echo.
    echo Потом запустите start.bat ещё раз.
    pause
    exit /b 1
)

if not exist data\chroma (
    echo.
    echo ==^> Индексирую документы, скачается модель поиска, около 80 МБ
    python scripts\index_documents.py
    if errorlevel 1 (
        echo Индексация не удалась.
        pause
        exit /b 1
    )
)

echo.
echo ==^> Сервер запускается
echo   чат          http://%HOST%:%PORT%/
echo   админ-панель http://%HOST%:%PORT%/admin
echo   остановить   Ctrl+C
start "" http://%HOST%:%PORT%/
python -m uvicorn app.main:app --host %HOST% --port %PORT%
pause
