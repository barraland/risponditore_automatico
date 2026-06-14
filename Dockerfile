FROM python:3.11-slim

# Evita .pyc e bufferizzazione log (così i log escono subito su docker logs)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dipendenze di sistema: poppler-utils fornisce `pdftotext`, usato dall'ingestion
# dei documenti PDF (estrazione testo pagina-per-pagina).
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Installa le dipendenze prima del codice per sfruttare la cache di build
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia il resto dell'applicazione
COPY . .

# Cartella per il database SQLite persistente (montata come volume in compose)
RUN mkdir -p /app/data

EXPOSE 9999

# Avvio in produzione (uvicorn diretto, niente --reload come nel blocco __main__)
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9999"]
