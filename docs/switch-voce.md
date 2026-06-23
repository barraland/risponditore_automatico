# Switch voce — ElevenLabs ⇄ OpenAI Realtime

Si cambia dal **numero Twilio** (Console → Phone Numbers → il numero → "A call comes in"):

1. **OpenAI Realtime** → tipo *Webhook* (HTTP POST): `https://horeca-app.ashymushroom-7f7b92f9.westeurope.azurecontainerapps.io/voice/incoming`
2. **ElevenLabs** → integrazione nativa: collega il numero all'agente ElevenLabs (sezione Phone Numbers dell'agente).
webhook --> https://api.us.elevenlabs.io/twilio/inbound_call
call status changes --> https://api.us.elevenlabs.io/twilio/status-callback


3. Per l'A/B in demo: tieni **due numeri**, uno per provider, e chiami l'uno o l'altro.

> Stesso prompt (dashboard → Configurazione assistente) e stessi tool per entrambi i provider.
