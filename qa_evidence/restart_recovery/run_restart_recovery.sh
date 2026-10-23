#!/usr/bin/env bash
# Real process restart recovery test (local, disposable DB only).
# Requires: CL_DATABASE_URL pointing at an EMPTY disposable PostgreSQL DB already
# migrated with `alembic upgrade head`, and PGURL (same DB, libpq form) for psql.
# Never point this at staging or production.
set -u
P=${PY_BIN:-python}; UV=${UVICORN_BIN:-uvicorn}
rows(){ echo "orders rows: $(psql "$PGURL" -tAc 'select count(*) from orders')"; }
start(){ $UV backend.main:app --port 8099 >/tmp/uv_$1.log 2>&1 & echo $! >/tmp/uv.pid
  for i in $(seq 60); do curl -sf localhost:8099/openapi.json >/dev/null && break; sleep 0.25; done
  echo "server start $1, pid $(cat /tmp/uv.pid)"; }
echo "Restart recovery test, $(date '+%F %T %Z'), commit $(git rev-parse --short HEAD), $($P --version), PostgreSQL $(psql "$PGURL" -tAc 'show server_version')"
start 1; $P qa_evidence/restart_recovery/restart_client.py before; rows
kill -TERM "$(cat /tmp/uv.pid)"; sleep 1.5; ps -p "$(cat /tmp/uv.pid)" >/dev/null && echo STILL_RUNNING || echo "process 1 stopped by SIGTERM"
start 2; $P qa_evidence/restart_recovery/restart_client.py after; rows
kill -KILL "$(cat /tmp/uv.pid)"; sleep 1; ps -p "$(cat /tmp/uv.pid)" >/dev/null && echo STILL_RUNNING || echo "process 2 stopped by SIGKILL"
start 3; $P qa_evidence/restart_recovery/restart_client.py after; rows
kill "$(cat /tmp/uv.pid)"
