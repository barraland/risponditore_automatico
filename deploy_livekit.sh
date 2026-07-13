#!/usr/bin/env bash
# Redeploy dell'agente vocale LiveKit (nuova versione del codice in livekit_agent/).
# Uso: ./deploy_livekit.sh
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR/livekit_agent"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# Credenziali LiveKit dal .env del backend (gitignored).
export LIVEKIT_URL=$(grep -E "^LIVEKIT_URL=" "$DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs)
export LIVEKIT_API_KEY=$(grep -E "^LIVEKIT_API_KEY=" "$DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs)
export LIVEKIT_API_SECRET=$(grep -E "^LIVEKIT_API_SECRET=" "$DIR/.env" | cut -d= -f2- | tr -d '"' | tr -d "'" | xargs)

echo "==> Deploy agente vocale LiveKit (nuova versione)..."
lk agent deploy

echo "==> Stato:"
lk agent status

echo
echo "Per i log in tempo reale:  cd livekit_agent && lk agent logs"
