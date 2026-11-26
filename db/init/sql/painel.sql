-- =============================================================================
-- Schema `painel` — o que o Metabase e o agente enxergam (Desafio 4)
-- -----------------------------------------------------------------------------
-- Idempotente: pode ser reaplicado a qualquer momento sem recriar o banco:
--   docker compose exec -T db psql -U postgres -d hackathon \
--        -f /docker-entrypoint-initdb.d/sql/painel.sql
--
-- Regras (herdadas do SIDEC-MB e ajustadas ao kit — ver LEIAME do kit):
--   - DATA DE REFERÊNCIA PARAMETRIZADA. O kit está congelado em 31/10/2026: contar
--     prazos a partir de hoje marcaria como vencido o que pode ter sido prorrogado
--     depois do corte (o kit não traz aditivos). Em produção, trocar por CURRENT_DATE.
--   - Contrato x empenho separados (62% das linhas do kit são empenhos).
--   - Concentração por CNPJ RAIZ (matriz + filiais), CNPJ sempre texto.
--   - Pessoa física: CPF e nome NUNCA saem das views (Art. 5º do regulamento).
--   - Valor atípico (> limite) não é descartado em silêncio: sai do cálculo de
--     concentração e vira alerta de qualidade de dado.
--   - Encerrado há até N dias = "verificar reposição": a sucessão não é deduzida.
-- =============================================================================
SET client_min_messages = warning;

-- ---------------------------------------------------------------------------
-- Parâmetros (uma linha). Reaplicar este arquivo NÃO sobrescreve valores editados.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS painel.parametro (
    id                        int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    data_referencia           date    NOT NULL,
    janela_reposicao_dias     int     NOT NULL,
    limiar_participacao_pct   numeric NOT NULL,
    min_instrumentos_unidade  int     NOT NULL,
    valor_atipico_acima       numeric NOT NULL
);
INSERT INTO painel.parametro VALUES (1, DATE '2026-10-31', 90, 40, 3, 1000000000)
ON CONFLICT (id) DO NOTHING;
COMMENT ON TABLE painel.parametro IS 'Parâmetros dos alertas (uma linha). data_referencia = data de corte do kit (31/10/2026).';
COMMENT ON COLUMN painel.parametro.data_referencia IS 'Data a partir da qual se contam os dias para vencer (data de corte do kit).';
COMMENT ON COLUMN painel.parametro.janela_reposicao_dias IS 'Encerrados há até estes dias entram no alerta "verificar reposição".';
COMMENT ON COLUMN painel.parametro.limiar_participacao_pct IS 'Participação do fornecedor no valor vigente da unidade a partir da qual há alerta de concentração.';
COMMENT ON COLUMN painel.parametro.min_instrumentos_unidade IS 'Mínimo de instrumentos vigentes na unidade para avaliar concentração.';
COMMENT ON COLUMN painel.parametro.valor_atipico_acima IS 'Valor global acima do qual o instrumento é tratado como valor atípico (provável erro de digitação).';

-- conversões seguras: texto mal formado vira NULL, nunca derruba a consulta
CREATE OR REPLACE FUNCTION painel.para_data(t text) RETURNS date
LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
    IF t IS NULL OR t !~ '^\d{4}-\d{2}-\d{2}' THEN RETURN NULL; END IF;
    RETURN left(t, 10)::date;
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

CREATE OR REPLACE FUNCTION painel.para_numero(t text) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN t ~ '^\s*-?[0-9]+(\.[0-9]+)?\s*$' THEN trim(t)::numeric END
$$;

DROP VIEW IF EXISTS painel.vw_alertas;
DROP VIEW IF EXISTS painel.vw_concentracao_marinha;
DROP VIEW IF EXISTS painel.vw_concentracao;
DROP VIEW IF EXISTS painel.vw_contratos;

-- ---------------------------------------------------------------------------
-- 1. vw_contratos — 1 linha por contrato ou empenho, tipado e sem dado pessoal
-- ---------------------------------------------------------------------------
CREATE VIEW painel.vw_contratos AS
WITH p AS (SELECT * FROM painel.parametro WHERE id = 1),
base AS (
    SELECT
        c.numero_controle_pncp,
        NULLIF(c.numero_controle_pncp_compra, '')                     AS numero_controle_compra,
        c.numero_contrato_empenho                                     AS numero_contrato,
        painel.para_numero(c.ano_contrato)::int                       AS ano_contrato,
        c.tipo_contrato_nome                                          AS tipo_instrumento,
        CASE WHEN c.tipo_contrato_nome = 'Empenho' THEN 'Empenho'
             WHEN c.tipo_contrato_nome IN ('Contrato (termo inicial)', 'Carta Contrato') THEN 'Contrato'
             ELSE 'Outros instrumentos' END                           AS familia_instrumento,
        c.categoria_processo_nome                                     AS categoria_processo,
        c.unidade_codigo,
        COALESCE(u.unidade_nome, c.unidade_nome)                      AS unidade_nome,
        COALESCE(u.unidade_municipio, c.unidade_municipio)            AS unidade_municipio,
        COALESCE(u.unidade_uf, c.unidade_uf)                          AS unidade_uf,
        c.objeto_contrato                                             AS objeto,
        c.tipo_pessoa,
        CASE WHEN c.tipo_pessoa = 'PF' THEN NULL ELSE c.ni_fornecedor END AS fornecedor_ni,
        CASE WHEN c.tipo_pessoa = 'PF' THEN 'PF-' || left(md5(c.ni_fornecedor), 8)
             WHEN c.tipo_pessoa = 'PJ' AND length(c.ni_fornecedor) = 14 THEN left(c.ni_fornecedor, 8)
             ELSE c.ni_fornecedor END                                 AS fornecedor_chave,
        CASE WHEN c.tipo_pessoa = 'PF' THEN 'Pessoa física (nome omitido)'
             ELSE c.nome_razao_social_fornecedor END                  AS fornecedor_nome,
        painel.para_numero(c.valor_inicial)                           AS valor_inicial,
        painel.para_numero(c.valor_global)                            AS valor_global,
        painel.para_numero(c.valor_parcela)                           AS valor_parcela,
        painel.para_numero(c.valor_acumulado)                         AS valor_acumulado,
        painel.para_numero(c.numero_parcelas)::int                    AS numero_parcelas,
        painel.para_data(c.data_assinatura)                           AS data_assinatura,
        painel.para_data(c.data_vigencia_inicio)                      AS vigencia_inicio,
        painel.para_data(c.data_vigencia_fim)                         AS vigencia_fim,
        painel.para_data(c.data_publicacao_pncp)                      AS data_publicacao,
        (c.receita = 'S')                                             AS gera_receita,
        painel.para_numero(c.numero_retificacao)::int                 AS numero_retificacoes,
        NULLIF(c.vigente_na_data_corte, '')                           AS vigente_na_data_corte_kit,
        p.data_referencia, p.janela_reposicao_dias, p.valor_atipico_acima
    FROM kit.contratos c
    CROSS JOIN p
    LEFT JOIN kit.unidades u ON u.unidade_codigo = c.unidade_codigo
),
calc AS (
    SELECT b.*,
        (b.vigencia_fim - b.data_referencia)                          AS dias_para_vencer,
        CASE WHEN b.vigencia_fim IS NULL                   THEN 'sem data de fim'
             WHEN b.vigencia_inicio > b.data_referencia    THEN 'a iniciar'
             WHEN b.vigencia_fim >= b.data_referencia      THEN 'vigente'
             ELSE 'encerrado' END                                     AS situacao
    FROM base b
)
SELECT
    numero_controle_pncp, numero_controle_compra, numero_contrato, ano_contrato,
    tipo_instrumento, familia_instrumento, categoria_processo,
    unidade_codigo, unidade_nome, unidade_municipio, unidade_uf,
    objeto, tipo_pessoa, fornecedor_ni, fornecedor_chave, fornecedor_nome,
    valor_inicial, valor_global, valor_parcela, valor_acumulado, numero_parcelas,
    data_assinatura, vigencia_inicio, vigencia_fim, data_publicacao,
    gera_receita, numero_retificacoes,
    data_referencia, dias_para_vencer, situacao,
    CASE WHEN situacao = 'sem data de fim'                    THEN '0. Sem data de fim'
         WHEN situacao = 'vigente' AND dias_para_vencer <= 90  THEN '1. Vence em até 90 dias'
         WHEN situacao = 'vigente' AND dias_para_vencer <= 180 THEN '2. Vence em 91 a 180 dias'
         WHEN situacao = 'vigente'                             THEN '3. Vence em mais de 180 dias'
         WHEN situacao = 'a iniciar'                           THEN '4. A iniciar'
         ELSE                                                       '5. Encerrado' END AS janela_vencimento,
    COALESCE(situacao = 'encerrado'
             AND dias_para_vencer >= -janela_reposicao_dias, false)        AS verificar_reposicao,
    COALESCE((vigencia_fim - vigencia_inicio) > 3653
             OR dias_para_vencer > 3653, false)                            AS vigencia_anomala,
    COALESCE(valor_global > valor_atipico_acima, false)                    AS valor_atipico,
    (data_publicacao - data_assinatura)                                    AS dias_assinatura_publicacao,
    vigente_na_data_corte_kit
FROM calc;

COMMENT ON VIEW painel.vw_contratos IS
  'Uma linha por contrato ou empenho da Marinha (contratos.csv do kit, publicados de 01/01/2023 a 31/10/2026). '
  'Prazos contados a partir de painel.parametro.data_referencia (data de corte 31/10/2026). '
  'Pessoa física sem CPF e sem nome. O kit não traz aditivos: um contrato prorrogado pode aparecer como encerrado.';
COMMENT ON COLUMN painel.vw_contratos.numero_controle_pncp IS 'Identificador do contrato no PNCP (fonte verificável). Use sempre para citar a origem.';
COMMENT ON COLUMN painel.vw_contratos.numero_controle_compra IS 'Contratação de origem (liga com kit.contratacoes; só 25% encontram).';
COMMENT ON COLUMN painel.vw_contratos.numero_contrato IS 'Número do contrato ou do empenho no órgão.';
COMMENT ON COLUMN painel.vw_contratos.tipo_instrumento IS 'Tipo original: Contrato (termo inicial), Empenho, Carta Contrato, Outros etc.';
COMMENT ON COLUMN painel.vw_contratos.familia_instrumento IS 'Contrato | Empenho | Outros instrumentos. Para "contratos" filtre familia_instrumento = ''Contrato''.';
COMMENT ON COLUMN painel.vw_contratos.categoria_processo IS 'Compras, Serviços, Serviços de Engenharia, Obras, Informática (TIC) etc.';
COMMENT ON COLUMN painel.vw_contratos.unidade_codigo IS 'Código UASG da OM contratante (6 dígitos, texto). Agrupe por OM sempre por esta coluna.';
COMMENT ON COLUMN painel.vw_contratos.unidade_nome IS 'Nome da OM como cadastrado (maiúsculas, abreviado). Para buscar por nome use ILIKE ''%TERMO%''.';
COMMENT ON COLUMN painel.vw_contratos.unidade_uf IS 'UF da sede da OM.';
COMMENT ON COLUMN painel.vw_contratos.objeto IS 'Objeto do contrato em texto livre.';
COMMENT ON COLUMN painel.vw_contratos.tipo_pessoa IS 'PJ, PF (pessoa física, dados omitidos) ou PE (estrangeiro).';
COMMENT ON COLUMN painel.vw_contratos.fornecedor_ni IS 'CNPJ do fornecedor (texto). NULL para pessoa física.';
COMMENT ON COLUMN painel.vw_contratos.fornecedor_chave IS 'Agrupador do fornecedor: CNPJ raiz (8 primeiros dígitos, junta matriz e filiais); pessoa física = código anônimo.';
COMMENT ON COLUMN painel.vw_contratos.fornecedor_nome IS 'Razão social do fornecedor; pessoa física aparece como "Pessoa física (nome omitido)".';
COMMENT ON COLUMN painel.vw_contratos.valor_inicial IS 'Valor inicial em R$.';
COMMENT ON COLUMN painel.vw_contratos.valor_global IS 'Valor global em R$ (igual ao inicial em 99,6% das linhas: o kit não traz aditivos).';
COMMENT ON COLUMN painel.vw_contratos.vigencia_inicio IS 'Início da vigência.';
COMMENT ON COLUMN painel.vw_contratos.vigencia_fim IS 'Fim da vigência (base dos alertas de vencimento). NULL em 29 instrumentos.';
COMMENT ON COLUMN painel.vw_contratos.data_publicacao IS 'Data de publicação no PNCP.';
COMMENT ON COLUMN painel.vw_contratos.gera_receita IS 'true se o instrumento gera receita (cessão, concessão), não despesa.';
COMMENT ON COLUMN painel.vw_contratos.numero_retificacoes IS 'Quantidade de retificações do registro no PNCP.';
COMMENT ON COLUMN painel.vw_contratos.data_referencia IS 'Data usada para contar os prazos (data de corte do kit).';
COMMENT ON COLUMN painel.vw_contratos.dias_para_vencer IS 'vigencia_fim - data_referencia. Negativo = já venceu.';
COMMENT ON COLUMN painel.vw_contratos.situacao IS 'vigente | encerrado | a iniciar | sem data de fim (na data de referência).';
COMMENT ON COLUMN painel.vw_contratos.janela_vencimento IS 'Faixa de vencimento: 0 sem data, 1 até 90 dias, 2 91 a 180, 3 mais de 180, 4 a iniciar, 5 encerrado.';
COMMENT ON COLUMN painel.vw_contratos.verificar_reposicao IS 'true se encerrou há até 90 dias: verificar se foi prorrogado ou reposto (sucessão não é deduzida).';
COMMENT ON COLUMN painel.vw_contratos.vigencia_anomala IS 'true se a vigência passa de 10 anos (provável erro de data).';
COMMENT ON COLUMN painel.vw_contratos.valor_atipico IS 'true se o valor global passa do limite de valor atípico (provável erro de digitação).';
COMMENT ON COLUMN painel.vw_contratos.dias_assinatura_publicacao IS 'Dias entre a assinatura e a publicação no PNCP.';
COMMENT ON COLUMN painel.vw_contratos.vigente_na_data_corte_kit IS 'Marca S/N calculada pela organização do kit (usada para conferir a nossa regra de vigência).';

-- ---------------------------------------------------------------------------
-- 2. vw_concentracao — 1 linha por OM x fornecedor (valor VIGENTE de despesa)
-- ---------------------------------------------------------------------------
CREATE VIEW painel.vw_concentracao AS
WITH p AS (SELECT * FROM painel.parametro WHERE id = 1),
v AS (
    SELECT * FROM painel.vw_contratos
    WHERE situacao = 'vigente' AND NOT gera_receita AND NOT valor_atipico AND valor_global > 0
),
f AS (
    SELECT unidade_codigo, max(unidade_nome) AS unidade_nome, max(unidade_uf) AS unidade_uf,
           fornecedor_chave, max(fornecedor_nome) AS fornecedor_nome, max(tipo_pessoa) AS tipo_pessoa,
           count(*)                                                       AS qtd_instrumentos,
           count(*) FILTER (WHERE familia_instrumento = 'Contrato')       AS qtd_contratos,
           count(*) FILTER (WHERE familia_instrumento = 'Empenho')        AS qtd_empenhos,
           sum(valor_global)                                              AS valor_vigente,
           string_agg(DISTINCT categoria_processo, ', ')                  AS categorias_processo,
           min(vigencia_fim)                                              AS proximo_vencimento
    FROM v GROUP BY unidade_codigo, fornecedor_chave
),
t AS (
    SELECT unidade_codigo, sum(valor_vigente) AS valor_unidade,
           sum(qtd_instrumentos) AS instrumentos_unidade, count(*) AS fornecedores_unidade
    FROM f GROUP BY unidade_codigo
)
SELECT f.unidade_codigo, f.unidade_nome, f.unidade_uf,
       f.fornecedor_chave, f.fornecedor_nome, f.tipo_pessoa,
       f.qtd_instrumentos, f.qtd_contratos, f.qtd_empenhos, f.valor_vigente,
       f.categorias_processo, f.proximo_vencimento,
       t.valor_unidade, t.instrumentos_unidade::int AS instrumentos_unidade, t.fornecedores_unidade::int AS fornecedores_unidade,
       round(100 * f.valor_vigente / t.valor_unidade, 1)                           AS participacao_pct,
       rank() OVER (PARTITION BY f.unidade_codigo ORDER BY f.valor_vigente DESC)::int AS posicao_na_unidade,
       round(sum((100 * f.valor_vigente / t.valor_unidade) ^ 2)
             OVER (PARTITION BY f.unidade_codigo))::int                             AS hhi_unidade,
       (100 * f.valor_vigente / t.valor_unidade >= p.limiar_participacao_pct
        AND t.instrumentos_unidade >= p.min_instrumentos_unidade)                  AS alerta_concentracao
FROM f JOIN t USING (unidade_codigo) CROSS JOIN p;

COMMENT ON VIEW painel.vw_concentracao IS
  'Uma linha por OM x fornecedor: participação do fornecedor (CNPJ raiz) no valor vigente de despesa da OM, com HHI da OM. '
  'Exclui receitas e valores atípicos. Mistura objetos diferentes: participação alta indica dependência a verificar, não irregularidade.';
COMMENT ON COLUMN painel.vw_concentracao.participacao_pct IS 'Percentual do valor vigente da OM que está com este fornecedor.';
COMMENT ON COLUMN painel.vw_concentracao.posicao_na_unidade IS '1 = fornecedor com maior valor vigente na OM.';
COMMENT ON COLUMN painel.vw_concentracao.hhi_unidade IS 'Índice Herfindahl-Hirschman da OM (0 a 10.000): soma dos quadrados das participações; quanto maior, mais concentrada.';
COMMENT ON COLUMN painel.vw_concentracao.alerta_concentracao IS 'true se a participação passa do limiar (padrão 40%) numa OM com pelo menos 3 instrumentos vigentes.';
COMMENT ON COLUMN painel.vw_concentracao.proximo_vencimento IS 'Fim de vigência mais próximo entre os instrumentos deste fornecedor na OM.';

-- ---------------------------------------------------------------------------
-- 3. vw_concentracao_marinha — 1 linha por fornecedor, somando todas as OM
-- ---------------------------------------------------------------------------
CREATE VIEW painel.vw_concentracao_marinha AS
WITH v AS (
    SELECT * FROM painel.vw_contratos
    WHERE situacao = 'vigente' AND NOT gera_receita AND NOT valor_atipico AND valor_global > 0
),
f AS (
    SELECT fornecedor_chave, max(fornecedor_nome) AS fornecedor_nome, max(tipo_pessoa) AS tipo_pessoa,
           count(DISTINCT unidade_codigo) AS unidades_atendidas, count(*) AS qtd_instrumentos,
           sum(valor_global) AS valor_vigente
    FROM v GROUP BY fornecedor_chave
)
SELECT f.*,
       round(100 * valor_vigente / sum(valor_vigente) OVER (), 2)        AS participacao_marinha_pct,
       rank() OVER (ORDER BY valor_vigente DESC)::int                    AS posicao
FROM f;
COMMENT ON VIEW painel.vw_concentracao_marinha IS
  'Uma linha por fornecedor (CNPJ raiz): valor vigente de despesa na Marinha inteira, OM atendidas e participação no total.';

-- ---------------------------------------------------------------------------
-- 4. vw_alertas — todos os alertas numa lista só (fonte do painel fixo)
-- ---------------------------------------------------------------------------
CREATE VIEW painel.vw_alertas AS
SELECT 'Vence em até 90 dias' AS tipo_alerta, 1 AS gravidade, familia_instrumento,
       unidade_codigo, unidade_nome, numero_controle_pncp AS referencia, objeto, fornecedor_nome,
       valor_global AS valor, dias_para_vencer,
       'Vence em ' || dias_para_vencer || ' dias (' || to_char(vigencia_fim, 'DD/MM/YYYY')
         || '): decidir entre prorrogação e nova contratação.' AS mensagem
FROM painel.vw_contratos WHERE situacao = 'vigente' AND dias_para_vencer <= 90
UNION ALL
SELECT 'Vence em 91 a 180 dias', 2, familia_instrumento, unidade_codigo, unidade_nome,
       numero_controle_pncp, objeto, fornecedor_nome, valor_global, dias_para_vencer,
       'Vence em ' || dias_para_vencer || ' dias (' || to_char(vigencia_fim, 'DD/MM/YYYY')
         || '): iniciar o planejamento da próxima contratação.'
FROM painel.vw_contratos WHERE situacao = 'vigente' AND dias_para_vencer BETWEEN 91 AND 180
UNION ALL
SELECT 'Encerrado há até 90 dias', 1, familia_instrumento, unidade_codigo, unidade_nome,
       numero_controle_pncp, objeto, fornecedor_nome, valor_global, dias_para_vencer,
       'Encerrou há ' || (-dias_para_vencer) || ' dias: verificar se foi prorrogado ou reposto '
         || '(o kit não traz aditivos).'
FROM painel.vw_contratos WHERE verificar_reposicao
UNION ALL
SELECT 'Fornecedor concentrado na OM', 2, NULL, unidade_codigo, unidade_nome,
       fornecedor_chave, categorias_processo, fornecedor_nome, valor_vigente,
       (proximo_vencimento - (SELECT data_referencia FROM painel.parametro WHERE id = 1)),
       fornecedor_nome || ' concentra ' || participacao_pct || '% do valor vigente da OM ('
         || qtd_instrumentos || ' de ' || instrumentos_unidade || ' instrumentos): avaliar dependência.'
FROM painel.vw_concentracao WHERE alerta_concentracao
UNION ALL
SELECT 'Qualidade do dado', 3, familia_instrumento, unidade_codigo, unidade_nome,
       numero_controle_pncp, objeto, fornecedor_nome, valor_global, dias_para_vencer,
       concat_ws('; ',
         CASE WHEN situacao = 'sem data de fim' THEN 'sem data de fim de vigência' END,
         CASE WHEN vigencia_anomala THEN 'vigência acima de 10 anos' END,
         CASE WHEN valor_atipico THEN 'valor global atípico' END) || ': conferir o registro no PNCP.'
FROM painel.vw_contratos
WHERE situacao = 'sem data de fim' OR vigencia_anomala OR valor_atipico;

COMMENT ON VIEW painel.vw_alertas IS
  'Lista única de alertas: vencimento (até 90 e 91-180 dias), encerrado há até 90 dias (verificar reposição), '
  'fornecedor concentrado na OM e qualidade do dado. gravidade 1 = agir já, 2 = planejar, 3 = conferir dado. '
  'referencia = numero_controle_pncp do instrumento (ou fornecedor_chave no alerta de concentração).';
COMMENT ON COLUMN painel.vw_alertas.tipo_alerta IS 'Vence em até 90 dias | Vence em 91 a 180 dias | Encerrado há até 90 dias | Fornecedor concentrado na OM | Qualidade do dado.';
COMMENT ON COLUMN painel.vw_alertas.gravidade IS '1 = agir já, 2 = planejar, 3 = conferir o dado.';
COMMENT ON COLUMN painel.vw_alertas.referencia IS 'numero_controle_pncp do instrumento; no alerta de concentração, fornecedor_chave.';
COMMENT ON COLUMN painel.vw_alertas.mensagem IS 'Texto pronto do alerta, com o prazo ou a participação calculados.';
