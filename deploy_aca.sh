#!/usr/bin/env bash
# Deploy del monolite su Azure Container Apps (ACA), DB su Supabase.
# Prerequisiti: az login fatto, .env compilato (con DATABASE_URL = Supabase session pooler).
# Uso:  ./deploy_aca.sh
set -euo pipefail
cd "$(dirname "$0")"

# ----------------- CONFIG (modifica qui) -----------------
RG="horeca-rg"
LOC="westeurope"
ENVNAME="horeca-env2"   # il vecchio 'horeca-env' è ScheduledForDelete: ne usiamo uno nuovo
APP="horeca-app"
DASHBOARD_USER="admin"
# Password della dashboard: passala via env (DASHBOARD_PASSWORD=... ./deploy_aca.sh)
# per non scriverla nel file. Altrimenti modifica il fallback qui sotto.
DASHBOARD_PASSWORD="${DASHBOARD_PASSWORD:-cambia-questa-password}"
# ---------------------------------------------------------

if [ ! -f .env ]; then echo "❌ manca .env"; exit 1; fi
# Carica i valori dal .env in modo robusto (gestisce spazi non quotati, CRLF, virgolette).
# NB: non usiamo `source` perché il .env stile dotenv non è sempre shell-valido.
load_env() {
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"                                  # rimuovi CR (file Windows)
    case "$line" in ''|\#*) continue;; esac               # salta righe vuote/commenti
    [ "${line#*=}" = "$line" ] && continue                # salta righe senza '='
    key="${line%%=*}"; val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"       # niente spazi nella chiave
    val="${val#"${val%%[![:space:]]*}"}"                  # trim spazi iniziali
    val="${val%"${val##*[![:space:]]}"}"                  # trim spazi finali
    case "$val" in                                        # togli virgolette esterne
      \"*\") val="${val#\"}"; val="${val%\"}";;
      \'*\') val="${val#\'}"; val="${val%\'}";;
    esac
    export "$key=$val"
  done < .env
}
load_env

: "${DATABASE_URL:?DATABASE_URL non impostata nel .env (deve puntare a Supabase)}"
: "${OPENAI_API_KEY:?OPENAI_API_KEY non impostata nel .env}"

echo "==> 1) Resource group"
az group create -n "$RG" -l "$LOC" -o none

echo "==> 2) Build immagine (in cloud) + deploy iniziale"
az containerapp up \
  --name "$APP" --resource-group "$RG" --location "$LOC" \
  --environment "$ENVNAME" \
  --source . \
  --ingress external --target-port 9999

echo "==> 3) Secret + env (passo solo i valori presenti nel .env; gli altri li salto)"
SECRETS=()
ENVS=("DOCUMENTI_DIR=/app/data/documenti" "DASHBOARD_USER=$DASHBOARD_USER")

# secret + relativa env (secretref) solo se il valore non è vuoto
add_secret() {   # $1=nome-secret  $2=NOME_ENV  $3=valore
  if [ -n "${3:-}" ]; then
    SECRETS+=("$1=$3")
    ENVS+=("$2=secretref:$1")
  fi
}
add_secret db-url     DATABASE_URL              "${DATABASE_URL:-}"
add_secret openai-key OPENAI_API_KEY            "${OPENAI_API_KEY:-}"
add_secret gmail-pass GMAIL_APP_PASSWORD        "${GMAIL_APP_PASSWORD:-}"
add_secret el-secret  ELEVENLABS_WEBHOOK_SECRET "${ELEVENLABS_WEBHOOK_SECRET:-}"
add_secret el-token   ELEVENLABS_WEBHOOK_TOKEN  "${ELEVENLABS_WEBHOOK_TOKEN:-}"
add_secret mcp-token  MCP_AUTH_TOKEN            "${MCP_AUTH_TOKEN:-}"
add_secret wa-token   WHATSAPP_TOKEN            "${WHATSAPP_TOKEN:-}"
add_secret wa-verify  WHATSAPP_VERIFY_TOKEN     "${WHATSAPP_VERIFY_TOKEN:-}"
add_secret dash-pass  DASHBOARD_PASSWORD        "${DASHBOARD_PASSWORD:-}"
add_secret twilio-sid TWILIO_ACCOUNT_SID        "${TWILIO_ACCOUNT_SID:-}"   # inoltro chiamata (REST)
add_secret twilio-tok TWILIO_AUTH_TOKEN         "${TWILIO_AUTH_TOKEN:-}"
add_secret el-api-key ELEVENLABS_API_KEY        "${ELEVENLABS_API_KEY:-}"   # inoltro assistito (outbound)
add_secret sb-svc-key SUPABASE_SERVICE_ROLE_KEY "${SUPABASE_SERVICE_ROLE_KEY:-}"  # download allegati da Storage
add_secret g-cli-id   GOOGLE_CLIENT_ID           "${GOOGLE_CLIENT_ID:-}"     # Google Calendar OAuth
add_secret g-cli-sec  GOOGLE_CLIENT_SECRET       "${GOOGLE_CLIENT_SECRET:-}"

# env in chiaro (non segrete), solo se presenti
if [ -n "${GMAIL_FROM:-}" ]; then ENVS+=("GMAIL_FROM=$GMAIL_FROM"); fi
if [ -n "${WHATSAPP_PHONE_NUMBER_ID:-}" ]; then ENVS+=("WHATSAPP_PHONE_NUMBER_ID=$WHATSAPP_PHONE_NUMBER_ID"); fi
if [ -n "${OPENAI_REALTIME_VOICE:-}" ]; then ENVS+=("OPENAI_REALTIME_VOICE=$OPENAI_REALTIME_VOICE"); fi
# Inoltro assistito: id dell'agente outbound e del numero outbound su ElevenLabs (non segreti)
if [ -n "${ELEVENLABS_OUTBOUND_AGENT_ID:-}" ]; then ENVS+=("ELEVENLABS_OUTBOUND_AGENT_ID=$ELEVENLABS_OUTBOUND_AGENT_ID"); fi
if [ -n "${ELEVENLABS_OUTBOUND_PHONE_ID:-}" ]; then ENVS+=("ELEVENLABS_OUTBOUND_PHONE_ID=$ELEVENLABS_OUTBOUND_PHONE_ID"); fi
# SPA: origini CORS ammesse + Supabase per verificare il token degli upload (anon key è pubblica)
if [ -n "${CORS_ORIGINS:-}" ]; then ENVS+=("CORS_ORIGINS=$CORS_ORIGINS"); fi
if [ -n "${SPA_BASE_URL:-}" ]; then ENVS+=("SPA_BASE_URL=$SPA_BASE_URL"); fi
if [ -n "${SUPABASE_URL:-}" ]; then ENVS+=("SUPABASE_URL=$SUPABASE_URL"); fi
if [ -n "${SUPABASE_ANON_KEY:-}" ]; then ENVS+=("SUPABASE_ANON_KEY=$SUPABASE_ANON_KEY"); fi

az containerapp secret set -g "$RG" -n "$APP" --secrets "${SECRETS[@]}" -o none

echo "==> 4) Variabili d'ambiente"
# --revision-suffix forza SEMPRE una nuova revision: senza, se la spec env non cambia (es. hai solo
# aggiornato il VALORE di un secret) ACA non riavvia i container e resta il vecchio valore in memoria.
az containerapp update -g "$RG" -n "$APP" --set-env-vars "${ENVS[@]}" \
  --revision-suffix "d$(date +%Y%m%d%H%M%S)" -o none

echo "==> 5) Scaling: 1 replica sempre calda (no cold start). max alzabile (DB ora è Postgres)."
az containerapp update -g "$RG" -n "$APP" --min-replicas 1 --max-replicas 3 -o none

FQDN=$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)
echo
echo "✅ FATTO. App pubblica: https://$FQDN"
echo
echo "Aggiorna i webhook a questo dominio:"
echo "  ElevenLabs init      : https://$FQDN/elevenlabs/init"
echo "  ElevenLabs post-call : https://$FQDN/elevenlabs/post-call"
echo "  ElevenLabs MCP       : https://$FQDN/mcp"
echo "  WhatsApp (Meta)      : https://$FQDN/webhook"
echo "  Dashboard (login)    : https://$FQDN   (utente: $DASHBOARD_USER)"
echo
echo "NB: i DOCUMENTI caricati sono ancora su disco effimero. Per renderli persistenti"
echo "    monta un Azure Files su /app/data (vedi lo step volume nella guida)."
