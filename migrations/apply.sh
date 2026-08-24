#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
env_file="${script_dir}/.env"

if [[ ! -f "${env_file}" ]]; then
    echo "Missing ${env_file}; copy migrations/.env.example and set its credentials." >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
source "${env_file}"
set +a

: "${FLYWAY_URL:?FLYWAY_URL is required}"
: "${FLYWAY_USER:?FLYWAY_USER is required}"
: "${FLYWAY_PASSWORD:?FLYWAY_PASSWORD is required}"

export FLYWAY_LOCATIONS="filesystem:${script_dir}/sql/postgresql"
flyway validate
flyway migrate

