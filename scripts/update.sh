#!/usr/bin/env bash
# Update Noble Search to the latest version and restart it. Your data/ folder is kept.
# Run from the noble-search folder:   ./scripts/update.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { cp .env.example .env; echo "Created .env from .env.example"; }
# add any new settings introduced in .env.example (keeps your existing values)
for k in $(grep -oE '^[A-Z_]+=' .env.example | tr -d '='); do
  grep -q "^$k=" .env || { grep "^$k=" .env.example >> .env; echo "Added $k to .env"; }
done
git pull --ff-only
docker compose up -d --build
port=$(grep -E '^HOST_PORT=' .env | cut -d= -f2); port=${port:-8093}
for i in 1 2 3 4 5 6 7 8 9 10; do
  if curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1; then echo "Noble Search is running: http://localhost:$port"; exit 0; fi
  sleep 3
done
echo "Started, but the health check did not answer yet. Check: docker compose logs --tail 50"
