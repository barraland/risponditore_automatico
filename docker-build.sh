#!/bin/bash
# Costruisce l'immagine Docker dell'app (da rilanciare dopo modifiche al codice)
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "❌ Manca il file .env — copialo da .env.example e compila le chiavi."
  echo "   cp .env.example .env"
  exit 1
fi

echo "🔨 Build dell'immagine Docker..."
docker compose build
echo "✅ Build completata. Avvia con: ./docker-start.sh"
