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

HTTP request bodies are limited to 512 MiB (536,870,912 bytes), including
multipart fields and boundaries. Middleware checks both `Content-Length` and
the actual received bytes before routing and returns `413` for oversized bodies.
Large accepted bodies are spooled to temporary disk storage. A setting may contain
at most 512 MiB of images in total, counting retained images and replacement
uploads once. Existing stored images do not count toward the request-body limit.
`UPLOAD_LIMIT_BYTES` still limits document uploads per job.

Each setting accepts at most 100 elements across fandoms, unit conversions,
phrase conversions, and image insertion rules combined. Matchers (unit source,
phrase source including regex, and image trigger) allow at most 128 characters;
other editable setting text remains limited to 64 characters.

Deleting a setting returns `202` and blocks edits, copies, and new processing
runs immediately. The worker removes the setting after all related queued and
running jobs finish. Completed output remains available to download. Image keys
are queued transactionally for deletion after commit, with retries on storage
failure and worker restart.

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
export TEXT_EVOLVER_TEST_POSTGRES_URL=postgresql+psycopg://user:password@localhost/test_db
.venv/bin/pytest
.venv/bin/ruff check src tests
```

The suite covers the Flyway SQLite baseline, schema parity, authentication,
CSRF, S3-backed setting-image flows, text analysis, all four document formats,
and incremental Pokémon cache behavior. Database-backed application tests require
PostgreSQL and are skipped if `TEXT_EVOLVER_TEST_POSTGRES_URL` is unset. SQLite
is used only for schema reflection and model-generation checks. S3 and Pokémon/Selenium network access
are mocked in deterministic tests; no MinIO smoke test is included.

## Important environment variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy psycopg URL; Compose points it at PgBouncer |
| `SECRET_KEY` | Signs the CSRF-only cookie; minimum 32 characters |
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
| `APP_BASE_URL` | Public application origin; initially `http://localhost:8000` |
| `WEBAUTHN_RP_ID` | Passkey relying-party hostname; initially `localhost` |
| `WEBAUTHN_ORIGIN` | Exact passkey origin; initially `http://localhost:8000` |
| `GOOGLE_CLIENT_ID` | Optional Google OAuth web client ID |
| `GOOGLE_CLIENT_SECRET` | Optional Google OAuth web client secret |
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


## Account settings and localization

Each account can enroll at most three passkeys, including concurrent enrollments.

Open **Account settings** from the workspace header to change your login name,
change a known password, connect Google, or enroll and manage WebAuthn passkeys.
Security changes require authentication within the preceding five minutes. When
prompted, authenticate again and return to settings to submit your change.
Changing a login name preserves account identity and ownership of all settings
and jobs. Keep at least one usable sign-in method when disconnecting Google or
removing passkeys.

The interface, help, validation, notifications, and logo support English and
Russian. The logo reads **TEXT EVOLVER** or **Эволюция текста**. The only language
selector is in account settings and offers **Default (browser language)**,
**English**, and **Русский**. New accounts use Default. Default resolves the
request's `Accept-Language` preferences, with English as the fallback for
unsupported languages. An explicit choice persists on the account. Anonymous
pages always use browser language; no language cookie or login-page toggle is
used. Document text and user-created conversion rules are not translated.

## Sessions and recovery

The session cookie is an opaque random ID; only its SHA-256 hash is stored in
`browser_session.token_hash`, which is the primary key. CSRF retains the original
signed-cookie mechanism in a separate `text_evolver_csrf` cookie containing only
`csrf_token`. The authentication cookie exposes only the opaque session token;
user identity and authentication metadata remain in the database. There is no
CSRF column or generic session data blob in `browser_session`. Flash messages use
`browser_session_flash`, and recovery grants remain in `auth_flow`. Both reference
the session hash with cascading updates and deletion. The session record stores
authentication freshness and expiration. Every sign-in creates a session with a fixed seven-day
lifetime. Requests and reauthentication do not extend it. Reauthentication
rotates the ID and CSRF token while preserving the original deadline. Logout
revokes the current session, and expired or revoked IDs cannot authenticate.
Processing WebSockets recheck validity and close with code 1008 when access ends;
worker jobs keep running. A dedicated worker thread cleans expired or revoked
sessions and expired authentication flows in bounded batches once a minute,
independently of job processing and login-failure cleanup.

CSRF middleware validates every HTTP POST using `X-CSRF-Token` or the form's
`csrf_token` field. Other HTTP methods and WebSockets bypass CSRF validation.

Forgot-password recovery requires an enrolled passkey and successful WebAuthn
verification. Google authentication and knowledge of a login name do not grant
password recovery. A five-minute, single-use grant authorizes only replacing an
existing password. Resetting it revokes all account sessions and requires fresh
sign-in. Removing the authorizing passkey invalidates its grants; removing the
last passkey disables both passkey sign-in and recovery. Authenticated changes
of a known password revoke other sessions and rotate the current session ID.

## Google setup on localhost

Create a Google OAuth **Web application** client and configure the exact redirect
URI `http://localhost:8000/auth/google/callback`. Set `GOOGLE_CLIENT_ID` and
`GOOGLE_CLIENT_SECRET` in the app's `.env`, then restart the app. Both values are
required to enable Google controls. No manually supplied Google token is needed.
TextEvolver uses OpenID Connect authorization code flow with PKCE, state, nonce,
and server-side signature and claim validation. Google identity is stored by
issuer and subject; matching email addresses never automatically merge accounts.
A first-time Google user chooses a unique TextEvolver login name. Accounts without a password can set an initial local password after recent
Google or passkey authentication.
Provider access/refresh tokens are not persisted. Access logs omit authentication
query strings, and dynamic responses use `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`.

## WebAuthn setup on localhost

Keep `APP_BASE_URL=http://localhost:8000`, `WEBAUTHN_RP_ID=localhost`, and
`WEBAUTHN_ORIGIN=http://localhost:8000`. Use `localhost` consistently. Browsers
allow WebAuthn over HTTP localhost; production requires HTTPS and
`COOKIE_SECURE=true`. The RP ID has no scheme or port. A port change requires
updating the origin and Google callback; another RP hostname requires enrolling
credentials for that hostname. Enrollment requires user verification and a
resident/discoverable credential. A browser without WebAuthn can still use
password or configured Google sign-in.

## Authentication schema and validation

All schema changes remain in the matching V1 baselines. Apply them to a fresh
database and regenerate models with `./migrations/generate_models.sh`. Existing
V1 databases are not automatically upgraded by these baseline edits. Development
validation uses disposable databases and does not reset existing deployments.

The authentication tests include a virtual authenticator producing real signed
WebAuthn registration/assertion responses, mocked Google exchanges, ID-token
signature/claim validation, expiration, revocation, concurrent recovery, and
concurrent credential removal. To run them, point the
following variable at a disposable test database whose user can create schemas:

```bash
TEXT_EVOLVER_TEST_POSTGRES_URL=postgresql+psycopg://user:password@localhost/test_db \
  .venv/bin/pytest tests/test_accounts.py tests/test_web.py
```

Each database-backed test creates and removes its own PostgreSQL schema.
Regenerate translation catalogs after changing messages:

```bash
.venv/bin/pybabel extract -F babel.cfg -k translate -k plural:1,2 -k ngettext:1,2 \
  -k HTTPException:2 -k flash:2 -k validation_error_response:2 -o /tmp/text-evolver.pot .
.venv/bin/pybabel update -i /tmp/text-evolver.pot -d src/text_evolver/locales
.venv/bin/pybabel compile -d src/text_evolver/locales
```

Review translations and remove fuzzy flags before compiling. Commit source `.po`
catalogs; generated `.mo` files are ignored by Git and excluded from the Docker
build context. The Docker build compiles catalogs before building the application
wheel, which packages both `.po` and `.mo` files. For local development, run the
compile command above after a fresh checkout or after editing translations.

TODO
- tokens creation, refresh, delete via settings (max one read, and one read+write)
- api to work with processing settings and to discover them, search, create tasks, check status, get result
- logs, metrics, traces, export to grafana (otel to write, prometheus, loki, jaeger to store at rest, grafana to display)
