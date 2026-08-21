FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Kyiv

WORKDIR /app

# CA-сертифікати потрібні для HTTPS до api.telegram.org і sf-ecom-api.silpo.ua
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Стан лежить у томі; контейнер працює від непривілейованого користувача.
RUN useradd --create-home --uid 10001 watcher \
 && mkdir -p /data && chown -R watcher:watcher /data /app
USER watcher

VOLUME ["/data"]

HEALTHCHECK --interval=5m --timeout=20s --start-period=30s --retries=3 \
    CMD python -c "import sqlite3,os,sys; sys.exit(0 if os.path.exists(os.getenv('STATE_PATH','/data/state.db')) else 1)"

ENTRYPOINT ["python", "-m", "app.main"]
CMD ["watch"]
