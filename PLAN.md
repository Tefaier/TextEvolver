# TextEvolver Modernization Plan

## Summary

Rewrite TextEvolver as a Python 3.12 FastAPI/Jinja application while preserving the existing HTML user experience and document-processing behavior. PostgreSQL becomes the only runtime database, accessed through PgBouncer. Flyway SQL becomes the schema authority, and checked-in SQLAlchemy 2.x models are generated from a freshly migrated SQLite database.

Compose will run PostgreSQL, PgBouncer, Flyway, the FastAPI server, and a separate processing worker.

## Step-by-step implementation

1. **Establish regression coverage**

   - Add `pytest` tests for the existing text-cleaning, phrase replacement, unit conversion, and supported file-format behavior.
   - Capture route, authentication, setting CRUD/copy/search, upload, cancellation, and download expectations.
   - Use small fixture documents and mock Selenium/network-dependent Pokémon operations.

2. **Create the Python project structure**

   - Add `pyproject.toml` with Python `>=3.12,<3.13`, setuptools src-layout packaging, runtime dependencies, and a `dev` dependency group.
   - Move all application modules, templates, and static files under `src/text_evolver/`.
   - Organize code into configuration, database, web, processing, and worker packages.
   - Remove Flask, Flask-SQLAlchemy, Flask-Migrate, Flask-Login, Flask-WTF, WTForms, and Alembic after the FastAPI port is verified.

3. **Introduce centralized configuration and portable paths**

   - Use `pydantic-settings` for database URL, secret key, work directory, Chrome configuration, upload limits, worker polling, memory threshold, cookie security, and server options.
   - Require secrets and the runtime database URL; do not retain hard-coded fallback credentials or SQLite runtime fallback.
   - Resolve relative paths consistently with `pathlib`; use configurable work/temp roots and package-relative template/static locations.
   - Replace Windows separators and string path concatenation throughout processing code.
   - Sanitize uploaded filenames and isolate files by processing-job ID.

4. **Replace Alembic with a Flyway V1 baseline**

   - Remove the current Alembic history and create:
     - `migrations/sql/postgresql/V1__initial_schema.sql`
     - `migrations/sql/sqlite/V1__initial_schema.sql`
     - `migrations/generate_models.sh`
     - `migrations/apply.sh`
     - `migrations/.env.example`
     - `migrations/README.md`
   - Keep PostgreSQL and SQLite V1 scripts logically equivalent while using dialect-specific identity and timestamp syntax.
   - Create a cleaned schema with users, settings, fandom configuration, unit/phrase/image conversions, and processing jobs.
   - Replace the redundant setting owner ID/name foreign keys with one owner ID.
   - Store password hashes, make required defaults non-null, add cascade behavior and useful indexes, and replace `Thread` with an explicit job-state table.
   - Define job states such as `queued`, `running`, `completed`, `failed`, and `cancelled`, plus cancellation, timestamps, and error information.

5. **Implement repeatable migration/model tooling**

   - `generate_models.sh` will create a temporary SQLite database, apply the SQLite Flyway migrations, run the pinned `sqlacodegen` version, and overwrite `src/text_evolver/db/models.py`.
   - Generated SQLAlchemy 2.x models will be committed and treated as generated code; application behavior belongs in services/repositories rather than edits to that file.
   - `apply.sh` will validate and load `migrations/.env`, select the PostgreSQL migration location, and run Flyway without printing credentials.
   - Ignore `migrations/.env` while tracking its example.
   - Document that every future schema change needs matching PostgreSQL and SQLite migration files.

6. **Build the SQLAlchemy application layer**

   - Use synchronous SQLAlchemy 2.x sessions with psycopg 3.
   - Configure the application connection for PgBouncer transaction pooling, disabling driver-side prepared statements and avoiding redundant long-lived application pooling.
   - Add request-scoped sessions and independent worker sessions; never share sessions across processes.
   - Move setting creation/copy/update, search, authentication, access checks, and job transitions into explicit services with transaction boundaries.
   - Replace submitted-value `eval()` calls with strict boolean/form parsing.

7. **Port the web server to FastAPI**

   - Create an app factory/lifespan and serve existing Jinja templates and static assets through FastAPI/Starlette.
   - Preserve the current page-oriented URLs and workflows wherever practical.
   - Replace Flask globals, Flask-WTF forms, flash messages, redirects, and login decorators with FastAPI dependencies and signed session-cookie helpers.
   - Hash passwords with Argon2, add CSRF protection to state-changing forms, and make logout a POST action.
   - Enforce ownership/public-setting rules consistently and return proper 403/404 responses.
   - Add `/health/live` and `/health/ready`; readiness must verify database access.

8. **Separate and harden document processing**

   - Run a dedicated worker service from the same Python image.
   - Let the API stage uploads and create queued database jobs; let the worker claim jobs transactionally and process them from the shared work volume.
   - Replace PID-based API cancellation with a database cancellation request that the worker observes; the worker owns and terminates its processing child when necessary.
   - Requeue interrupted running jobs when the single Compose worker restarts.
   - Pass plain processing settings into `ProcessUnit` instead of depending on Flask application contexts or ORM globals.
   - Replace tkinter/EPS image composition with headless Pillow drawing so image generation works inside the container without a display or CWD temp files.
   - Preserve DOCX, EPUB, HTML, FB2, image insertion, and Pokémon behavior; centralize Selenium/Chromium options and always close drivers.

9. **Add containers and Compose orchestration**

   - Create a multi-stage `Dockerfile` based on Python 3.12 slim, installing the package, Chromium/runtime libraries, fonts, and a non-root application user.
   - Add `.dockerignore`.
   - Create `compose.yaml` with:
     - PostgreSQL and a persistent database volume.
     - PgBouncer using SCRAM authentication and transaction pooling.
     - A one-shot Flyway service connected directly to PostgreSQL.
     - FastAPI and worker services built from the same Dockerfile.
     - A shared processing-files volume for API and worker.
   - Do not expose PostgreSQL directly by default. API and worker must use PgBouncer; Flyway alone uses the direct PostgreSQL endpoint.
   - Gate API/worker startup on PostgreSQL/PgBouncer health and successful Flyway completion.

10. **Add environment files and documentation**

   - Add tracked root `.env.example` and an ignored local `.env` for Compose/application settings.
   - Keep the separately selected `migrations/.env.example` and ignored `migrations/.env` for manual Flyway execution.
   - Document local Python installation, environment initialization, model regeneration, manual migrations, `podman build`, Compose startup/shutdown, health checks, and clean-volume initialization.
   - Explain that generated models must not be edited manually and SQLite is used only for schema reflection/model generation.
   - Update `AGENTS.md` with the new architecture, commands, configuration, src layout, schema workflow, worker lifecycle, testing commands, and container-specific constraints.

## Public interfaces

- Preserve the server-rendered login, registration, settings, search, upload, cancellation, and download workflows.
- Add health endpoints: `GET /health/live` and `GET /health/ready`.
- Replace the ORM `Thread` concept with a `ProcessingJob` state model.
- Required runtime interfaces include `DATABASE_URL`, `SECRET_KEY`, `WORK_ROOT`, PostgreSQL/PgBouncer credentials, and documented worker/Chrome settings.
- Use `uvicorn text_evolver.main:app` for the server and `python -m text_evolver.worker` for the worker.

## Test and acceptance plan

- Apply Flyway V1 successfully to fresh PostgreSQL and SQLite databases and verify equivalent tables, columns, foreign keys, constraints, and indexes.
- Regenerate ORM models twice and confirm deterministic output.
- Test registration/password hashing, sessions, CSRF, authorization, setting CRUD/copy/search, and validation errors.
- Test job enqueue, claim, processing, completion, failure, restart recovery, cancellation, archive download, and cleanup.
- Run processing regression tests for all four supported document formats.
- Build the image with Podman and start the complete Compose stack from empty volumes.
- Confirm application SQL connections terminate at PgBouncer while Flyway connects directly to PostgreSQL.
- Smoke-test the full browser flow from registration through processed ZIP download.

## Assumptions

- This is a fresh V1 baseline; migration of existing SQLite data is not included.
- Existing HTML/CSS and user-facing functionality remain in scope; a new JSON API or frontend redesign does not.
- Python 3.12 is used for compatibility with the document, Selenium, and image-processing dependencies.
- One worker instance is the supported initial deployment; the job schema and transactional claiming should permit later scaling.
- Root and migration `.env` files contain local secrets and remain untracked; only example files are committed.
