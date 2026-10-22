#!/bin/sh
# Sourced by backup-db.sh and restore-db.sh. Not run on its own.
#
# pg_connect_from_url URL [DATABASE]: reads a postgresql:// URL and exports the libpq variables PGHOST, PGPORT,
# PGUSER, PGPASSWORD, PGDATABASE and PGSSLMODE. The PostgreSQL client tools then connect without any
# connection string on their command line, so the password never appears in the process list
# (ps and /proc/<pid>/cmdline). Environment variables are readable only by the same user.
#
# DATABASE overrides the database named in the URL path. User, password and database name are percent-decoded,
# as in a URL. A raw backslash in the password must be written as %5C. The URL is never printed.

# pg_url_decode TEXT: prints TEXT with %XX escapes decoded. Plain POSIX sh, so it works where printf has no \x escapes.
pg_url_decode() {
  decoded=""
  rest=$1
  while [ -n "$rest" ]; do
    head=${rest%"${rest#?}"}
    rest=${rest#?}
    if [ "$head" = "%" ]; then
      hex=${rest%"${rest#??}"}
      case $hex in
        [0-9A-Fa-f][0-9A-Fa-f])
          octal=$(printf '%03o' "0x$hex")
          decoded="$decoded$(printf "\\$octal")"
          rest=${rest#??}
          continue
          ;;
      esac
    fi
    decoded="$decoded$head"
  done
  printf '%s' "$decoded"
}

pg_connect_from_url() {
  conn=${1#postgresql://}
  query=""
  case $conn in
    *\?*) query=${conn#*\?}; conn=${conn%%\?*} ;;
  esac
  case $conn in
    *@*) userinfo=${conn%%@*}; hostpath=${conn#*@} ;;
    *) userinfo=""; hostpath=$conn ;;
  esac
  case $userinfo in
    *:*) raw_user=${userinfo%%:*}; raw_password=${userinfo#*:} ;;
    *) raw_user=$userinfo; raw_password="" ;;
  esac
  case $hostpath in
    */*) host=${hostpath%%/*}; raw_database=${hostpath#*/} ;;
    *) host=$hostpath; raw_database="" ;;
  esac
  case $host in
    *:*) PGPORT=${host##*:}; PGHOST=${host%:*} ;;
    *) PGHOST=$host; PGPORT="" ;;
  esac
  PGUSER=$(pg_url_decode "$raw_user")
  PGPASSWORD=$(pg_url_decode "$raw_password")
  PGDATABASE=${2:-$(pg_url_decode "$raw_database")}
  PGSSLMODE=""
  case $query in
    *sslmode=*)
      sslmode_part=${query#*sslmode=}
      PGSSLMODE=${sslmode_part%%&*}
      ;;
  esac
  [ -n "$PGPORT" ] || unset PGPORT
  [ -n "$PGSSLMODE" ] || unset PGSSLMODE
  export PGHOST PGUSER PGPASSWORD PGDATABASE
  [ -z "${PGPORT:-}" ] || export PGPORT
  [ -z "${PGSSLMODE:-}" ] || export PGSSLMODE
  [ -n "$PGHOST" ] && [ -n "$PGUSER" ] && [ -n "$PGDATABASE" ] || return 1
  return 0
}
