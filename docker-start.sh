#!/bin/bash
# Avvia app + ngrok in background, poi mostra l'URL pubblico per Wazzup
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ Manca il file .env — copialo da .env.example e compila le chiavi."
  exit 1
fi

if [ ! -f credentials.json ]; then
  echo "⚠️  credentials.json non trovato: Google Calendar sarà disabilitato (l'app parte comunque)."
fi

echo "🚀 Avvio container (app + ngrok)..."
docker compose up -d

echo "⏳ Attendo che ngrok stabilisca il tunnel..."
sleep 5

# Recupera l'URL pubblico dalla API di ngrok (porta 4040 esposta sull'host)
API=$(curl -s http://localhost:4040/api/tunnels 2>/dev/null \
  || wget -qO- http://localhost:4040/api/tunnels 2>/dev/null)
URL=$(echo "$API" | grep -o 'https://[a-zA-Z0-9.-]*ngrok[a-zA-Z0-9.-]*' | head -n1)

echo "------------------------------------------------"
if [ -n "$URL" ]; then
  echo "🌍 Endpoint pubblico:  $URL"
  echo "👉 Incolla questo URL (+ il path del webhook) nella dashboard Wazzup."
else
  echo "ℹ️  Non sono riuscito a leggere l'URL automaticamente."
  echo "   Aprilo dalla dashboard ngrok: http://<IP_SERVER>:4040"
fi
echo "------------------------------------------------"
echo "📋 Log in tempo reale:  docker compose logs -f"
echo "🛑 Stop:                ./docker-stop.sh"
