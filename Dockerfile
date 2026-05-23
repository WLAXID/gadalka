# gadalka paper-trader + Telegram bot
#
# Двухстадийная сборка на python:3.12-slim.
# Секреты не вшиваются: .env монтируется снаружи (через docker-compose).
# DB и логи — на именованных томах.

FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Зависимости отдельным слоем для кеширования
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir --target=/install -r requirements.txt

# ---------- runtime ----------
FROM python:3.12-slim-bookworm AS runtime

# Tini для корректного PID-1 и проброса сигналов
RUN apt-get update && apt-get install -y --no-install-recommends \
        tini ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Непривилегированный пользователь
RUN groupadd --system app \
    && useradd --system --gid app --uid 10001 --home /app app

WORKDIR /app

# Зависимости из builder'а
COPY --from=builder /install /usr/local/lib/python3.12/site-packages

# Код
COPY src/ ./src/
COPY requirements.txt ./

RUN mkdir -p /app/data /app/logs \
    && chown -R app:app /app

USER app

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PAPER_DB_PATH=/app/data/paper.duckdb

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "src.paper"]
