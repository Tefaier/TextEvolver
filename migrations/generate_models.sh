#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repository_dir="$(cd -- "${script_dir}/.." && pwd)"
temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/text-evolver-models.XXXXXX")"
database_path="${temporary_dir}/schema.db"
trap 'rm -rf -- "${temporary_dir}"' EXIT

export FLYWAY_URL="jdbc:sqlite:${database_path}"
export FLYWAY_LOCATIONS="filesystem:${script_dir}/sql/sqlite"
export FLYWAY_USER=""
export FLYWAY_PASSWORD=""

flyway migrate

sqlacodegen \
    "sqlite:///${database_path}" \
    --generator declarative \
    --tables user_account,user_password,setting,fandom,unit_conversion,phrase_conversion,image_conversion,image_conversion_file,processing_job \
    --outfile "${repository_dir}/src/text_evolver/db/models.py"

echo "Generated src/text_evolver/db/models.py from a fresh Flyway SQLite schema."
