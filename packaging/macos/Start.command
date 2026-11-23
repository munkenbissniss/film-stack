#!/bin/bash
# Film-stack – start (macOS). Dobbeltklikk. Første gang kjøres veiviseren.
cd "$(dirname "$0")" || exit 1
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.orbstack/bin:/Applications/Docker.app/Contents/Resources/bin:$PATH"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }

if ! command -v docker >/dev/null 2>&1; then
  say "Fant ikke Docker. Installer OrbStack (https://orbstack.dev) eller Docker Desktop, og prøv igjen."
  read -r -p "Trykk Enter for å lukke." _; exit 1
fi

if ! docker info >/dev/null 2>&1; then
  say "Starter Docker …"
  open -a OrbStack 2>/dev/null || open -a Docker 2>/dev/null
  for _ in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 3; done
  if ! docker info >/dev/null 2>&1; then
    say "Docker startet ikke. Start OrbStack eller Docker Desktop selv, og dobbeltklikk Start.command igjen."
    read -r -p "Trykk Enter for å lukke." _; exit 1
  fi
fi

if [ ! -f .env ]; then
  say "Første gang: starter veiviseren …"
  docker compose run --rm setup || { read -r -p "Trykk Enter for å lukke." _; exit 1; }
fi

say "Starter alt (første gang lastes rundt 3 GB ned) …"
docker compose up -d

say "Følger oppsettet (2–5 minutter). Ctrl+C avbryter bare visningen – ingenting stoppes."
docker compose logs -f --tail 50 autoconfig &
LOGS=$!
sleep 5
while [ "$(docker compose ps -a --format '{{.State}}' autoconfig 2>/dev/null)" = "running" ]; do sleep 5; done
sleep 1; kill "$LOGS" 2>/dev/null || true

say "Sjekker at alt virker …"
docker compose run --rm sjekk || say "Noe trenger oppmerksomhet – se listen over. Rett det, og kjør «docker compose run --rm sjekk» igjen."
