#!/usr/bin/env bash
# Запуск ассистента одной командой:  ./start.sh
#
# Скрипт идемпотентный: создаёт виртуальное окружение, ставит зависимости,
# индексирует документы при первом запуске и поднимает сервер. Повторный
# запуск просто стартует сервер.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON=${PYTHON:-python3}
HOST=${HOST:-127.0.0.1}
PORT=${PORT:-8000}

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

if ! command -v "$PYTHON" >/dev/null; then
    echo "Не найден $PYTHON. Установите Python 3.10 или новее: https://www.python.org/downloads/"
    exit 1
fi

if [ ! -d .venv ]; then
    step "Создаю виртуальное окружение"
    "$PYTHON" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
    step "Ставлю зависимости (первый раз это пара минут)"
    pip install --quiet --upgrade pip
    pip install --quiet -r requirements.txt
    touch .venv/.deps-installed
fi

if [ ! -f .env ]; then
    step "Создаю .env"
    cp .env.example .env
    cat <<'MSG'
Файл .env создан. Откройте его и впишите два значения:

  ANTHROPIC_API_KEY=sk-ant-...   ключ с https://console.anthropic.com/settings/keys
  ADMIN_TOKEN=...                любой пароль для входа в админ-панель

Потом запустите ./start.sh ещё раз.
MSG
    exit 1
fi

if ! grep -q '^ANTHROPIC_API_KEY=sk-' .env; then
    echo "В .env не заполнен ANTHROPIC_API_KEY — без него ассистент найдёт нормы, но не сформулирует ответ."
    echo "Ключ берётся здесь: https://console.anthropic.com/settings/keys"
fi

if [ ! -d data/chroma ]; then
    step "Индексирую документы (скачается модель поиска, ~80 МБ)"
    python scripts/index_documents.py
fi

step "Сервер запускается на http://$HOST:$PORT"
echo "  чат          http://$HOST:$PORT/"
echo "  админ-панель http://$HOST:$PORT/admin"
echo "  остановить   Ctrl+C"
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
