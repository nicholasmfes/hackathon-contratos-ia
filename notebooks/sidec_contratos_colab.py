"""
SIDEC-MB -> Hackathon C-ApA-IM | Desafio 4 - Gestao e acompanhamento de contratos
==================================================================================
Porta para pandas (Colab) a logica do Modulo M3 - Ciclo de Vida Contratual e do
ISC - Indice de Saude Contratual do SIDEC-MB, sobre a tabela de contratos do kit.

Divisao de trabalho (mesma ideia da Parte 8 do Tutorial 2):
    PYTHON calcula tudo o que e numero (dias, faixas, participacao, ISC);
    a IA (Gemini) so ESCREVE o resumo para o fiscal e o gestor;
    o PYTHON CONFERE se os numeros que a IA citou batem com os calculados.

Uso no Colab (depois da celula 0.2 do Tutorial 2, que cria `cliente` e `MODELO`):

    import pandas as pd, sidec_contratos_colab as sc
    bruto = pd.read_csv("/content/kit/.../contratos.csv", dtype=str, keep_default_na=False)
    c = sc.preparar(bruto)                  # mapeia colunas e calcula tudo (vivo)
    sc.visao_geral(c)                       # carteira por janela de vencimento
    sc.alertas_vencimento(c)                # vigentes que vencem em ate 180 dias
    conc = sc.concentracao(c)               # participacao do fornecedor em cada UASG
    sc.alertas_concentracao(conc)
    fila = sc.prioridade(c).head(10)        # menor ISC primeiro
    res = sc.resumir_lote(cliente, MODELO, fila, conc)   # IA escreve + Python confere

Limitacoes declaradas (herdadas do SIDEC-MB):
    - Desempenho (25) e Conformidade (15) nao tem fonte no kit -> NEUTROS (12,5 e 7,5).
      "Nao verificado nunca vale nota cheia": o teto do ISC aqui e 80. A ORDEM de
      prioridade nao muda (o neutro e constante para todos).
    - Sucessao contratual NAO e inferida dos dados (testado e reprovado no SIDEC-MB):
      contrato encerrado ha ate 90 dias vira "verificar reposicao", nunca "reposto".
    - Variacao valor global / inicial inclui prorrogacoes: nao e, por si, acrescimo
      do art. 125. Serve de sinal para o fiscal olhar, nao de conclusao.
    - Nada aqui conclui irregularidade ou fraude (fora do escopo do desafio).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------------------
# 1. Mapeamento de colunas (nomes da API do PNCP + variantes "achatadas" do kit)
# ---------------------------------------------------------------------------
CANDIDATAS = {
    "numero_controle": ["numeroControlePNCP", "numeroControlePncp", "numero_controle_pncp"],
    "numero_controle_compra": ["numeroControlePncpCompra", "numeroControlePNCPCompra"],
    "numero_contrato": ["numeroContratoEmpenho", "numero_contrato_empenho", "numeroContrato"],
    "objeto": ["objetoContrato", "objeto_contrato", "objeto"],
    "tipo_contrato": ["tipoContratoNome", "tipoContrato_nome", "tipo_contrato", "tipoContrato"],
    "categoria": ["categorias_intendencia", "categoria_intendencia"],
    "uasg": ["unidade_codigo", "codigoUnidade", "unidadeOrgao_codigoUnidade", "codigo_uasg"],
    "uasg_nome": ["unidade_nome", "nomeUnidade", "unidadeOrgao_nomeUnidade", "nome_uasg"],
    "uf": ["unidade_uf", "ufSigla", "unidadeOrgao_ufSigla", "uf"],
    "eh_marinha": ["eh_marinha", "is_marinha"],
    "ni_fornecedor": ["niFornecedor", "ni_fornecedor", "cnpjFornecedor"],
    "tipo_pessoa": ["tipoPessoa", "tipo_pessoa"],
    "fornecedor": ["nomeRazaoSocialFornecedor", "razao_social_fornecedor", "fornecedor"],
    "valor_inicial": ["valorInicial", "valor_inicial"],
    "valor_global": ["valorGlobal", "valor_global", "valorGlobalAtual"],
    "vig_inicio": ["dataVigenciaInicio", "vigencia_inicio", "data_vigencia_inicio"],
    "vig_fim": ["dataVigenciaFim", "vigencia_fim", "data_vigencia_fim"],
    "assinatura": ["dataAssinatura", "data_assinatura"],
    "publicacao": ["dataPublicacaoPncp", "data_publicacao_pncp"],
}
OBRIGATORIAS = ["numero_controle", "ni_fornecedor", "vig_fim", "valor_global", "uasg"]

# Parametrizacao inicial do ISC no SIDEC-MB (sidec_mb_parametros_isc.py, 20/07/2026)
ISC = {
    "DESEMPENHO": {"peso": 25, "frac_neutro": 0.5},
    "SITUACAO": {"peso": 25, "frac_vigente": 1.0, "frac_encerrado": 0.6, "frac_outro": 0.2},
    "VENCIMENTO": {"peso": 20, "faixas": [(180, 1.0), (90, 0.7), (60, 0.4)], "frac_padrao": 0.15},
    "ADITIVOS": {"peso": 15, "fator_penalidade": 2.0},
    "CONFORMIDADE": {"peso": 15, "frac_neutro": 0.5},
}
LIMIAR_PARTICIPACAO_PCT = 40   # provisorio: fatia do valor vigente da UASG
MIN_CONTRATOS_UASG = 3         # evita "100%" trivial em UASG com 1 contrato
JANELA_REPOSICAO_DIAS = 90     # encerrado ha ate 90 dias: verificar reposicao


def mapear_colunas(df: pd.DataFrame) -> dict:
    """Escolhe, para cada campo canonico, a primeira coluna candidata que existir."""
    por_minuscula = {c.lower(): c for c in df.columns}
    mapa = {}
    for campo, opcoes in CANDIDATAS.items():
        for op in opcoes:
            if op.lower() in por_minuscula:
                mapa[campo] = por_minuscula[op.lower()]
                break
    faltando = [c for c in OBRIGATORIAS if c not in mapa]
    print("Mapeamento de colunas:")
    for campo in CANDIDATAS:
        print(f"  {campo:<24} <- {mapa.get(campo, '(ausente)')}")
    if faltando:
        raise KeyError(
            f"Faltam colunas obrigatorias: {faltando}. Veja df.columns e acrescente o "
            f"nome real em sc.CANDIDATAS['<campo>'] antes de rodar de novo.")
    return mapa


def _num(s: pd.Series) -> pd.Series:
    s = s.astype(str).str.strip()
    v = pd.to_numeric(s, errors="coerce")
    # fallback para decimal com virgula (ex.: "1.234,56")
    if v.isna().mean() > 0.5:
        v = pd.to_numeric(s.str.replace(".", "", regex=False)
                           .str.replace(",", ".", regex=False), errors="coerce")
    return v


def _data(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str).str.slice(0, 10), errors="coerce", format="%Y-%m-%d")


def _raiz(ni: str, tipo_pessoa: str) -> str:
    """CNPJ raiz (8 primeiros caracteres; CNPJ alfanumerico -> texto). PF fica isolada."""
    ni = re.sub(r"[^0-9A-Za-z]", "", str(ni or "")).upper()
    if str(tipo_pessoa).upper() == "PF" or len(ni) == 11:
        return "PF-" + hashlib.sha1(ni.encode()).hexdigest()[:8]
    return ni[:8] if ni else "SEM-NI"


# ---------------------------------------------------------------------------
# 2. Preparacao: tudo VIVO em relacao a data de referencia (decisao n. 6 SIDEC-MB)
# ---------------------------------------------------------------------------
def preparar(bruto: pd.DataFrame, data_referencia=None) -> pd.DataFrame:
    m = mapear_colunas(bruto)
    hoje = pd.Timestamp(data_referencia) if data_referencia else pd.Timestamp.today().normalize()
    col = lambda campo: bruto[m[campo]] if campo in m else pd.Series([""] * len(bruto), index=bruto.index)

    c = pd.DataFrame({
        "numero_controle_pncp": col("numero_controle").astype(str),
        "numero_controle_compra": col("numero_controle_compra").astype(str),
        "numero_contrato": col("numero_contrato").astype(str),
        "objeto": col("objeto").astype(str),
        "tipo_contrato": col("tipo_contrato").astype(str),
        "categoria": col("categoria").astype(str),
        "uasg": col("uasg").astype(str),
        "uasg_nome": col("uasg_nome").astype(str),
        "uf": col("uf").astype(str),
        "eh_marinha": col("eh_marinha").astype(str),
        "ni_fornecedor": col("ni_fornecedor").astype(str),
        "tipo_pessoa": col("tipo_pessoa").astype(str),
        "fornecedor": col("fornecedor").astype(str),
        "valor_inicial": _num(col("valor_inicial")),
        "valor_global": _num(col("valor_global")),
        "vigencia_inicio": _data(col("vig_inicio")),
        "vigencia_fim": _data(col("vig_fim")),
        "assinatura": _data(col("assinatura")),
        "publicacao": _data(col("publicacao")),
    })
    c["data_referencia"] = hoje
    c["dias_para_vencer"] = (c["vigencia_fim"] - hoje).dt.days
    c["situacao"] = "vigente"                      # sem data de fim conta como vigente (igual ao ETL)
    c.loc[c["dias_para_vencer"] < 0, "situacao"] = "encerrado"

    def janela(r):
        if r["situacao"] == "vigente" and pd.isna(r["dias_para_vencer"]):
            return "0. Sem data de termino"
        if r["situacao"] == "vigente" and r["dias_para_vencer"] <= 90:
            return "1. Vence em ate 90 dias"
        if r["situacao"] == "vigente" and r["dias_para_vencer"] <= 180:
            return "2. Vence em 91-180 dias"
        if r["situacao"] == "vigente":
            return "3. Vigencia > 180 dias"
        return "4. Encerrado"
    c["janela_vencimento"] = c.apply(janela, axis=1)
    c["verificar_reposicao"] = (c["situacao"] == "encerrado") & (c["dias_para_vencer"] >= -JANELA_REPOSICAO_DIAS)
    c["no_acompanhamento"] = (c["situacao"] == "vigente") | c["verificar_reposicao"]

    vi, vg = c["valor_inicial"], c["valor_global"]
    c["variacao_valor"] = (vg / vi - 1).where(vi > 0).clip(-9.99, 9.99)
    c["vigencia_total_dias"] = (c["vigencia_fim"] - c["vigencia_inicio"]).dt.days
    c["vigencia_anomala"] = c["vigencia_total_dias"] > 3653          # A3: > 10 anos
    c["dias_ate_publicacao"] = (c["publicacao"] - c["assinatura"]).dt.days  # S3 (limiar provisorio 20)
    c["raiz_fornecedor"] = [_raiz(n, t) for n, t in zip(c["ni_fornecedor"], c["tipo_pessoa"])]
    c["eh_pf"] = c["raiz_fornecedor"].str.startswith("PF-")

    _calcular_isc(c)
    return c


def _calcular_isc(c: pd.DataFrame) -> None:
    p = ISC
    c["c_desempenho"] = p["DESEMPENHO"]["peso"] * p["DESEMPENHO"]["frac_neutro"]
    c["c_situacao"] = p["SITUACAO"]["peso"] * c["situacao"].map(
        {"vigente": p["SITUACAO"]["frac_vigente"], "encerrado": p["SITUACAO"]["frac_encerrado"]}
    ).fillna(p["SITUACAO"]["frac_outro"])

    def frac_venc(d):
        if pd.isna(d):
            return p["VENCIMENTO"]["frac_padrao"]
        for dias_acima, frac in p["VENCIMENTO"]["faixas"]:
            if d > dias_acima:
                return frac
        return p["VENCIMENTO"]["frac_padrao"]
    c["c_vencimento"] = p["VENCIMENTO"]["peso"] * c["dias_para_vencer"].map(frac_venc)
    # teto 1.0: supressao (variacao negativa) nao pode render mais que 15
    frac_adit = (1 - c["variacao_valor"].fillna(0) * p["ADITIVOS"]["fator_penalidade"]).clip(0, 1)
    c["c_aditivos"] = p["ADITIVOS"]["peso"] * frac_adit
    c["c_conformidade"] = p["CONFORMIDADE"]["peso"] * p["CONFORMIDADE"]["frac_neutro"]
    comp = ["c_desempenho", "c_situacao", "c_vencimento", "c_aditivos", "c_conformidade"]
    c[comp] = c[comp].round(1)
    c["isc"] = c[comp].sum(axis=1).round(1)
    c["faixa_isc"] = pd.cut(c["isc"], [-1, 40, 60, 80, 101],
                            labels=["1. Critico (0-40)", "2. Atencao (41-60)",
                                    "3. Adequado (61-80)", "4. Excelente (81-100)"]).astype(str)


# ---------------------------------------------------------------------------
# 3. Paineis e alertas (so pandas: sao fatos, nao opiniao)
# ---------------------------------------------------------------------------
def visao_geral(c: pd.DataFrame) -> pd.DataFrame:
    return (c.groupby("janela_vencimento")
             .agg(contratos=("numero_controle_pncp", "count"), valor_global=("valor_global", "sum"))
             .reset_index())


def alertas_vencimento(c: pd.DataFrame, ate_dias: int = 180) -> pd.DataFrame:
    a = c[(c["situacao"] == "vigente") & (c["dias_para_vencer"] <= ate_dias)].copy()
    a["alerta"] = a["dias_para_vencer"].map(
        lambda d: "VENCE EM ATE 90 DIAS - decidir prorrogacao ou nova contratacao"
        if d <= 90 else "VENCE EM 91-180 DIAS - iniciar planejamento")
    return a.sort_values("dias_para_vencer")[
        ["numero_controle_pncp", "uasg", "uasg_nome", "objeto", "fornecedor",
         "vigencia_fim", "dias_para_vencer", "valor_global", "alerta"]]


def alertas_reposicao(c: pd.DataFrame) -> pd.DataFrame:
    """Encerrados ha ate 90 dias: a sucessao nao e inferida -> o gestor confirma."""
    return c[c["verificar_reposicao"]].sort_values("dias_para_vencer")[
        ["numero_controle_pncp", "uasg", "uasg_nome", "objeto", "fornecedor",
         "vigencia_fim", "dias_para_vencer", "valor_global"]]


def concentracao(c: pd.DataFrame, por_categoria: bool = False) -> pd.DataFrame:
    """Participacao de cada fornecedor (CNPJ raiz) no valor VIGENTE de cada UASG + HHI."""
    v = c[c["situacao"] == "vigente"].copy()
    chave = ["uasg"] + (["categoria"] if por_categoria else [])
    g = (v.groupby(chave + ["raiz_fornecedor"])
          .agg(fornecedor=("fornecedor", "first"), eh_pf=("eh_pf", "first"),
               qtd_contratos=("numero_controle_pncp", "count"),
               valor_vigente=("valor_global", "sum"))
          .reset_index())
    tot = v.groupby(chave).agg(valor_uasg=("valor_global", "sum"),
                               contratos_uasg=("numero_controle_pncp", "count"),
                               uasg_nome=("uasg_nome", "first")).reset_index()
    g = g.merge(tot, on=chave)
    g["participacao_pct"] = (100 * g["valor_vigente"] / g["valor_uasg"]).round(1)
    hhi = (g.assign(q=g["participacao_pct"] ** 2).groupby(chave)["q"].sum().round(0)
            .rename("hhi_uasg").reset_index())
    g = g.merge(hhi, on=chave)
    g["fornecedores_uasg"] = g.groupby(chave)["raiz_fornecedor"].transform("count")
    return g.sort_values(chave + ["participacao_pct"], ascending=[True] * len(chave) + [False])


def alertas_concentracao(conc: pd.DataFrame, limiar_pct: float = LIMIAR_PARTICIPACAO_PCT) -> pd.DataFrame:
    a = conc[(conc["participacao_pct"] >= limiar_pct) & (conc["contratos_uasg"] >= MIN_CONTRATOS_UASG)]
    return a.sort_values("participacao_pct", ascending=False)


def prioridade(c: pd.DataFrame) -> pd.DataFrame:
    """Fila de atencao: em acompanhamento, menor ISC primeiro, depois o que vence antes."""
    f = c[c["no_acompanhamento"]].copy()
    return f.sort_values(["isc", "dias_para_vencer", "valor_global"], ascending=[True, True, False])


# ---------------------------------------------------------------------------
# 4. IA generativa: resumo para fiscal e gestor + conferencia pelo Python
# ---------------------------------------------------------------------------
ESQUEMA_RESUMO = {
    "type": "object",
    "properties": {
        "numero_controle_pncp": {"type": "string"},
        "resumo_para_fiscal": {"type": "string"},
        "resumo_para_gestor": {"type": "string"},
        "pontos_de_atencao": {"type": "array", "items": {"type": "string"}},
        "acao_sugerida": {"type": "string"},
        "valor_global_citado": {"type": "number"},
        "dias_para_vencer_citado": {"type": "integer"},
        "participacao_fornecedor_pct_citada": {"type": "number"},
        "isc_citado": {"type": "number"},
    },
    "required": ["numero_controle_pncp", "resumo_para_fiscal", "resumo_para_gestor",
                 "pontos_de_atencao", "acao_sugerida", "valor_global_citado",
                 "dias_para_vencer_citado", "participacao_fornecedor_pct_citada", "isc_citado"],
}

INSTRUCAO_SISTEMA = (
    "Voce apoia gestores e fiscais de contratos da Marinha do Brasil (Lei 14.133/2021). "
    "Use SOMENTE os dados do JSON recebido; nao invente datas, valores, clausulas ou fatos. "
    "Nao conclua sobre irregularidade, fraude ou culpa: aponte apenas o que merece verificacao. "
    "Nos campos *_citado copie EXATAMENTE os numeros dos campos de origem indicados. "
    "Resumo para o fiscal: o que acompanhar na execucao. Resumo para o gestor: decisao a tomar e prazo. "
    "Escreva em portugues do Brasil, frases curtas. Termine a acao sugerida recomendando revisao humana."
)


def fatos_do_contrato(linha: pd.Series, conc: pd.DataFrame | None = None) -> dict:
    """Pacote de fatos que vai para a IA. PF: CPF e nome omitidos (Art. 5 do regulamento)."""
    part = None
    if conc is not None:
        hit = conc[(conc["uasg"] == linha["uasg"]) & (conc["raiz_fornecedor"] == linha["raiz_fornecedor"])]
        part = float(hit["participacao_pct"].iloc[0]) if len(hit) else 0.0
    f = {
        "numero_controle_pncp": linha["numero_controle_pncp"],
        "uasg": f'{linha["uasg"]} - {linha["uasg_nome"]}',
        "objeto": linha["objeto"],
        "tipo_contrato": linha["tipo_contrato"],
        "fornecedor": "Pessoa fisica (dados omitidos)" if linha["eh_pf"] else linha["fornecedor"],
        "vigencia_fim": None if pd.isna(linha["vigencia_fim"]) else str(linha["vigencia_fim"].date()),
        "dias_para_vencer": None if pd.isna(linha["dias_para_vencer"]) else int(linha["dias_para_vencer"]),
        "janela_vencimento": linha["janela_vencimento"],
        "situacao": linha["situacao"],
        "verificar_reposicao": bool(linha["verificar_reposicao"]),
        "valor_inicial": None if pd.isna(linha["valor_inicial"]) else round(float(linha["valor_inicial"]), 2),
        "valor_global": None if pd.isna(linha["valor_global"]) else round(float(linha["valor_global"]), 2),
        "variacao_valor_pct": None if pd.isna(linha["variacao_valor"]) else round(100 * float(linha["variacao_valor"]), 1),
        "participacao_fornecedor_na_uasg_pct": part,
        "isc": float(linha["isc"]),
        "faixa_isc": linha["faixa_isc"],
        "observacoes_de_metodo": [
            "Desempenho e conformidade sem fonte no kit: valores neutros (teto do ISC = 80).",
            "Variacao do valor global inclui prorrogacoes; nao e, por si, acrescimo contratual.",
            "Sucessao do contrato nao e inferida dos dados.",
        ],
    }
    return f


def _chamar(cliente, modelo, fatos: dict, tentativas: int = 3) -> dict:
    pedido = "Gere o resumo do contrato a partir destes dados:\n" + json.dumps(fatos, ensure_ascii=False)
    for t in range(tentativas):
        try:
            r = cliente.interactions.create(
                model=modelo, input=pedido, system_instruction=INSTRUCAO_SISTEMA,
                response_format={"type": "text", "mime_type": "application/json",
                                 "schema": ESQUEMA_RESUMO})
            return json.loads(r.output_text)
        except Exception as erro:  # 429 = limite do plano gratuito; JSON invalido = tenta de novo
            if "429" in str(erro) or "RESOURCE_EXHAUSTED" in str(erro):
                print("Limite de uso (429). Esperando 30 s...")
                time.sleep(30)
            elif isinstance(erro, json.JSONDecodeError) and t < tentativas - 1:
                time.sleep(2)
            else:
                raise
    raise RuntimeError("Sem resposta da IA apos as tentativas.")


def conferir(resumo: dict, fatos: dict) -> list[str]:
    """O Python confere os numeros que a IA citou. Lista vazia = conferido."""
    div = []
    if resumo.get("numero_controle_pncp") != fatos["numero_controle_pncp"]:
        div.append("numero de controle diferente do registro")
    pares = [("valor_global_citado", fatos["valor_global"], 0.01),
             ("dias_para_vencer_citado", fatos["dias_para_vencer"], 0),
             ("participacao_fornecedor_pct_citada", fatos["participacao_fornecedor_na_uasg_pct"], 0.1),
             ("isc_citado", fatos["isc"], 0.1)]
    for campo, esperado, tol in pares:
        if esperado is None:
            continue
        citado = resumo.get(campo)
        if citado is None or abs(float(citado) - float(esperado)) > tol:
            div.append(f"{campo}: IA citou {citado}, registro = {esperado}")
    return div


def resumir_lote(cliente, modelo, linhas: pd.DataFrame, conc: pd.DataFrame | None = None,
                 cache: str = "cache_resumos.json", pausa_s: float = 5) -> pd.DataFrame:
    """Resume cada contrato (com cache em disco para nao gastar cota de novo) e confere."""
    arq = Path(cache)
    memo = json.loads(arq.read_text(encoding="utf-8")) if arq.exists() else {}
    saida = []
    for _, linha in linhas.iterrows():
        fatos = fatos_do_contrato(linha, conc)
        chave = hashlib.sha1(json.dumps(fatos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if chave not in memo:
            memo[chave] = _chamar(cliente, modelo, fatos)
            arq.write_text(json.dumps(memo, ensure_ascii=False, indent=1), encoding="utf-8")
            time.sleep(pausa_s)
        resumo = memo[chave]
        div = conferir(resumo, fatos)
        saida.append({**fatos, **{k: resumo.get(k) for k in
                      ["resumo_para_fiscal", "resumo_para_gestor", "pontos_de_atencao", "acao_sugerida"]},
                      "conferencia": "conferido" if not div else "DIVERGENTE: " + "; ".join(div)})
    return pd.DataFrame(saida)


def taxa_conferencia(resumos: pd.DataFrame) -> str:
    ok = (resumos["conferencia"] == "conferido").sum()
    return f"A IA citou corretamente os numeros em {ok} de {len(resumos)} resumos ({100*ok/len(resumos):.0f}%)."
