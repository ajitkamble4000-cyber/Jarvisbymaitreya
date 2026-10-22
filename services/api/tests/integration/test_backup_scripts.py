"""scripts/backup-db.sh and scripts/restore-db.sh, run against scratch databases.

The scratch databases jarvis_ops_scratch and jarvis_ops_restore are created from jarvis_vector_template (pgvector
preinstalled) and dropped again. The role needs CREATEDB. The scripts read DATABASE_URL from a temporary env file
(JARVIS_ENV_FILE) and write dumps to a temporary directory (BACKUP_DIR), so the repository is never touched.
Tests are skipped, with the reason, when a PostgreSQL client tool is missing or the scratch databases cannot be created.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine.url import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401  - registers every table on Base.metadata
from app.database.base import Base

REPO_ROOT = Path(__file__).resolve().parents[4]
API_ROOT = Path(__file__).resolve().parents[2]
BACKUP_SCRIPT = REPO_ROOT / "scripts" / "backup-db.sh"
RESTORE_SCRIPT = REPO_ROOT / "scripts" / "restore-db.sh"
SCRATCH_DB = "jarvis_ops_scratch"
RESTORE_DB = "jarvis_ops_restore"
TEST_DATABASE_URL = os.environ.get(
    "JARVIS_TEST_DATABASE_URL",
    "postgresql+asyncpg://jarvis:jarvis@127.0.0.1:5432/jarvis_test",
)
ENCRYPTION_KEY_VALUE = "ops-test-encryption-key-must-not-leak"
LOCK_FILE = Path(tempfile.gettempdir()) / "jarvis-backup-scripts-tests.lock"
DUMP_NAME = re.compile(r"^jarvis-\d{8}T\d{6}Z\.dump$")

_MISSING_TOOLS = [tool for tool in ("pg_dump", "pg_restore", "psql") if shutil.which(tool) is None]
if shutil.which("sha256sum") is None and shutil.which("shasum") is None:
    _MISSING_TOOLS.append("sha256sum or shasum")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        bool(_MISSING_TOOLS),
        reason=f"PostgreSQL client tools not installed: {', '.join(_MISSING_TOOLS)}",
    ),
]


def _admin_url() -> URL:
    return make_url(TEST_DATABASE_URL).set(database="postgres")


def _db_url(name: str) -> URL:
    return make_url(TEST_DATABASE_URL).set(database=name)


async def _admin_exec(sql: str) -> None:
    engine = create_async_engine(_admin_url(), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(sql)
    finally:
        await engine.dispose()


async def _query(url: URL, sql: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            result = await conn.exec_driver_sql(sql)
            return [tuple(row) for row in result.all()]
    finally:
        await engine.dispose()


async def _create_schema(url: URL) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


def _alembic_config(url: URL) -> Config:
    # No ini file, so env.py does not reset the application's loggers.
    config = Config()
    config.set_main_option("script_location", str(API_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url.render_as_string(hide_password=False).replace("%", "%%"))
    return config


def _tables(url: URL) -> list[str]:
    rows = asyncio.run(_query(url, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1"))
    return [str(row[0]) for row in rows]


def _write_env_file(path: Path) -> Path:
    url = _db_url(SCRATCH_DB).render_as_string(hide_password=False)
    path.write_text(
        f"JARVIS_ENV=test\nDATABASE_URL={url}\nENCRYPTION_KEY={ENCRYPTION_KEY_VALUE}\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _create_scratch_databases() -> None:
    # jarvis_ops_scratch holds the ORM schema, stamped at the current heads; jarvis_ops_restore starts empty.
    for name in (SCRATCH_DB, RESTORE_DB):
        asyncio.run(_admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        asyncio.run(_admin_exec(f'CREATE DATABASE "{name}" TEMPLATE jarvis_vector_template'))
    asyncio.run(_create_schema(_db_url(SCRATCH_DB)))
    command.stamp(_alembic_config(_db_url(SCRATCH_DB)), "heads")


def _drop_scratch_databases() -> None:
    for name in (SCRATCH_DB, RESTORE_DB):
        asyncio.run(_admin_exec(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


def _skip_reason() -> str | None:
    rows = asyncio.run(
        _query(
            _admin_url(),
            "SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'jarvis_vector_template'), "
            "COALESCE((SELECT rolcreatedb OR rolsuper FROM pg_roles WHERE rolname = current_user), false)",
        )
    )
    template_exists, may_create = rows[0]
    if not template_exists:
        return "the template database jarvis_vector_template does not exist (see README: pgvector setup)"
    if not may_create:
        return "the database role may not create databases (CREATEDB is required)"
    return None


@pytest.fixture
def scratch_env(tmp_path: Path) -> Iterator[Path]:
    # The database names are fixed, so two test runs on one host would drop each other's databases. The lock
    # makes concurrent runs take turns.
    with LOCK_FILE.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            reason = _skip_reason()
            if reason is not None:
                pytest.skip(reason)
            _create_scratch_databases()
            yield _write_env_file(tmp_path / "scratch.env")
        finally:
            _drop_scratch_databases()
            fcntl.flock(lock, fcntl.LOCK_UN)


def _run(script: Path, args: list[str], env_file: Path, backup_dir: Path) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key not in ("BACKUP_DIR", "BACKUP_RETENTION_DAYS")}
    # The alembic check finds alembic next to the test interpreter when services/api/.venv does not exist.
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    env["JARVIS_ENV_FILE"] = str(env_file)
    env["BACKUP_DIR"] = str(backup_dir)
    return subprocess.run(
        ["/bin/sh", str(script), *args],
        capture_output=True,
        text=True,
        env=env,
        check=False,
        timeout=600,
    )


def _backup(env_file: Path, backup_dir: Path) -> subprocess.CompletedProcess[str]:
    result = _run(BACKUP_SCRIPT, [], env_file, backup_dir)
    assert result.returncode == 0, result.stderr
    return result


def _dump_in(backup_dir: Path) -> Path:
    dumps = sorted(path for path in backup_dir.iterdir() if DUMP_NAME.match(path.name))
    assert dumps, "no dump was written"
    return dumps[-1]


def _assert_no_secrets(result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    assert ENCRYPTION_KEY_VALUE not in output
    assert "jarvis:jarvis@" not in output
    assert _db_url(SCRATCH_DB).render_as_string(hide_password=False) not in output


def test_backup_writes_checksummed_dump_and_prunes_only_old_dump_files(scratch_env: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    old_time = time.time() - 30 * 86400
    recent_time = time.time() - 2 * 86400
    for stem, when in (("jarvis-20000101T000000Z", old_time), ("jarvis-20000102T000000Z", recent_time)):
        (backup_dir / f"{stem}.dump").write_bytes(b"old dump")
        (backup_dir / f"{stem}.sha256").write_text("00  x\n", encoding="utf-8")
        for suffix in (".dump", ".sha256"):
            os.utime(backup_dir / f"{stem}{suffix}", (when, when))
    (backup_dir / "notes.txt").write_text("not a dump", encoding="utf-8")
    os.utime(backup_dir / "notes.txt", (old_time, old_time))

    result = _backup(scratch_env, backup_dir)

    _assert_no_secrets(result)
    assert "backup-db: wrote" in result.stdout
    assert "ENCRYPTION_KEY is NOT in this dump" in result.stdout
    dump = _dump_in(backup_dir)
    assert "jarvis-20000101T000000Z.dump" not in {path.name for path in backup_dir.iterdir()}
    assert not (backup_dir / "jarvis-20000101T000000Z.sha256").exists()
    assert (backup_dir / "jarvis-20000102T000000Z.dump").exists()
    assert (backup_dir / "notes.txt").exists()
    expected_digest = hashlib.sha256(dump.read_bytes()).hexdigest()
    checksum_line = (backup_dir / f"{dump.stem}.sha256").read_text(encoding="utf-8").split()
    assert checksum_line == [expected_digest, dump.name]
    assert stat.S_IMODE(backup_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(dump.stat().st_mode) == 0o600
    listed = subprocess.run(["pg_restore", "--list", str(dump)], capture_output=True, text=True, check=False)
    assert listed.returncode == 0, listed.stderr


def test_restore_into_empty_database_matches_source_and_passes_alembic_check(scratch_env: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _backup(scratch_env, backup_dir)
    dump = _dump_in(backup_dir)

    result = _run(RESTORE_SCRIPT, [str(dump), RESTORE_DB], scratch_env, backup_dir)

    _assert_no_secrets(result)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "alembic check passed" in result.stdout
    assert _tables(_db_url(RESTORE_DB)) == _tables(_db_url(SCRATCH_DB))
    assert _tables(_db_url(RESTORE_DB))  # the source schema is not empty
    versions = "SELECT version_num FROM alembic_version ORDER BY 1"
    source_versions = asyncio.run(_query(_db_url(SCRATCH_DB), versions))
    assert asyncio.run(_query(_db_url(RESTORE_DB), versions)) == source_versions
    assert source_versions


def test_restore_refuses_non_empty_target_without_force(scratch_env: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _backup(scratch_env, backup_dir)
    dump = _dump_in(backup_dir)
    first = _run(RESTORE_SCRIPT, [str(dump), RESTORE_DB], scratch_env, backup_dir)
    assert first.returncode == 0, first.stdout + first.stderr
    tables_after_first = _tables(_db_url(RESTORE_DB))

    second = _run(RESTORE_SCRIPT, [str(dump), RESTORE_DB], scratch_env, backup_dir)

    _assert_no_secrets(second)
    assert second.returncode != 0
    assert "--force" in second.stderr
    assert _tables(_db_url(RESTORE_DB)) == tables_after_first


def test_restore_force_replaces_objects_in_populated_target(scratch_env: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _backup(scratch_env, backup_dir)
    dump = _dump_in(backup_dir)
    assert _run(RESTORE_SCRIPT, [str(dump), RESTORE_DB], scratch_env, backup_dir).returncode == 0

    forced = _run(RESTORE_SCRIPT, ["--force", str(dump), RESTORE_DB], scratch_env, backup_dir)

    assert forced.returncode == 0, forced.stdout + forced.stderr
    assert "alembic check passed" in forced.stdout
    assert _tables(_db_url(RESTORE_DB)) == _tables(_db_url(SCRATCH_DB))


def test_restore_refuses_dump_that_does_not_match_its_checksum(scratch_env: Path, tmp_path: Path) -> None:
    backup_dir = tmp_path / "backups"
    _backup(scratch_env, backup_dir)
    dump = _dump_in(backup_dir)
    checksum = backup_dir / f"{dump.stem}.sha256"
    checksum.write_text(f"{'0' * 64}  {dump.name}\n", encoding="utf-8")

    result = _run(RESTORE_SCRIPT, [str(dump), RESTORE_DB], scratch_env, backup_dir)

    _assert_no_secrets(result)
    assert result.returncode != 0
    assert "checksum mismatch" in result.stderr
    assert _tables(_db_url(RESTORE_DB)) == []


def test_backup_keeps_the_password_out_of_pg_dump_arguments(scratch_env: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A process list shows arguments to every local user, so the URL and password must travel as PG* variables.
    real_pg_dump = shutil.which("pg_dump")
    assert real_pg_dump is not None
    wrapper_dir = tmp_path / "bin"
    wrapper_dir.mkdir()
    arguments_file = tmp_path / "argv.txt"
    variables_file = tmp_path / "env.txt"
    wrapper = wrapper_dir / "pg_dump"
    wrapper.write_text(
        "#!/bin/sh\n"
        + f'printf "%s\\n" "$@" > "{arguments_file}"\n'
        + f'env | grep "^PG" | cut -d= -f1 > "{variables_file}"\n'
        + f'exec "{real_pg_dump}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    _backup(scratch_env, backup_dir)

    arguments = arguments_file.read_text(encoding="utf-8")
    assert "postgresql" not in arguments
    assert "jarvis:jarvis" not in arguments
    assert "@127.0.0.1" not in arguments
    variables = set(variables_file.read_text(encoding="utf-8").split())
    assert {"PGHOST", "PGUSER", "PGPASSWORD", "PGDATABASE"} <= variables
