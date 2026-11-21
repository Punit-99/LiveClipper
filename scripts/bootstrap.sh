#!/usr/bin/env bash
# Cross-platform bootstrap for LiveClipper (Linux / macOS).
# Pre-creates host files/dirs that the compose stack bind-mounts, then brings it up.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/.."

echo ">>> Creating data directories on host..."
mkdir -p \
  backend/clips \
  backend/shorts \
  backend/imports \
  backend/.buffer \
  backend/logs

echo ">>> Creating state files (if missing)..."
for f in backend/highlights.jsonl backend/.youtube_tokens.json; do
  if [ ! -e "$f" ]; then
    : > "$f"
    echo "    created $f"
  fi
done

if [ ! -f backend/.env ]; then
  if [ -f backend/.env.example ]; then
    cp backend/.env.example backend/.env
    echo ">>> Copied backend/.env.example -> backend/.env  (edit it before first run!)"
  else
    echo "!!! No backend/.env found and no backend/.env.example to copy from." >&2
    exit 1
  fi
fi

echo ">>> Building images (uses BuildKit cache mounts)..."
DOCKER_BUILDKIT=1 docker compose build

echo ">>> Starting stack..."
docker compose up -d

echo
echo "Stack is up:"
docker compose ps --format "table {{.Service}}\t{{.Status}}\t{{.Ports}}"
echo
echo "Open:   http://localhost:3000"
echo "Logs:   docker compose logs -f"
echo "Stop:   docker compose down"
