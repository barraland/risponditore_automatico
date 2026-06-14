#!/bin/bash
# Ferma e rimuove i container (i dati del DB restano nella cartella ./data)
set -e
cd "$(dirname "$0")"

echo "🛑 Arresto container..."
docker compose down
echo "✅ Container fermati. Il database in ./data è conservato."
echo "   Per ripartire:  ./docker-start.sh"
