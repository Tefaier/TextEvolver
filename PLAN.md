# S3 User Image Migration Plan

## Scope and decisions

- Store user-provided setting images in an S3-compatible object store through
  `boto3`; do not store Base64 image payloads in PostgreSQL.
- Run MinIO as a dedicated Podman/Compose container with its own named
  `user-data` volume mounted at `/data`.
- The application and worker access MinIO only through its S3 API and never
  mount `user-data`.
- Download a job's images into `TEMP_ROOT/jobs/<job_id>/images` when the worker
  starts that job. Document-processing children use only those local files.
- This remains a fresh V1 schema baseline. Migrating data from an already
  deployed Base64 schema is outside this change.
- Do not add or run an end-to-end MinIO smoke test.

## Step-by-step implementation

1. **Add S3 dependencies and configuration**

   - Add `boto3` as a runtime dependency.
   - Add settings for the endpoint URL, bucket, region, access key, secret key,
     and path-style addressing.
   - Use `http://localhost:9000` for processes outside containers and
     `http://minio:9000` inside Compose.

   Validation:

   - Test valid and invalid configuration, required values, and secret handling.
   - Run Ruff over configuration and tests.

2. **Introduce an S3 image-storage service**

   - Add an injectable wrapper around the boto3 client.
   - Support streaming upload, download to a `Path`, server-side copy, health
     checks, and idempotent batch deletion.
   - Generate immutable object keys under user/setting prefixes and translate
     boto3 errors into application-level storage errors without leaking secrets.

   Validation:

   - Mock the boto3 client and cover each operation and error path.
   - Verify streams are rewound and downloads create only the requested parent
     directory.

3. **Replace Base64 database storage**

   - Remove `image_conversion.images`.
   - Add `image_conversion_file` with an image-conversion foreign key, unique
     object key, byte size, and deterministic position.
   - Add cascade deletion and matching PostgreSQL and SQLite constraints and
     indexes.
   - Regenerate the checked-in SQLAlchemy models.

   Validation:

   - Apply both V1 migrations to fresh databases and run schema-parity tests.
   - Run model generation twice and confirm the second run is deterministic.
   - Review all generated relationships and constraints.

4. **Refactor setting image updates**

   - Preserve stored image references when a file input is left empty.
   - Stream new images to S3 and enforce `SETTING_LIMIT_BYTES` from upload sizes
     and persisted object sizes.
   - Update image-conversion metadata in place, replace file rows only when new
     files are submitted, and remove objects belonging to deleted rows.
   - Delete newly uploaded objects on database rollback and delete superseded
     objects only after a successful commit.

   Validation:

   - Cover unchanged, added, replaced, removed, multiple, oversized, failed
     upload, and database-rollback cases.
   - Assert old objects are deleted only after their references are removed.

5. **Update setting copy and deletion**

   - Copy every source object to a new immutable key with S3 server-side copy so
     copied settings are independent.
   - Delete every owned object when a conversion or setting is removed.
   - Retain the existing rejection for settings referenced by processing jobs.

   Validation:

   - Verify source and copied keys differ and either setting can be changed or
     deleted independently.
   - Verify failed copies roll back database rows and newly copied objects.

6. **Stage images when a job starts**

   - Create `TEMP_ROOT/jobs/<job_id>/images` for the claimed job.
   - Read ordered object references and download each object into that directory.
   - Put local image paths in `ProcessingConfiguration`.
   - Coordinate setting reads and writes so an object cannot be deleted while a
     worker is staging it.

   Validation:

   - Test staging layout, ordering, multiple images, missing-object failure,
     concurrent job isolation, and replacement/download coordination.

7. **Process staged local images and clean them up**

   - Make `ProcessUnit` and image composition open local staged files.
   - Ensure processing children never construct an S3 client.
   - Remove the job staging directory on success, failure, cancellation,
     configuration failure, and worker startup recovery without touching the
     Pokémon cache.

   Validation:

   - Cover each worker exit path, startup cleanup, concurrent job isolation, and
     image insertion for all supported document formats.

8. **Run MinIO in a dedicated Podman container**

   - Add a MinIO service and one-shot bucket initializer to `compose.yaml`.
   - Mount only `user-data:/data` into MinIO.
   - Expose the configured S3 and console ports and make app/worker startup depend
     on MinIO health and successful bucket initialization.

   Validation:

   - Run `podman compose config`.
   - Confirm only MinIO mounts `user-data` and app/worker use
     `http://minio:9000`.
   - Build the application image with Podman.
   - Do not add or run an end-to-end MinIO smoke test.

9. **Update environment and documentation**

   - Update `.env.example`, `README.md`, and `AGENTS.md` with S3 variables,
     internal/external endpoints, Podman startup, volume persistence, and cleanup.
   - State that `podman compose down --volumes` permanently deletes `user-data`.

   Validation:

   - Cross-check every documented variable against `AppSettings` and Compose.
   - Confirm no real credentials are committed.

10. **Run final automated validation**

    ```bash
    ./migrations/generate_models.sh
    .venv/bin/pytest tests/test_migrations.py
    .venv/bin/pytest tests/test_services.py tests/test_worker.py tests/test_processing.py tests/test_web.py
    .venv/bin/pytest
    .venv/bin/ruff check src tests
    podman compose config
    podman build -t text-evolver:local .
    ```

    Review the final diff and report any validation that could not be completed.
