#!/usr/bin/env bash
# Nightly ledger backup. The hash chain makes tampering visible; a backup makes loss survivable.
#   crontab: 15 3 * * * /opt/signalproof/deploy/backup.sh
set -euo pipefail
cd /opt/signalproof
mkdir -p backups
f="backups/signalproof-$(date -u +%F).sql.gz"
docker compose -f docker-compose.prod.yml exec -T db pg_dump -U signalproof signalproof | gzip > "$f"
find backups -name '*.sql.gz' -mtime +14 -delete
# verify the chain after backup; a non-zero exit shows up in cron mail
docker compose -f docker-compose.prod.yml exec -T api python -c \
  "import urllib.request,json,sys;r=json.load(urllib.request.urlopen('http://localhost:8000/v1/verify/chain/full'));print(r);sys.exit(0 if r['ok'] else 1)"
