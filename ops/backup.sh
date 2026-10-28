#!/usr/bin/env bash
set -euo pipefail
mkdir -p "${BACKUP_DIR:-backups}"
out="${BACKUP_DIR:-backups}/servicedesk_$(date +%Y%m%d_%H%M%S).sql"
docker compose exec -T postgres pg_dump -U "${POSTGRES_USER:-dba_admin}" -d "${POSTGRES_DB:-servicedesk}" > "$out"
echo "Backup created: $out"
