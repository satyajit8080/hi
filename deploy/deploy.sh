#!/usr/bin/env bash
# First deploy and every redeploy. Idempotent. Run as a sudo-capable user on Ubuntu 22.04/24.04.
#   DOMAIN=signals.example.com REPO=https://github.com/you/signalproof.git bash deploy.sh
set -euo pipefail
: "${DOMAIN:?export DOMAIN=your.domain}"
APP_DIR="${APP_DIR:-/opt/signalproof}"
REPO="${REPO:-}"

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
  sudo usermod -aG docker "$USER"
fi

sudo mkdir -p "$APP_DIR" && sudo chown "$USER" "$APP_DIR"
if [ -n "$REPO" ] && [ ! -d "$APP_DIR/.git" ]; then git clone "$REPO" "$APP_DIR"; fi
cd "$APP_DIR"
[ -d .git ] && git pull --ff-only

if [ ! -f .env ]; then
  cp .env.example .env
  PG=$(openssl rand -hex 24); SK=$(openssl rand -hex 32)
  sed -i "s|^SP_SECRET_KEY=.*|SP_SECRET_KEY=$SK|" .env
  sed -i "s|^SP_DATABASE_URL=.*|SP_DATABASE_URL=postgresql+asyncpg://signalproof:$PG@db:5432/signalproof|" .env
  sed -i "s|^SP_ENV=.*|SP_ENV=production|" .env
  sed -i "s|^SP_CORS_ORIGINS=.*|SP_CORS_ORIGINS=https://$DOMAIN|" .env
  sed -i "s|^NEXT_PUBLIC_API_URL=.*|NEXT_PUBLIC_API_URL=https://$DOMAIN|" .env
  sed -i "s|^NEXT_PUBLIC_WS_URL=.*|NEXT_PUBLIC_WS_URL=wss://$DOMAIN/ws/market|" .env
  { echo "POSTGRES_PASSWORD=$PG"; echo "DOMAIN=$DOMAIN"; } >> .env
  chmod 600 .env
  echo ">> .env created. Still replay mode. Edit SP_MARKET_MODE / billing / SMTP, then rerun."
fi

# Firewall: only SSH and Caddy.
if command -v ufw >/dev/null; then
  sudo ufw allow OpenSSH >/dev/null; sudo ufw allow 80/tcp >/dev/null; sudo ufw allow 443/tcp >/dev/null
  sudo ufw --force enable >/dev/null
fi

docker compose -f docker-compose.prod.yml build --pull
docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker image prune -f >/dev/null

echo ">> waiting for API"
for _ in $(seq 1 30); do
  if docker compose -f docker-compose.prod.yml exec -T api python -c \
     "import urllib.request,json;print(json.load(urllib.request.urlopen('http://localhost:8000/v1/status'))['data_delayed'])" 2>/dev/null; then break; fi
  sleep 3
done
docker compose -f docker-compose.prod.yml ps
echo ">> https://$DOMAIN"
