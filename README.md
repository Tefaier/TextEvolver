# TextEvolver

TextEvolver is a FastAPI web application that applies configurable unit and
phrase conversions and image insertion rules to DOCX, EPUB, HTML, and FB2
documents. The browser UI submits jobs to PostgreSQL; a separate worker reads
the queue and writes processed files to a shared work volume. User-provided
setting images live in MinIO and are staged locally when a job starts.

## Requirements

- Python 3.12 for local development
- PostgreSQL 17 and PgBouncer, or a Compose-compatible container runtime
- Podman with Podman Compose for the local MinIO object-store container
- Flyway 11 with PostgreSQL and SQLite support for manual migration work
- Chromium for Pokémon image scraping

## Local Python setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
```

Set a random `SECRET_KEY`, database credentials, and `DATABASE_URL` in `.env`.
Set unique `S3_ACCESS_KEY` and `S3_SECRET_KEY` values as well. Processes running
directly on the host use `S3_ENDPOINT_URL=http://localhost:9000` when a local
S3-compatible service is separately exposed there.
The application has no SQLite runtime fallback. Start the web process and the
worker separately:

```bash
.venv/bin/uvicorn text_evolver.main:app --reload
.venv/bin/python -m text_evolver.worker
```

Set `WORKER_CONCURRENCY` to the maximum number of jobs that the worker may run
at once. It defaults to `1`; each additional slot can consume another document
processing child process and Chromium instance.

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
podman compose up --build
```

--build causes local build images to rebuild
--force-recreate recreates containers even if their configuration in compose have not changed yet they are already removed by `docker compose down`

The stack contains nginx, PostgreSQL, PgBouncer, a one-shot Flyway migration
service, MinIO, a one-shot MinIO bucket initializer, the FastAPI server, and
one worker. Only nginx publishes the application port. It applies a general
per-IP request limit and a stricter limit to `/login`; TLS termination is not
configured yet.
PostgreSQL, PgBouncer, and MinIO are not published to the host. Application and
worker connections go through PgBouncer on the internal Compose network.
Flyway is the only service that connects directly to PostgreSQL. App and worker
contact MinIO at `http://minio:9000`; only MinIO mounts `user-data` at `/data`.

Open <http://localhost:8000>. Health endpoints are available at
`/health/live` and `/health/ready`.

The MinIO initializer creates `S3_BUCKET` idempotently after MinIO becomes
healthy. Its S3 endpoint and management console remain internal to Compose.

### User image storage

Uploaded setting images are independent S3 objects. PostgreSQL stores only
their keys, sizes, and ordering. Replacements and deletions remove superseded
objects after the database commit; a rollback removes newly uploaded objects.
Copying a setting creates independent objects for the copy.

At job start the worker downloads referenced images to
`TEMP_ROOT/jobs/<job_id>/images`. Processing uses those local paths, and the
worker removes the directory on every completion path. This does not affect the
Pokémon cache under `TEMP_ROOT/Pokemons`.

### Pokémon cache

Before accepting jobs, the worker refreshes the Pokémon fandom cache under
`TEMP_ROOT/Pokemons`. It loads the current Pokémon list, compares it with
`pokemon.csv`, and visits detail pages only for entries that are absent or have
a missing image. The CSV stores names, page and image URLs, local image paths,
height, and weight; downloaded artwork is stored in `Pokemons/images`.

The CSV is updated atomically after each successful download, so an interrupted
first run resumes with the missing entries. A list-refresh failure leaves the
existing cache intact. Document-processing children use only the CSV data and
saved images; they do not start Selenium or make Pokémon network requests.

Stop containers while retaining data:

```bash
podman compose down
```

Delete all local volumes, including MinIO's `user-data`, and return to a fresh
V1 deployment:

```bash
podman compose down --volumes
```

This last command permanently removes the Compose-managed local data.

## Tests and checks

```bash
.venv/bin/pytest
.venv/bin/ruff check src tests
```

The suite covers the Flyway SQLite baseline, schema parity, authentication,
CSRF, S3-backed setting-image flows, text analysis, all four document formats,
and incremental Pokémon cache behavior. S3 and Pokémon/Selenium network access
are mocked in deterministic tests; no MinIO smoke test is included.

## Important environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy psycopg URL; Compose points it at PgBouncer |
| `SECRET_KEY` | Signs session cookies; minimum 32 characters |
| `WORK_ROOT` | Shared job input/output directory |
| `TEMP_ROOT` | Temporary processing directory |
| `S3_ENDPOINT_URL` | S3-compatible endpoint; host processes use `http://localhost:9000` |
| `S3_BUCKET` | Bucket containing user-provided setting images |
| `S3_REGION` | S3 signing region; local MinIO defaults to `us-east-1` |
| `S3_ACCESS_KEY` | S3 access key and local MinIO root username |
| `S3_SECRET_KEY` | S3 secret key and local MinIO root password |
| `S3_FORCE_PATH_STYLE` | Enables path-style addressing for MinIO |
| `CHROME_BINARY` | Optional Chromium executable override |
| `SELENIUMBASE_DRIVER_ROOT` | Ephemeral writable directory for SeleniumBase-generated drivers |
| `COOKIE_SECURE` | Enables HTTPS-only session cookies |
| `UPLOAD_LIMIT_BYTES` | Total upload limit per job |
| `WEBSOCKET_UPDATE_SECONDS` | Processing-status WebSocket update interval (5 seconds by default) |
| `WEBSOCKET_MAX_CONNECTIONS` | Maximum active processing-status WebSockets per app process |
| `WORKER_POLL_SECONDS` | Queue polling interval |
| `WORKER_CONCURRENCY` | Maximum jobs processed concurrently by the worker (1-64) |
| `WORKER_MIN_FREE_MEMORY_BYTES` | Memory threshold before claiming jobs |

### Password storage

New passwords must contain 12 to 128 characters. `user_password` stores only an
Argon2id PHC hash; plaintext passwords and reversible encryption keys are not
stored. Authentication performs a dummy Argon2id verification for unknown
users to reduce username timing differences, and successful authentication
upgrades hashes when the configured Argon2 parameters change.

### Login throttling

nginx limits all requests from one IP to 20 requests per second with a burst of
40, and `/login` to 10 requests per minute with a burst of 5. The application
also records failed `(username, IP)` pairs. Five failures within ten minutes
block further attempts. After ten minutes, one attempt is allowed; another
failure moves the window forward without resetting the failure count. A
username-wide failure score prevents IP rotation from bypassing throttling. At
five failures it requires a minimum interval of one second between attempts;
the interval doubles after each further failure up to 60 seconds. A successful
login removes both records. The worker checks every minute and removes pair or
account records whose relevant failure timestamp is more than one hour old.
