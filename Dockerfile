FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip wheel --wheel-dir /wheels .

FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    CHROME_BINARY=/usr/bin/chromium

RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        chromium \
        chromium-driver \
        fonts-dejavu-core \
        libxml2 \
        libxslt1.1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 text-evolver \
    && useradd --system --uid 10001 --gid text-evolver --create-home text-evolver

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/* && rm -rf /wheels

WORKDIR /app
RUN mkdir -p /data/work /data/tmp /data/chrome \
    && chown -R text-evolver:text-evolver /data /app

USER text-evolver
EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=2)"

CMD ["uvicorn", "text_evolver.main:app", "--host", "0.0.0.0", "--port", "8000"]

