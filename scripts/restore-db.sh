#!/bin/sh
# Restores a dump written by scripts/backup-db.sh into an existing target database on the server named in
# DATABASE_URL (read from .env), then runs `alembic check` against the target. The target must already exist.
# Usage: scripts/restore-db.sh [--force] <dump file> <target database>
#
# The target must have no tables unless --force is given. With --force, pg_restore runs with --clean --if-exists,
# so the objects in the dump are dropped and recreated; other objects in the target are left alone. The vector
# extension is never dropped or created with --force, so the target must already have it. The restore runs in
# one transaction, so a failure leaves the target unchanged.
#
# If <dump>.sha256 sits next to the dump (the name with .dump replaced by .sha256), its checksum must match.
# Optional overrides: JARVIS_ENV_FILE (default <repo>/.env). JARVIS_ENV is taken from the environment, or from
# the env file when unset. Secrets, the URL and the password are never printed.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=${JARVIS_ENV_FILE:-$ROOT/.env}

fail() {
  echo "restore-db: $*" >&2
  exit 1
}

usage() {
  echo "usage: restore-db.sh [--force] <dump file> <target database>" >&2
  exit 2
}

# env_value KEY: prints the last value assigned to KEY in ENV_FILE. The file is read as text, never sourced.
env_value() {
  awk -v key="$1" '
    index($0, key "=") == 1 { value = substr($0, length(key) + 2) }
    END { sub(/\r$/, "", value); print value }
  ' "$ENV_FILE"
}

if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum "$1" | awk '{ print $1 }'; }
elif command -v shasum >/dev/null 2>&1; then
  sha256() { shasum -a 256 "$1" | awk '{ print $1 }'; }
else
  fail "sha256sum or shasum is required"
fi

force=0
while [ $# -gt 0 ]; do
  case $1 in
    --force) force=1; shift ;;
    -*) usage ;;
    *) break ;;
  esac
done
[ $# -eq 2 ] || usage
dump=$1
target=$2

[ -f "$dump" ] || fail "dump file not found: $dump"
case $target in
  ''|*[!A-Za-z0-9_]*) fail "target database name may contain only letters, digits and underscores" ;;
esac

# Checksum first: a dump that does not match its .sha256 file is never restored.
checksum="${dump%.dump}.sha256"
if [ -f "$checksum" ]; then
  expected=$(awk 'NR == 1 { print $1 }' "$checksum")
  case $expected in
    *[!0-9a-f]*|'') fail "malformed checksum file: $checksum" ;;
  esac
  [ "${#expected}" -eq 64 ] || fail "malformed checksum file: $checksum"
  [ "$(sha256 "$dump")" = "$expected" ] || fail "checksum mismatch for $dump; refusing to restore"
  echo "restore-db: checksum verified"
else
  echo "restore-db: no checksum file next to the dump; checksum not verified" >&2
fi

command -v pg_restore >/dev/null 2>&1 || fail "pg_restore not found on PATH (install the PostgreSQL client tools)"
command -v psql >/dev/null 2>&1 || fail "psql not found on PATH (install the PostgreSQL client tools)"
if [ -x "$ROOT/services/api/.venv/bin/alembic" ]; then
  alembic="$ROOT/services/api/.venv/bin/alembic"
elif command -v alembic >/dev/null 2>&1; then
  alembic=$(command -v alembic)
else
  fail "alembic not found: create services/api/.venv or put alembic on PATH"
fi

[ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"
database_url=$(env_value DATABASE_URL)
case $database_url in
  '"'*'"' | "'"*"'") database_url=${database_url#?}; database_url=${database_url%?} ;;
esac
case $database_url in
  postgresql+asyncpg://?*) rest=${database_url#postgresql+asyncpg://} ;;
  postgresql://?*) rest=${database_url#postgresql://} ;;
  *) fail "DATABASE_URL in the env file must start with postgresql:// or postgresql+asyncpg://" ;;
esac
database_url=""

# Same server, credentials and query options as DATABASE_URL; only the database name changes.
base=${rest%%\?*}
query=""
case $rest in
  *\?*) query="?${rest#*\?}" ;;
esac
server=${base%/*}
async_target="postgresql+asyncpg://$server/$target$query"
# The PostgreSQL client tools get the target through PG* variables, so the password stays off the command line.
. "$ROOT/scripts/pg-connection.sh"
pg_connect_from_url "postgresql://$server/$target$query" "$target" || fail "DATABASE_URL must name a host, a user and a database"
rest=""

tables=$(psql -X -A -t -v ON_ERROR_STOP=1 -c \
  "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname NOT IN ('pg_catalog', 'information_schema')") \
  || fail "cannot connect to target database \"$target\"; create it first (it must exist and be readable by the role)"
case $tables in
  ''|*[!0-9]*) fail "unexpected answer from the target database" ;;
esac
if [ "$tables" -gt 0 ] && [ "$force" -eq 0 ]; then
  fail "target database \"$target\" already has $tables table(s); refusing to restore. Use --force to overwrite the objects in the dump."
fi

# The dump's table of contents is filtered before the restore. Skipped: the COMMENT ON EXTENSION entry, which
# only the extension owner may set; and with --force the EXTENSION entry, because --clean would DROP the
# extension, which only its owner may do. With --force the target must already have the vector extension.
# Every other entry is restored, in dump order.
toc=$(mktemp "${TMPDIR:-/tmp}/restore-db.XXXXXX") || fail "cannot create a temporary file"
filtered=$(mktemp "${TMPDIR:-/tmp}/restore-db.XXXXXX") || fail "cannot create a temporary file"
trap 'rm -f "$toc" "$filtered"' EXIT
pg_restore --list "$dump" > "$toc" || fail "pg_restore cannot read $dump"
awk -v force="$force" '
  / COMMENT - EXTENSION / || (force == 1 && / EXTENSION - /) { print ";" $0; next }
  { print }
' "$toc" > "$filtered"

if [ "$force" -eq 1 ]; then
  pg_restore --no-owner --exit-on-error --single-transaction --clean --if-exists \
    --use-list="$filtered" --dbname="$target" "$dump" \
    || fail "pg_restore failed; the transaction was rolled back and \"$target\" is unchanged"
else
  pg_restore --no-owner --exit-on-error --single-transaction \
    --use-list="$filtered" --dbname="$target" "$dump" \
    || fail "pg_restore failed; the transaction was rolled back and \"$target\" is unchanged"
fi
echo "restore-db: restored $dump into database \"$target\""

# JARVIS_ENV selects the validation rules in Settings. An unset value defaults to production, as the backend does.
if [ -z "${JARVIS_ENV:-}" ]; then
  jarvis_env=$(env_value JARVIS_ENV)
  [ -z "$jarvis_env" ] || export JARVIS_ENV="$jarvis_env"
fi
if (cd "$ROOT/services/api" && DATABASE_URL="$async_target" "$alembic" check); then
  echo "restore-db: alembic check passed; the restored schema matches the models"
else
  fail "alembic check failed for \"$target\" (output above). The data is restored. If the dump predates the current migrations, run alembic upgrade head against it; otherwise the schema differs from the models."
fi
