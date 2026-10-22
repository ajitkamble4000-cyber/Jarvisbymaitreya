# Operations: configuration, backups, and recovery

## Configuration

Every setting comes from environment variables (or `.env`). The authoritative list with defaults is
`services/api/app/core/config.py`, and `.env.example` shows the values for local development.

Settings that matter most in production:

| Variable | Production value | Why |
|---|---|---|
| `JARVIS_ENV` | `production` | Turns on secure cookies, requires an encryption key, disables API docs |
| `ENCRYPTION_KEY` | Fernet key from your secret store | Encrypts provider keys, device secrets, and OAuth tokens. Required in production |
| `COOKIE_SECURE` | `true` | Session cookies are sent only over HTTPS |
| `PUBLIC_BASE_URL` | `https://…` | Must use HTTPS in production |
| `ALLOWED_ORIGINS` | JSON list of your web origin(s) | Cross-origin unsafe requests from other origins are rejected. Wildcards are refused |
| `DATABASE_URL` | Managed PostgreSQL with TLS | Stores all application state |
| `FORWARDED_ALLOW_IPS` | The address of your reverse proxy, for example `172.30.0.10` | The API reads `X-Forwarded-For` only from these addresses. The default is `127.0.0.1`, so behind a proxy on another host, set it to the proxy's address. The proxy must replace the header, not append to it. The compose stack does this already |

Generate an encryption key with:
```sh
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

In development, when `ENCRYPTION_KEY` is empty, the backend creates `DATA_DIR/secret.key` (mode 600) and uses it.
`scripts/dev-backend.sh` writes a generated key into `.env` instead.

## Key handling (read this before you lose data)

- The encryption key protects every stored credential. **If it is lost or replaced, stored provider keys,
  device secrets, and OAuth tokens cannot be decrypted.** Devices then need re-pairing and providers need new keys.
- Back up the key separately from the database, in your secret manager. Never store it in the same backup
  as the database dump.
- Rotating the key requires decrypting and re-encrypting every secret. No rotation tool exists yet.
- Pairing codes are keyed with a key derived from `ENCRYPTION_KEY`, so changing the key also invalidates any
  unused pairing code.

## Database backups

Backups are PostgreSQL logical dumps: portable, and they include the schema. Two scripts do the work.
`services/api/tests/integration/test_backup_scripts.py` runs both against scratch databases.

Back up the database named in `DATABASE_URL` (read from `.env`, never sourced):
```sh
scripts/backup-db.sh
```

It writes to `backups/` (mode 700, git-ignored):
- `backups/jarvis-<UTC timestamp>.dump`: `pg_dump --format=custom --no-owner` output (mode 600). The
  connection is read from `DATABASE_URL` and passed to `pg_dump` as `PG*` environment variables (host, port, user,
  password, database and `sslmode`), never as a command-line argument. A process list therefore does not show the
  password. Inside the URL, percent-encode special characters in the user and password (for example `%40` for `@`
  and `%5C` for a backslash).
- `backups/jarvis-<UTC timestamp>.sha256`: the SHA-256 of the dump.

It then deletes `jarvis-*` dumps and checksum files older than `BACKUP_RETENTION_DAYS` (default 14) and no other
files. It prints the dump path and size and exits non-zero on any failure. It never prints the URL, password or key.
The dump does not contain `ENCRYPTION_KEY`; see Key handling above.

Restore into a database that already exists (for example `createdb jarvis_restore`, run as a role that may create
databases):
```sh
scripts/restore-db.sh backups/jarvis-YYYYMMDDTHHMMSSZ.dump jarvis_restore
```

- The server and credentials come from `DATABASE_URL`; only the database name changes. The restore runs in one
  transaction, so a failure leaves the target unchanged.
- When `jarvis-<timestamp>.sha256` sits next to the dump, its checksum must match, or the restore is refused.
- A target that already has tables is refused. `scripts/restore-db.sh --force <dump> <database>` restores over it:
  the objects in the dump are dropped and recreated, and the `vector` extension is kept, so the target must already have it.
- After the restore, the script runs `alembic check` against the target. If the check fails, the data is restored but
  the schema does not match the code. A dump taken before the current migrations needs `alembic upgrade head` on the
  target first.

Scripted runs can override the files and directory with `JARVIS_ENV_FILE` (read instead of `.env`), `BACKUP_DIR`
and `BACKUP_RETENTION_DAYS`.

Schedule the backup script at least daily, copy the dumps off the host (the script does not do this), and test a
restore into a scratch database before you need one.

## pgvector and semantic memory (superuser note)

Semantic memory search stores one 768-value embedding per memory in `memories.embedding`, a pgvector
`vector(768)` column. Migration `5b7e2d91c0a4` runs `CREATE EXTENSION IF NOT EXISTS vector` and then adds the column.

- **Creating the extension needs a privileged role.** The pgvector build used here (0.6.0) is not a trusted
  extension, so only a superuser can create it. A normal application role gets `permission denied to create extension "vector"`.
  Use one of these in production:
  1. Run the migrations with the owner or superuser role, so the extension is created during `alembic upgrade head`.
  2. Or, once as a superuser, run `CREATE EXTENSION IF NOT EXISTS vector;` in the application database. After that
     the extension exists, and the application role needs no extra privilege for later migrations.
- Check it is installed: `SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';`
- Restores: `scripts/restore-db.sh` needs the extension to be creatable by its role, or already installed in the target.
  A non-superuser role therefore restores into a database that already has `vector` (the test suite uses the
  `jarvis_vector_template` database for this). `--force` never drops the extension.
- Settings: `GEMINI_EMBEDDING_MODEL` (unset means semantic search is off and reports `no_embedding_model`).
  `EMBEDDING_DIMENSIONS` must stay `768` to match the column.
- Rollback: `alembic downgrade -1` drops the `embedding` column and keeps the extension installed, because other objects may use it.
- Existing memories have no embedding until `POST /api/v1/memories/reindex` embeds them (200 at a time).

## Recovery checklist

1. Restore the newest database dump into a fresh database with `scripts/restore-db.sh` (see Database backups).
2. Restore `ENCRYPTION_KEY` from the secret manager. Do not regenerate it: the restored secrets need the original key.
3. Start the backend with the restored `DATABASE_URL` and `ENCRYPTION_KEY`. Check `GET /api/v1/health`
   reports `"database": "ok"`.
4. Sign in and check *AI Models*: a provider that shows an error after restore usually means its key was not
   restored with the database.
5. Desktop agents keep their own secret. If the server was rebuilt from an older dump, the agent may show
   *re-pair required*: create a new pairing code and pair again.
6. Pending approvals are not re-sent. Tasks that were `running` when the backup was taken show as failed or
   timed out after the restore, and must be retried by the user.

## Monitoring

- `GET /api/v1/health/live` returns `200` while the process is up.
- `GET /api/v1/health` also checks the database and reports `"status": "degraded"` when it is unreachable.
- Logs are JSON lines on stdout. Secrets are redacted before they are written. The Developer Console reads the
  last 1000 lines from memory (`GET /api/v1/developer/logs`).
- Audit events (sign-ins, credential changes, approvals, dispatches, results) are in the `audit_events` table
  and on the Activity page.
- Analytics come from measured rows only. Costs show as unavailable: no pricing table is configured.
- Metrics are described in [docs/phase3/metrics.md](phase3/metrics.md).

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request. Its token is read-only, it uses no secrets, and it
has no deployment step. Each job runs on `ubuntu-24.04`:

| Job | What it does |
|---|---|
| `backend` | Starts a `pgvector/pgvector:pg16` service, creates the `jarvis_vector_template` database, installs `services/api[dev]` on Python 3.13, checks that `alembic heads` prints exactly one head, and runs `pytest -q` (including the backup script tests). |
| `desktop` | Node 22, `npm ci` with `ELECTRON_SKIP_BINARY_DOWNLOAD=1`, then typecheck, test and build in `apps/desktop`. |
| `web` | Node 22, `npm ci`, then typecheck, test and build in `apps/web`. |
| `e2e` | Runs after the three jobs above. Uses the same database service, builds the web app, installs Playwright Chromium and runs the milestone test in `tests/e2e`. |

## Scaling and what does not scale yet

- The device hub, event stream, and rate limiter live in process memory. Run **one API process** until
  connection registries and rate limits move to a shared store.
- Background work (scheduler, task sweeps, heartbeat monitor) runs inside each API process when
  `RUN_BACKGROUND_WORKERS=true`. Run one API process with workers, and others with it set to `false`,
  or the same scheduled job can be claimed twice (row locks reduce this, but do not remove the need).
- Command replay protection is in memory on the desktop agent; see the README limitations.
