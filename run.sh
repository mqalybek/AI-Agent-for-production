#!/usr/bin/env bash
# Локальный запуск: ./run.sh  (по умолчанию http://127.0.0.1:8000)
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "Нет .env — скопируйте .env.example и укажите ключи."; exit 1; }
exec python3 -m uvicorn app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}" "$@"
