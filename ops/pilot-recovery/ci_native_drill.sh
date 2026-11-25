#!/bin/sh
# Synthetic in-container PostgreSQL only; no cloud credentials or network target.
set -eu
export CL_APP_ENV=test
export CL_DATABASE_URL='sqlite:///:memory:'
export CL_BACKUP_TEST_ADMIN_URL='postgresql://pilot_backup_test@127.0.0.1:5432/postgres'
export CL_BACKUP_TEST_PG_BIN=/usr/lib/postgresql/16/bin
export CL_BACKUP_TEST_AGE_BIN=/opt/pilot-age/age
test_state=$(mktemp -d /tmp/cl-ci-pg.XXXXXX)
cleanup() {
  /usr/lib/postgresql/16/bin/pg_ctl -D "$test_state/data" stop -m fast >/dev/null 2>&1 || true
  rm -rf "$test_state"
}
trap cleanup EXIT INT TERM
/usr/lib/postgresql/16/bin/initdb -D "$test_state/data" --username=pilot_backup_test --auth=trust --no-locale --encoding=UTF8 >/dev/null
/usr/lib/postgresql/16/bin/pg_ctl -D "$test_state/data" -l "$test_state/postgres.log" -o '-h 127.0.0.1 -p 5432 -k /tmp' -w start >/dev/null
python -m pytest -q -p no:cacheprovider tests/test_pilot_backup.py tests/test_pilot_backup_native.py tests/test_release_bootstrap.py
