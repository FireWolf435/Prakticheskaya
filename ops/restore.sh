#!/usr/bin/env bash
set -euo pipefail
if [[ $# -ne 1 ]]; then echo "usage: $0 backup.sql" >&2; exit 2; fi
cat "$1" | docker compose exec -T postgres psql -U "${POSTGRES_USER:-dba_admin}" -d "${POSTGRES_DB:-servicedesk}"
echo "Restore completed"
