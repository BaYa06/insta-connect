#!/usr/bin/env bash
# Daily Postgres dump with rotation. Run from the project dir (cron example in deploy/README.md).
set -Eeuo pipefail
cd "$(dirname "$0")/.."
KEEP_DAYS="${KEEP_DAYS:-14}"
mkdir -p backups
out="backups/flashly_$(date +%Y-%m-%d_%H%M).sql.gz"
if docker compose exec -T postgres pg_dump -U flashly -d flashly --no-owner | gzip > "$out"; then
  [ "$(gzip -dc "$out" | wc -c)" -gt 0 ] || { echo "empty dump"; rm -f "$out"; exit 1; }
  echo "$(date -Is) ok $out ($(du -h "$out" | cut -f1))"
else
  rm -f "$out"; echo "$(date -Is) BACKUP FAILED" >&2; exit 1
fi
find backups -name 'flashly_*.sql.gz' -type f -mtime +"$KEEP_DAYS" -delete
