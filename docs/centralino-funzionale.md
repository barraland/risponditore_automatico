# Centralino AI — Descrizione funzionale

> Documento di sintesi funzionale (non tecnico) del prodotto. Scopo: base di discussione
> su posizionamento e go-to-market.

## In una frase
Un **assistente telefonico e di messaggistica basato su AI** che risponde al posto (o a
supporto) del personale: parla al telefono e su WhatsApp con voce naturale, capisce la
richiesta, agisce sui dati dell'azienda (CRM, ordini, agenda, documenti) e lascia al titolare
un pannello dove vede tutto e configura tutto senza scrivere codice.

Nato per il mondo **HORECA** (bar, ristoranti, hotel, distribuzione food&beverage), ma la
struttura è generale e riusabile su altri verticali (studi professionali, cliniche/veterinari,
servizi su appuntamento).

## Canali coperti (un solo "cervello" per tutti)
- **Telefono / voce**: risponde alle chiamate con voce naturale in tempo reale.
- **WhatsApp**: gestisce le conversazioni testuali (e i vocali, trascritti automaticamente).
- **Email**: invia riepiloghi, documenti e conferme.

La conoscenza dell'azienda e le regole sono **uniche e condivise fra i canali**: quello che
configuri una volta vale al telefono, su WhatsApp e via mail.

## Multi-cliente (multi-tenant)
Una sola piattaforma serve **più aziende contemporaneamente**, ognuna con il proprio numero,
i propri dati, i propri documenti e le proprie regole, **isolate tra loro**. Il numero chiamato
determina automaticamente di quale azienda si tratta.

---

## Cosa fa quando chiama/scrive un CLIENTE o un potenziale cliente

1. **Riconosce chi è**: identifica il chiamante dal numero e recupera la sua scheda anagrafica
   (o ne crea una nuova se è un contatto sconosciuto). Saluto personalizzato per i clienti noti.
2. **Risponde alle domande** su prodotti, listini, prezzi, condizioni di vendita, FAQ: legge dai
   **documenti caricati dall'azienda** (una knowledge base per cliente) e risponde a voce/testo.
3. **Qualifica e registra il lead**: raccoglie in modo naturale nome, azienda/locale, ruolo,
   email, sede, esigenze — e lo salva nel CRM senza interrogatori.
4. **Registra ordini** (caso HORECA): prende un ordine o un riordino con prodotti e quantità e lo
   inserisce come bozza/confermato, agganciandolo al locale giusto.
5. **Prenota appuntamenti**: verifica la disponibilità e fissa un meeting sul **Google Calendar**
   dell'azienda (con invito e link).
6. **Apre un ticket di follow-up**: a fine conversazione crea automaticamente una scheda per il
   team commerciale (titolo, priorità, sintesi) — **anche se il cliente riaggancia a metà**,
   perché la scheda si genera dal testo della conversazione, non "a mano" durante la chiamata.
7. **Inoltra la chiamata a una persona reale**: se serve un umano (es. il responsabile
   spedizioni), trasferisce la chiamata annunciando chi/cosa e chiedendo conferma vocale al
   destinatario, secondo regole configurabili.
8. **Invia email e documenti**: manda al cliente listini, schede o riepiloghi via mail.

## Cosa fa lato TITOLARE / AMMINISTRATORE
- Se a chiamare/scrivere è un **amministratore** (numero riconosciuto), parte un assistente
  dedicato con funzioni gestionali.
- **Lascia promemoria mirati su un cliente**: "quando chiama Mario del Bar Centrale, digli dello
  sconto sulle birre". Alla successiva chiamata di quel cliente, l'assistente lo comunica da solo.
- **Riceve i ticket via email** in base alla priorità (alta/media/bassa).

---

## Il pannello di controllo (dashboard web)
- **CRM**: contatti, aziende/locali, storico, ordini.
- **Ticket & Log conversazioni**: due viste collegate — elenco ticket ed elenco di **tutte le
  conversazioni** (telefono / WhatsApp), con badge canale, riassunto, trascrizione e link
  incrociato ticket ↔ conversazione. Registra anche le **chiamate perse**.
- **Calendario**: vista settimanale del Google Calendar collegato.
- **Assistente (editor no-code)**: il comportamento dell'assistente è a **moduli** modificabili
  da pannello, distinti per **pubblico** (cliente / amministratore) e per **canale**
  (voce / WhatsApp / mail). Anche i saluti d'apertura sono editabili.
- **Documenti**: caricamento della knowledge base per azienda, con **note interpretative**
  che l'amministratore aggiunge a ciascun documento per guidare le risposte.
- **Integrazioni**: connessione Google (Calendar + invio email dal proprio account).

---

## Differenziatori funzionali (da usare nel posizionamento)
- **Un solo assistente per tutti i canali**: voce, WhatsApp e mail attingono alla stessa
  conoscenza e alle stesse regole. Niente silos, niente doppie configurazioni.
- **Tutto configurabile dal pannello, niente black-box**: comportamento, saluti, regole e
  knowledge base sono visibili e modificabili dal titolare. Nessun "prompt nascosto".
- **Knowledge base per cliente**: ogni azienda ha i suoi documenti e le sue note; le risposte
  restano dentro il suo perimetro (nessuna contaminazione tra clienti).
- **Continuità del dato**: il lead, il ticket e il log si salvano anche se la conversazione si
  interrompe; le chiamate perse diventano una lista di richiami.
- **Azione, non solo risposta**: non "chatta" e basta — registra ordini, fissa meeting, apre
  ticket, inoltra chiamate, manda email. Fa cose sui dati reali dell'azienda.
- **La sicurezza/identità la gestisce il sistema**, non il modello: chi è amministratore e a
  quale azienda appartiene la chiamata li decide il backend al momento della chiamata.

## Valore per il cliente finale (spunti GTM)
- **Non perde più chiamate/richieste** fuori orario o quando il personale è occupato.
- **Cattura e qualifica i lead** in automatico (ogni contatto diventa una scheda + un ticket).
- **Recupera le chiamate perse** (lista richiami) — clienti quasi persi che tornano.
- **Riduce il lavoro ripetitivo** (ordini di riassortimento, richieste di listino, prenotazioni).
- **Briefing operativo**: a fine giornata il titolare ha il quadro di chi ha chiamato, per cosa,
  con quali follow-up aperti.

## Possibili direzioni di mercato (da discutere)
- **Verticale HORECA** (prodotto attuale): distributori food&beverage, catene di locali,
  fornitori che ricevono riordini telefonici/WhatsApp.
- **Servizi su appuntamento** (cliniche, veterinari, studi, saloni): prenotazioni + promemoria +
  richiami, con la stessa struttura.
- **PMI con centralino "che squilla a vuoto"**: sostituzione/affiancamento del receptionist.
- Modelli possibili: per-tenant a canone (numero + minuti/messaggi inclusi), a consumo, o
  white-label per agenzie/rivenditori che rivendono a più clienti.

---

*Nota: descrizione funzionale allo stato attuale del prodotto (demo funzionante multicanale e
multi-tenant). Alcune funzioni richiedono la configurazione degli account esterni collegati
(telefonia, WhatsApp, Google) per ciascun cliente.*
