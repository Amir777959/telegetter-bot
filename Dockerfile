FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    KEEPALIVE_PORT=8080 \
    DB_FILE=/app/data/reposter.db \
    LOG_FILE=/app/data/reposter.log

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
