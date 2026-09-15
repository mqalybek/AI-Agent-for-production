"""Конфигурация приложения. Читается из переменных окружения (см. .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _clean_value(value: str) -> str:
    """Снять кавычки и пробелы вокруг значения.

    Ключ, записанный как ANTHROPIC_API_KEY="sk-ant-...", уходил в Anthropic
    вместе с кавычками и отклонялся как неверный.
    """
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1].strip()
    return value


def _load_dotenv() -> None:
    """Минимальный загрузчик .env, чтобы не тянуть лишнюю зависимость."""
    path = Path(__file__).resolve().parent.parent / ".env"
    if not path.exists():
        return
    # utf-8-sig убирает BOM: блокнот Windows дописывает его в начало файла,
    # и первая переменная превращалась в «\ufeffANTHROPIC_API_KEY».
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), _clean_value(value))


_load_dotenv()

# Телеметрия ChromaDB не нужна и в 0.6.x шумит в логах — глушим до импорта.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
os.environ.setdefault("CHROMA_TELEMETRY_IMPL", "chromadb.telemetry.product.NoopTelemetryClient")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = _env("ANTHROPIC_API_KEY")
    anthropic_model: str = _env("ANTHROPIC_MODEL", "claude-opus-5")

    embeddings_provider: str = _env("EMBEDDINGS_PROVIDER", "local").lower()
    voyage_api_key: str = _env("VOYAGE_API_KEY")
    voyage_model: str = _env("VOYAGE_MODEL", "voyage-3")

    chroma_dir: str = _env("CHROMA_DIR", "./data/chroma")
    upload_dir: str = _env("UPLOAD_DIR", "./data/uploads")
    collection_name: str = _env("COLLECTION_NAME", "subsoil_law")

    admin_token: str = _env("ADMIN_TOKEN")

    chunk_size: int = _env_int("CHUNK_SIZE", 1200)
    chunk_overlap: int = _env_int("CHUNK_OVERLAP", 200)
    top_k: int = _env_int("TOP_K", 6)

    max_upload_mb: int = _env_int("MAX_UPLOAD_MB", 40)


settings = Settings()

# Значение из .env.example: пароль администратора, который забыли поменять.
DEFAULT_ADMIN_TOKEN = "change-me-please"

DISCLAIMER = (
    "Ответ подготовлен автоматически на основании загруженных документов "
    "и не заменяет консультацию квалифицированного юриста."
)
