# Образ для запуска ассистента на сервере: docker build -t subsoil-rag .
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHROMA_DIR=/data/chroma \
    UPLOAD_DIR=/data/uploads

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY scripts ./scripts
COPY documents ./documents

# Индекс и загруженные файлы переживают пересборку образа только в томе.
VOLUME ["/data"]
EXPOSE 8000

# Документы индексируются при первом старте, дальше индекс берётся из тома.
CMD ["sh", "-c", "[ -d \"$CHROMA_DIR\" ] || python scripts/index_documents.py; exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
