#!/bin/bash
# =============================================================================
# Carga do kit do hackathon no PostgreSQL (roda UMA vez, na primeira subida)
# -----------------------------------------------------------------------------
# - Cria os bancos `hackathon` (dados) e `metabaseappdb` (aplicação do Metabase).
# - Cria os papéis:
#     metabase_app     dono do app-db do Metabase
#     metabase_leitor  SÓ LEITURA em kit + painel (Metabase é local: não sai da máquina)
#     agente_ro        SÓ LEITURA em painel (o agente manda resultados a uma IA
#                      externa; as views de painel escondem dado pessoal — Art. 5º)
# - Carrega TODO .csv encontrado em /kit (pasta ./dados/kit do projeto) como
#   tabela kit.<nome_do_arquivo>, com TODAS as colunas em TEXT: códigos e CNPJs
#   não perdem zeros à esquerda (mesma lição do Tutorial 1: dtype=str). Os tipos
#   (data, número) são aplicados nas views do schema `painel`.
# - Nomes de coluna viram snake_case (numeroControlePNCP -> numero_controle_pncp),
#   para o SQL — escrito por gente ou pela IA — não precisar de aspas.
# - Ao final grava controle.carga_concluida; o healthcheck do compose só libera
#   o Metabase depois disso.
# =============================================================================
set -euo pipefail

PASTA_KIT="${PASTA_KIT:-/kit}"
PSQL=(psql -v ON_ERROR_STOP=1 -X -q -U "${POSTGRES_USER:-postgres}")

echo "[carga] criando bancos e papéis"
"${PSQL[@]}" -d postgres <<SQL
CREATE ROLE metabase_app    LOGIN PASSWORD '${METABASE_APP_PWD}';
CREATE ROLE metabase_leitor LOGIN PASSWORD '${LEITOR_PWD}';
CREATE ROLE agente_ro       LOGIN PASSWORD '${AGENTE_RO_PWD}';
CREATE DATABASE metabaseappdb OWNER metabase_app;
CREATE DATABASE hackathon;
SQL

"${PSQL[@]}" -d hackathon <<'SQL'
CREATE SCHEMA kit;        -- dado bruto do kit, como veio (tudo TEXT)
CREATE SCHEMA painel;     -- views tipadas e sem dado pessoal (Metabase + agente)
CREATE SCHEMA controle;   -- registro da carga
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT CONNECT ON DATABASE hackathon TO metabase_leitor, agente_ro;
GRANT USAGE ON SCHEMA kit, painel TO metabase_leitor;
GRANT USAGE ON SCHEMA painel TO agente_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA kit    GRANT SELECT ON TABLES TO metabase_leitor;
ALTER DEFAULT PRIVILEGES IN SCHEMA painel GRANT SELECT ON TABLES TO metabase_leitor, agente_ro;
-- o agente nunca segura o banco: consulta longa é cortada
ALTER ROLE agente_ro SET statement_timeout = '15s';
ALTER ROLE agente_ro SET default_transaction_read_only = on;
CREATE TABLE controle.tabelas_carregadas (
    tabela text PRIMARY KEY, arquivo text, colunas int, linhas bigint,
    carregado_em timestamptz DEFAULT now());
SQL

export LC_ALL=C.UTF-8
snake() {   # numeroControlePNCP -> numero_controle_pncp ; "Descrição (R$)" -> descricao_r
  sed 'y/áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ/aaaaaeeeeiiiiooooouuuucnAAAAAEEEEIIIIOOOOOUUUUCN/' \
  | sed -E 's/([a-z0-9])([A-Z])/\1_\2/g; s/([A-Z]+)([A-Z][a-z])/\1_\2/g' \
  | tr '[:upper:]' '[:lower:]' \
  | sed -E 's/[^a-z0-9]+/_/g; s/^_+//; s/_+$//; s/^([0-9])/c_\1/'
}

if [ ! -d "$PASTA_KIT" ] || [ -z "$(find "$PASTA_KIT" -type f -iname '*.csv' -print -quit)" ]; then
  echo "[carga] AVISO: nenhum .csv em $PASTA_KIT — banco criado vazio."
  echo "[carga] Coloque o kit descompactado em ./dados/kit e rode: docker compose down -v && docker compose up -d"
fi

find "$PASTA_KIT" -type f -iname '*.csv' 2>/dev/null | sort | while read -r arq; do
  tabela=$(basename "$arq" | sed -E 's/\.[cC][sS][vV]$//' | snake)
  # nome repetido em pastas diferentes: acrescenta a pasta-mãe
  if "${PSQL[@]}" -d hackathon -Atc "SELECT 1 FROM controle.tabelas_carregadas WHERE tabela='$tabela'" | grep -q 1; then
    tabela="$(basename "$(dirname "$arq")" | snake)_${tabela}"
  fi
  cabecalho=$(head -n 1 "$arq" | sed 's/^\xEF\xBB\xBF//' | tr -d '\r')
  # cabeçalho fora de UTF-8 (ex.: LATIN1 do Excel) é convertido antes de virar nome de coluna
  if ! iconv -f utf-8 -t utf-8 <<<"$cabecalho" >/dev/null 2>&1; then
    cabecalho=$(iconv -f latin1 -t utf-8 <<<"$cabecalho")
  fi
  # separador: o que aparecer mais no cabeçalho (vírgula ou ponto e vírgula)
  n_virg=$(tr -cd ',' <<<"$cabecalho" | wc -c); n_pv=$(tr -cd ';' <<<"$cabecalho" | wc -c)
  sep=','; [ "$n_pv" -gt "$n_virg" ] && sep=';'
  colunas=$(tr "$sep" '\n' <<<"$cabecalho" | sed -E 's/^ *"?//; s/"? *$//' | snake \
            | awk '{n=$0; if(n=="") n="coluna_" NR; if(seen[n]++) n=n "_" seen[n]; printf "%s\"%s\" text", (NR>1?", ":""), n}')
  ncol=$(tr "$sep" '\n' <<<"$cabecalho" | wc -l)
  echo "[carga] $arq -> kit.$tabela ($ncol colunas, separador '$sep')"
  "${PSQL[@]}" -d hackathon -c "CREATE TABLE kit.\"$tabela\" ($colunas);"
  if ! "${PSQL[@]}" -d hackathon -c "\copy kit.\"$tabela\" FROM '$arq' WITH (FORMAT csv, HEADER true, DELIMITER '$sep', ENCODING 'UTF8')" 2>/tmp/erro_carga; then
    echo "[carga]   UTF-8 falhou ($(head -c 160 /tmp/erro_carga)); tentando LATIN1"
    "${PSQL[@]}" -d hackathon -c "\copy kit.\"$tabela\" FROM '$arq' WITH (FORMAT csv, HEADER true, DELIMITER '$sep', ENCODING 'LATIN1')"
  fi
  "${PSQL[@]}" -d hackathon -c "INSERT INTO controle.tabelas_carregadas (tabela, arquivo, colunas, linhas)
      SELECT '$tabela', '${arq#$PASTA_KIT/}', $ncol, count(*) FROM kit.\"$tabela\";"
done

"${PSQL[@]}" -d hackathon -c "CREATE TABLE controle.carga_concluida AS SELECT now() AS em;"
echo "[carga] concluída:"
"${PSQL[@]}" -d hackathon -c "SELECT tabela, colunas, linhas, arquivo FROM controle.tabelas_carregadas ORDER BY tabela;"
