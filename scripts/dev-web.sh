#!/bin/sh
# Web app development server on 127.0.0.1:5173. Vite proxies /api to the backend on 127.0.0.1:8000
# (start that first with scripts/dev-backend.sh). The proxy keeps the browser Origin header, so CSRF checks work.
#
# Idempotent: installs npm dependencies only when apps/web/node_modules is missing.
set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
WEB_DIR="$ROOT/apps/web"

if ! command -v npm >/dev/null 2>&1; then
  echo "dev-web: npm is required (Node 22 is used in this repository)." >&2
  exit 1
fi

cd "$WEB_DIR"
if [ ! -d node_modules ]; then
  npm ci
fi
exec npm run dev -- --host 127.0.0.1
