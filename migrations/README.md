# Database migrations

Flyway SQL is the only schema source of truth. Runtime deployments use the
PostgreSQL scripts; SQLite exists only to provide a disposable database for
SQLAlchemy model generation.

## Apply migrations to PostgreSQL

Install Flyway, then create the migration environment file:

```bash
cp migrations/.env.example migrations/.env
./migrations/apply.sh
```

`migrations/.env` is intentionally separate from the application `.env` and is
not committed. `FLYWAY_URL` must be a JDBC PostgreSQL URL. The script validates
all known migrations before applying pending versions.

## Generate SQLAlchemy models

Install the project development dependencies and a Flyway distribution that
includes SQLite support:

```bash
python3.12 -m pip install -e '.[dev]'
./migrations/generate_models.sh
```

The script creates a fresh temporary SQLite database, applies every SQLite
migration, and overwrites `src/text_evolver/db/models.py` using `sqlacodegen`.
It does not use or modify a developer database. Review and commit the generated
diff; do not add application behavior to that file.

`FLYWAY_COMMAND` and `SQLACODEGEN_COMMAND` may point to non-default executable
names or absolute executable paths.

## Upgrade the password cipher key version

Configure both the current and previous password keys in the root application
`.env`, then run from the repository root:

```bash
.venv/bin/python migrations/upgrade_cipher_version.py --batch-size 100
```

The script connects through the application's `DATABASE_URL`. Each batch locks
rows using the configured previous key version, decrypts them with that key,
re-encrypts them with the current key and a new unique nonce, and commits the
batch. Completed batches remain committed if a later batch fails, so rerunning
the same command resumes the upgrade. Rows already using the current version
are not changed.

Keep `PASSWORD_KEY_PREVIOUS` and `PASSWORD_KEY_PREVIOUS_VERSION` configured
until the script reports that no more records were upgraded. Do not put these
application encryption keys in `migrations/.env`; that file is only for the
Flyway connection.

## Add a migration

Add the same version and description under both `sql/postgresql/` and
`sql/sqlite/`, using dialect-specific syntax only where necessary. Then apply
both paths and regenerate the models. Never edit an already-applied migration.
