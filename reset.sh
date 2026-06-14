#!/bin/bash
# Reset DB + sync Google Calendar
# Uso: ./reset.sh

cd "$(dirname "$0")"

PYTHON="${VIRTUAL_ENV:-/home/sommojames/langgraph_env}/bin/python"
if [ ! -f "$PYTHON" ]; then
    PYTHON=python3
fi

echo "Fermo l'app se è in esecuzione..."
fuser -k 9999/tcp 2>/dev/null
sleep 1

echo "Reset database + Calendar sync..."
$PYTHON reset_db.py

echo ""
echo "Riavvio l'app..."
$PYTHON main.py
