#!/bin/sh
# Writes a PostgreSQL custom-format dump of the database named in DATABASE_URL (read from .env), a SHA-256
# file next to it, and prunes older dumps. Usage: scripts/backup-db.sh
#
# Optional overrides: JARVIS_ENV_FILE (default <repo>/.env), BACKUP_DIR (default <repo>/backups),
# BACKUP_RETENTION_DAYS (default 14). Files are mode 600 and the directory is mode 700.
# The dump does not contain ENCRYPTION_KEY. Back that key up separately (docs/operations.md).
# The URL, password and key are never printed.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
ENV_FILE=${JARVIS_ENV_FILE:-$ROOT/.env}
BACKUP_DIR=${BACKUP_DIR:-$ROOT/backups}
RETENTION_DAYS=${BACKUP_RETENTION_DAYS:-14}

fail() {
  echo "backup-db: $*" >&2
  exit 1
}

# env_value KEY: prints the last value assigned to KEY in ENV_FILE. The file is read as text, never sourced.
env_value() {
  awk -v key="$1" '
    index($0, key "=") == 1 { value = substr($0, length(key) + 2) }
    END { sub(/\r$/, "", value); print value }
  ' "$ENV_FILE"
}

case $RETENTION_DAYS in
  ''|*[!0-9]*) fail "BACKUP_RETENTION_DAYS must be a whole number of days" ;;
esac
command -v pg_dump >/dev/null 2>&1 || fail "pg_dump not found on PATH (install the PostgreSQL client tools)"
if command -v sha256sum >/dev/null 2>&1; then
  sha256() { sha256sum "$1" | awk '{ print $1 }'; }
elif command -v shasum >/dev/null 2>&1; then
  sha256() { shasum -a 256 "$1" | awk '{ print $1 }'; }
else
  fail "sha256sum or shasum is required"
fi

[ -f "$ENV_FILE" ] || fail "env file not found: $ENV_FILE"
database_url=$(env_value DATABASE_URL)
case $database_url in
  '"'*'"' | "'"*"'") database_url=${database_url#?}; database_url=${database_url%?} ;;
esac
case $database_url in
  postgresql+asyncpg://?*) pg_url="postgresql://${database_url#postgresql+asyncpg://}" ;;
  postgresql://?*) pg_url=$database_url ;;
  *) fail "DATABASE_URL in the env file must start with postgresql:// or postgresql+asyncpg://" ;;
esac
database_url=""
. "$ROOT/scripts/pg-connection.sh"
# The database name comes from the URL path. A URL without a host, user or database is refused before any dump starts.
pg_connect_from_url "$pg_url" || fail "DATABASE_URL must name a host, a user and a database"
pg_url=""

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

name="jarvis-$(date -u +%Y%m%dT%H%M%SZ)"
dump="$BACKUP_DIR/$name.dump"
checksum="$BACKUP_DIR/$name.sha256"
partial="$dump.partial"
trap 'rm -f "$partial"' EXIT
[ ! -e "$dump" ] || fail "$dump already exists; retry in a second"

pg_dump --format=custom --no-owner --file="$partial" \
  || fail "pg_dump failed; no dump was kept"
mv "$partial" "$dump"
printf '%s  %s\n' "$(sha256 "$dump")" "$name.dump" > "$checksum"

find "$BACKUP_DIR" -maxdepth 1 -type f \
  \( -name 'jarvis-[0-9]*T[0-9]*Z.dump' -o -name 'jarvis-[0-9]*T[0-9]*Z.sha256' \) \
  -mmin "+$((RETENTION_DAYS * 1440))" ! -name "$name.dump" ! -name "$name.sha256" \
  -exec rm -f {} +

size=$(wc -c < "$dump" | tr -d ' ')
echo "backup-db: wrote $dump"
echo "backup-db: size $size bytes; checksum in $checksum"
echo "backup-db: pruned dumps older than $RETENTION_DAYS day(s) from $BACKUP_DIR"
echo "backup-db: ENCRYPTION_KEY is NOT in this dump. Back it up separately, in your secret manager."
