#!/bin/bash
# Cria as views do schema painel (db/init/sql/painel.sql) e marca a carga como concluída.
# Sem kit.contratos (kit ausente), pula as views mas conclui: o banco sobe vazio.
set -euo pipefail
PSQL=(psql -v ON_ERROR_STOP=1 -X -q -U "${POSTGRES_USER:-postgres}" -d hackathon)
PASTA_SQL="$(dirname "${BASH_SOURCE[0]}")/sql"

if "${PSQL[@]}" -Atc "SELECT to_regclass('kit.contratos') IS NOT NULL" | grep -q t; then
  echo "[painel] criando views do schema painel"
  "${PSQL[@]}" -f "$PASTA_SQL/painel.sql"
  "${PSQL[@]}" -c "SELECT tipo_alerta, count(*) AS alertas FROM painel.vw_alertas GROUP BY 1 ORDER BY 1;"
else
  echo "[painel] AVISO: kit.contratos não existe — views não criadas (coloque o kit em ./dados/kit)."
fi

"${PSQL[@]}" -c "CREATE TABLE IF NOT EXISTS controle.carga_concluida AS SELECT now() AS em;"
echo "[painel] carga concluída"
