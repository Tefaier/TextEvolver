# TextEvolver

TextEvolver is a FastAPI web application that applies configurable unit and
phrase conversions and image insertion rules to DOCX, EPUB, HTML, and FB2
documents. The browser UI submits jobs to PostgreSQL; a separate worker reads
the queue and writes processed files to a shared work volume.

## Requirements

- Python 3.12 for local development
- PostgreSQL 17 and PgBouncer, or a Compose-compatible container runtime
- Flyway 11 with PostgreSQL and SQLite support for manual migration work
- Chromium for Pokémon image scraping

## Local Python setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Set a random `SECRET_KEY`, database credentials, and `DATABASE_URL` in `.env`.
The application has no SQLite runtime fallback. Start the web process and the
worker separately:

```bash
.venv/bin/uvicorn text_evolver.main:app --reload
.venv/bin/python -m text_evolver.worker
```

## Database migrations and models

Flyway SQL under `migrations/sql` is the schema authority. PostgreSQL is the
runtime database. SQLite is used only as a fresh reflection target for model
generation.

Apply migrations manually:

```bash
cp migrations/.env.example migrations/.env
./migrations/apply.sh
```

Regenerate the checked-in SQLAlchemy 2.x models:

```bash
./migrations/generate_models.sh
```

The generation command overwrites `src/text_evolver/db/models.py`; do not edit
that file by hand. Every new Flyway version must have corresponding PostgreSQL
and SQLite scripts. See [migrations/README.md](migrations/README.md) for details.

## Build with Podman

Create a local image from the repository root:

```bash
podman build -t text-evolver:local .
```

The image includes Python 3.12, Chromium, Chromium Driver, fonts, and all Python
runtime dependencies. It runs as an unprivileged user.

## Start Compose

Initialize local environment values and start the stack:

```bash
cp .env.example .env
docker compose up --build
```

`podman compose up --build` can be used when a Compose provider is configured.
The stack contains PostgreSQL, PgBouncer, a one-shot Flyway migration service,
the FastAPI server, and one worker. PostgreSQL is not published to the host;
host and application connections go through PgBouncer on port 6432. Flyway is
the only service that connects directly to PostgreSQL.

Open <http://localhost:8000>. Health endpoints are available at
`/health/live` and `/health/ready`.

Stop containers while retaining data:

```bash
docker compose down
```

Delete local database and work volumes and return to a fresh V1 database:

```bash
docker compose down --volumes
```

This last command permanently removes the Compose-managed local data.

## Tests and checks

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
```

The suite covers the Flyway SQLite baseline, schema parity, authentication,
CSRF, settings and upload flows, text analysis, and all four document formats.
Pokémon/Selenium network access is excluded from deterministic tests.

## Important environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy psycopg URL; Compose points it at PgBouncer |
| `SECRET_KEY` | Signs session cookies; minimum 32 characters |
| `WORK_ROOT` | Shared job input/output directory |
| `TEMP_ROOT` | Temporary processing directory |
| `CHROME_USER_DATA_DIR` | Selenium browser profile directory |
| `CHROME_BINARY` | Optional Chromium executable override |
| `COOKIE_SECURE` | Enables HTTPS-only session cookies |
| `UPLOAD_LIMIT_BYTES` | Total upload limit per job |
| `WORKER_POLL_SECONDS` | Queue polling interval |
| `WORKER_MIN_FREE_MEMORY_BYTES` | Memory threshold before claiming jobs |

