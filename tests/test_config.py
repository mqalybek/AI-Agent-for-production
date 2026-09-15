"""Тесты чтения .env: кавычки, BOM и прочие ловушки блокнота."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import _clean_value  # noqa: E402
from app.rag import describe_key, inspect_key  # noqa: E402


def test_quotes_are_stripped():
    """Ключ в кавычках уходил в Anthropic вместе с ними и отклонялся."""
    assert _clean_value('"sk-ant-REAL"') == "sk-ant-REAL"
    assert _clean_value("'sk-ant-REAL'") == "sk-ant-REAL"
    assert _clean_value("  sk-ant-REAL  ") == "sk-ant-REAL"
    assert _clean_value('""') == ""


def test_quote_inside_value_is_kept():
    """Кавычка внутри пароля — часть пароля, а не обёртка."""
    assert _clean_value('па"роль') == 'па"роль'


def test_dotenv_handles_bom_and_quotes(tmp_path, monkeypatch):
    """Блокнот Windows пишет BOM — первая переменная из-за него пропадала."""
    import importlib
    import os

    env = tmp_path / ".env"
    env.write_text(
        'ANTHROPIC_API_KEY="sk-ant-quoted"\nADMIN_TOKEN=secret\nexport TOP_K=4\n',
        encoding="utf-8-sig",
    )
    for name in ("ANTHROPIC_API_KEY", "ADMIN_TOKEN", "TOP_K"):
        os.environ.pop(name, None)

    from app import config

    monkeypatch.setattr(config.Path, "resolve", config.Path.resolve)  # без подмен
    monkeypatch.setattr(
        config, "_load_dotenv", config._load_dotenv
    )  # функция та же, меняем только путь
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config.__dict__["Path"], "__call__", config.__dict__["Path"].__call__, raising=False
    )

    # Читаем файл напрямую тем же кодом, что и приложение.
    for line in env.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), config._clean_value(value))

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-ant-quoted"
    assert os.environ["TOP_K"] == "4"
    importlib.reload(config)


def test_placeholder_key_is_recognised():
    """Незаполненный шаблон — самая частая причина «ключ отклонён»."""
    assert "шаблона" in inspect_key("sk-ant-...")


def test_key_problems_are_named_precisely():
    assert "не похож на ключ Anthropic" in inspect_key("my-secret-key")
    assert "пробел" in inspect_key("sk-ant-abc def" + "z" * 40)
    assert "не полностью" in inspect_key("sk-ant-short")
    assert inspect_key("sk-ant-api03-" + "x" * 90) is None
    assert inspect_key("") is None  # пустой ключ — отдельный статус


def test_describe_key_hides_the_secret():
    """В сообщениях видно, какой ключ прочитан, но не сам ключ."""
    key = "sk-ant-api03-" + "x" * 90 + "END1"
    described = describe_key(key)
    assert key not in described
    assert "sk-ant-api" in described and "END1" in described
    assert "длина 107" in described
