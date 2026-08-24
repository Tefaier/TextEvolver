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

## Add a migration

Add the same version and description under both `sql/postgresql/` and
`sql/sqlite/`, using dialect-specific syntax only where necessary. Then apply
both paths and regenerate the models. Never edit an already-applied migration.

