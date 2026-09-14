FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system godzilla \
    && useradd --system --gid godzilla --home-dir /app --shell /usr/sbin/nologin godzilla

COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config
COPY data ./data

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && chown -R godzilla:godzilla /app

USER godzilla

CMD ["godzilla", "run", "--config-dir", "/app/config"]
