FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Системные утилиты и установка корневых сертификатов Минцифры РФ
# (обязательно для работы с https://platform-api2.max.ru с 19.07.2026)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    libpq5 \
    && mkdir -p /usr/local/share/ca-certificates/mincifry \
    && (curl -fsSL https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
          -o /usr/local/share/ca-certificates/mincifry/russian_trusted_root_ca.crt || true) \
    && (curl -fsSL https://gu-st.ru/content/lending/russian_trusted_sub_ca_pem.crt \
          -o /usr/local/share/ca-certificates/mincifry/russian_trusted_sub_ca.crt || true) \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt

COPY pyproject.toml README.md ./
COPY src ./src
COPY web ./web
COPY migrations ./migrations
COPY alembic.ini ./

RUN pip install --upgrade pip && pip install .

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
