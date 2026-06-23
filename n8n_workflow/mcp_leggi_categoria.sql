-- Tool "leggi_<categoria>" — ritorna il testo integrale dei documenti di una categoria
-- + la nota amministratore. Cambia SOLO la categoria ('listino') per gli altri tool:
--   leggi_condizioni_vendita -> 'contratti'
--   leggi_schede_prodotto    -> 'schede_prodotto'
--   leggi_faq                -> 'faq'
--   leggi_altri_documenti    -> 'altro'
WITH nota AS (
  SELECT testo FROM testi_categoria
  WHERE categoria = 'listino' AND COALESCE(trim(testo), '') <> ''
),
docs AS (
  SELECT d.nome_file,
         string_agg(s.content_md, E'\n' ORDER BY s.ordine) AS testo,
         max(d.caricato_at) AS ts
  FROM documenti d
  JOIN sezioni s ON s.documento_id = d.id
  WHERE d.categoria = 'listino' AND d.stato IN ('READY', 'NEEDS_REVIEW')
  GROUP BY d.id, d.nome_file
)
SELECT COALESCE(
  NULLIF(trim(concat_ws(E'\n\n',
    (SELECT 'NOTA DELL''AMMINISTRATORE: ' || testo FROM nota),
    (SELECT string_agg('=== ' || nome_file || E' ===\n' || testo, E'\n\n' ORDER BY ts DESC) FROM docs)
  )), ''),
  'Nessun documento disponibile in questa categoria.'
) AS contenuto;
