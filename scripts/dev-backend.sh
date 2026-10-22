#!/bin/sh
# Local API for development, served on 127.0.0.1:8000 (override with JARVIS_API_HOST / JARVIS_API_PORT).
#
# Prerequisites: PostgreSQL reachable at the host and port in DATABASE_URL, with the database created,
# and the backend venv at services/api/.venv (python3.13 -m venv, then pip install -e "services/api[dev]").
#
# Idempotent: on first run it creates .env from .env.example (mode 600). It only fills values that are
# still empty or still CHANGE_ME, so existing secrets are never replaced. Then it applies migrations and serves.
# Secrets are never printed.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
API_DIR="$ROOT/services/api"
VENV_BIN="$API_DIR/.venv/bin"
ENV_FILE="$ROOT/.env"
HOST="${JARVIS_API_HOST:-127.0.0.1}"
PORT="${JARVIS_API_PORT:-8000}"

if [ ! -x "$VENV_BIN/python" ]; then
  echo "dev-backend: $VENV_BIN/python not found. Create the venv first (see the header of this script)." >&2
  exit 1
fi

if [ ! -f "$ENV_FILE" ]; then
  cp "$ROOT/.env.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "dev-backend: created .env from .env.example"
fi

# set_env_value KEY VALUE: writes VALUE only when KEY is present and still empty.
set_env_value() {
  if grep -q "^$1=\$" "$ENV_FILE"; then
    tmp="$ENV_FILE.tmp.$$"
    sed "s|^$1=\$|$1=$2|" "$ENV_FILE" > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$ENV_FILE"
    echo "dev-backend: generated $1 in .env"
  fi
}

# Replace the placeholder password with the local development password (same default as docker-compose).
if grep -q 'CHANGE_ME' "$ENV_FILE"; then
  tmp="$ENV_FILE.tmp.$$"
  sed 's|CHANGE_ME|jarvis|g' "$ENV_FILE" > "$tmp"
  chmod 600 "$tmp"
  mv "$tmp" "$ENV_FILE"
  echo "dev-backend: set the local development database password in .env"
fi

if grep -q '^ENCRYPTION_KEY=$' "$ENV_FILE"; then
  key=$("$VENV_BIN/python" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())')
  set_env_value ENCRYPTION_KEY "$key"
  unset key
fi

# Export every KEY=VALUE line of .env as a literal. The file is not sourced by the shell, because shell
# quote removal would break JSON values such as ALLOWED_ORIGINS=["http://localhost:5173"].
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in
    ''|'#'*) continue ;;
  esac
  key=${line%%=*}
  case "$key" in
    [A-Za-z_]*[!A-Za-z0-9_]*) continue ;;
    [A-Za-z_]*) ;;
    *) continue ;;
  esac
  # Variables already set in the environment win over .env, as they do for the settings loader.
  if eval "[ -n \"\${$key+set}\" ]"; then
    continue
  fi
  export "$key=${line#*=}"
done < "$ENV_FILE"
DATA_DIR="${DATA_DIR:-./data}"
case "$DATA_DIR" in
  ./*) DATA_DIR="$ROOT/${DATA_DIR#./}" ;;
esac
export DATA_DIR

cd "$API_DIR"
"$VENV_BIN/alembic" upgrade head
exec "$VENV_BIN/uvicorn" app.main:create_app --factory --host "$HOST" --port "$PORT"
