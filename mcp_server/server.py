"""
Servidor MCP do Desafio 4 — ferramentas do "Mentor do Fiscal".

Traduz o sistema (views do schema `painel` no PostgreSQL) em ferramentas MCP
que qualquer agente compatível usa. Aqui, quem usa é o agente do Google ADK.

Regras que valem para todas as ferramentas:
- Todo número sai do banco; a IA só escreve texto (RN-11).
- Usuário do banco `agente_ro`: só leitura, só o schema `painel`, sem dado
  pessoal (RN-10, RN-16, Art. 5º do regulamento).
- Carteira padrão = contratos (contrato termo inicial e carta contrato);
  empenhos só quando pedidos (RN-02).
- Toda resposta leva a fonte e a limitação do kit (RN-15).
"""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

PASTA_SAIDA = Path(os.getenv("PASTA_SAIDA", "/app/saida"))
PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
URL_ARQUIVOS = os.getenv("URL_ARQUIVOS", "http://localhost:8080").rstrip("/")
GUIA = Path(__file__).with_name("guia_fiscal.md")

FONTE = ("Fonte: kit de dados do PNCP fornecido pela organização do hackathon, "
         "base congelada em 31/10/2026 (contratos da Marinha).")
LIMITACAO = ("Limitação: o kit não traz aditivos, prorrogações, execução, pagamentos "
             "nem o nome dos fiscais; um contrato prorrogado pode aparecer como encerrado.")
SELO = "Subsídio gerado com apoio de IA — revisar antes de usar (Art. 2º, parágrafo único)."

ACAO_FAIXA = {
    "1. Vence em até 90 dias": "Agir já: avisar o gestor e decidir entre prorrogação e nova contratação.",
    "2. Vence em 91 a 180 dias": "Planejar: avisar o gestor para iniciar o planejamento da próxima contratação.",
    "3. Vence em mais de 180 dias": "Acompanhar a execução normalmente.",
    "4. A iniciar": "Vigência ainda não começou: conferir a data de início com o gestor.",
    "5. Encerrado": "Encerrado: verificar no processo se foi prorrogado ou substituído.",
    "0. Sem data de fim": "Registro sem data de fim: conferir no PNCP e no processo.",
}

mcp = MCPServer(
    name="contratos-marinha",
    instructions=("Ferramentas sobre os contratos da Marinha publicados no PNCP (kit do hackathon, "
                  "corte em 31/10/2026) e um guia prático para o fiscal de contrato."),
)


# --------------------------------------------------------------------------- utilidades
def _conn():
    return psycopg.connect(row_factory=dict_row, autocommit=True, connect_timeout=10)


def _sql(query: str, params: dict | None = None) -> list[dict]:
    with _conn() as c, c.cursor() as cur:
        cur.execute(query, params or {})
        return [_limpa(r) for r in cur.fetchall()] if cur.description else []


def _limpa(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, Decimal):
            v = float(v)
        elif isinstance(v, (date, datetime)):
            v = v.isoformat()
        out[k] = v
    return out


def brl(v) -> str:
    if v is None:
        return "não informado"
    s = f"{float(v):,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def dbr(v) -> str:
    if not v:
        return "não informada"
    a, m, d = str(v)[:10].split("-")
    return f"{d}/{m}/{a}"


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode()
    return t.upper().strip()


NORM_SQL = ("translate(upper({}), 'ÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇ', "
            "'AAAAAEEEEIIIIOOOOOUUUUC')")


def link_pncp(numero_controle: str) -> str | None:
    m = re.match(r"^(\d{14})-2-(\d+)/(\d{4})$", numero_controle or "")
    if not m:
        return None
    return f"https://pncp.gov.br/app/contratos/{m.group(1)}/{m.group(3)}/{int(m.group(2))}"


def _data_ref() -> str:
    r = _sql("SELECT data_referencia FROM painel.parametro WHERE id = 1")
    return r[0]["data_referencia"] if r else "2026-10-31"


def _slug(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _norm(t).lower()).strip("_")[:40] or "arquivo"


def _filtro_familia(incluir_empenhos: bool) -> str:
    return "" if incluir_empenhos else " AND familia_instrumento = 'Contrato'"


# --------------------------------------------------------------------------- ferramentas
@mcp.tool()
def buscar_om(texto: str) -> dict:
    """Encontra a OM (unidade compradora da Marinha) pelo nome, pela cidade, pela UF ou pelo
    código UASG de 6 dígitos. Use SEMPRE antes das outras ferramentas quando o usuário disser
    a OM pelo nome (ex.: "Centro de Intendência de Natal", "Hospital Naval Marcílio Dias").
    Devolve o código (unidade_codigo) e quantos contratos a OM tem vigentes."""
    palavras = [p for p in re.split(r"\s+", _norm(texto)) if len(p) > 1 and p not in {"DA", "DE", "DO", "EM", "DAS", "DOS"}]
    if not palavras:
        return {"erro": "Informe parte do nome, a cidade ou o código da OM."}
    conds, params = [], {}
    for i, p in enumerate(palavras):
        params[f"p{i}"] = f"%{p}%"
        conds.append(f"({NORM_SQL.format('unidade_nome')} LIKE %(p{i})s OR "
                     f"{NORM_SQL.format('unidade_municipio')} LIKE %(p{i})s OR unidade_uf = %(u{i})s)")
        params[f"u{i}"] = p
    params["cod"] = texto.strip()
    linhas = _sql(f"""
        SELECT unidade_codigo, max(unidade_nome) AS nome, max(unidade_municipio) AS municipio,
               max(unidade_uf) AS uf,
               count(*) FILTER (WHERE familia_instrumento = 'Contrato' AND situacao = 'vigente') AS contratos_vigentes,
               count(*) FILTER (WHERE familia_instrumento = 'Contrato') AS contratos_no_kit
        FROM painel.vw_contratos
        WHERE unidade_codigo = %(cod)s OR ({' AND '.join(conds)})
        GROUP BY unidade_codigo
        ORDER BY contratos_vigentes DESC, contratos_no_kit DESC
        LIMIT 12""", params)
    return {"oms_encontradas": linhas, "quantidade": len(linhas),
            "dica": "Use o unidade_codigo nas próximas ferramentas.", "fonte": FONTE}


@mcp.tool()
def meus_contratos(unidade_codigo: str, busca: str = "", situacao: str = "vigentes",
                   incluir_empenhos: bool = False) -> dict:
    """Lista os contratos de uma OM para o fiscal conhecer a carteira que vai acompanhar.
    - unidade_codigo: código UASG da OM (use buscar_om antes, se precisar).
    - busca: filtra por palavra do objeto, nome do fornecedor ou número do contrato (opcional).
    - situacao: "vigentes" (padrão), "encerrados_recentes" (encerrados há até 90 dias) ou "todos".
    - incluir_empenhos: falso por padrão (a carteira padrão é só de contratos).
    Devolve os contratos ordenados pelo vencimento mais próximo, com a faixa de prazo."""
    params = {"u": unidade_codigo.strip()}
    filtro = _filtro_familia(incluir_empenhos)
    if situacao == "vigentes":
        filtro += " AND situacao = 'vigente'"
    elif situacao == "encerrados_recentes":
        filtro += " AND verificar_reposicao"
    if busca.strip():
        params["b"] = f"%{_norm(busca)}%"
        filtro += (f" AND ({NORM_SQL.format('objeto')} LIKE %(b)s OR "
                   f"{NORM_SQL.format('fornecedor_nome')} LIKE %(b)s OR {NORM_SQL.format('numero_contrato')} LIKE %(b)s)")
    linhas = _sql(f"""
        SELECT numero_controle_pncp, numero_contrato, tipo_instrumento, left(objeto, 160) AS objeto,
               fornecedor_nome, valor_global, vigencia_inicio, vigencia_fim, dias_para_vencer,
               janela_vencimento, situacao
        FROM painel.vw_contratos
        WHERE unidade_codigo = %(u)s {filtro}
        ORDER BY (situacao = 'vigente') DESC, abs(coalesce(dias_para_vencer, 99999))
        LIMIT 40""", params)
    resumo = _sql(f"""
        SELECT janela_vencimento, count(*) AS quantidade, sum(valor_global) AS valor_total
        FROM painel.vw_contratos WHERE unidade_codigo = %(u)s {filtro}
        GROUP BY 1 ORDER BY 1""", params)
    om = _sql("SELECT max(unidade_nome) AS nome FROM painel.vw_contratos WHERE unidade_codigo = %(u)s", params)
    for l in linhas:
        l["valor_global_fmt"] = brl(l["valor_global"])
        l["vigencia_fim_fmt"] = dbr(l["vigencia_fim"])
        l["acao"] = ACAO_FAIXA.get(l["janela_vencimento"], "")
        l["link_pncp"] = link_pncp(l["numero_controle_pncp"])
    return {"om": om[0]["nome"] if om else None, "unidade_codigo": unidade_codigo,
            "data_referencia": _data_ref(), "filtro": f"situação={situacao}; "
            + ("contratos e empenhos" if incluir_empenhos else "só contratos"),
            "resumo_por_faixa": resumo, "contratos": linhas,
            "mostrando": len(linhas), "fonte": FONTE, "limitacao": LIMITACAO}


def _ficha(identificador: str, unidade_codigo: str = "") -> dict:
    ident = identificador.strip()
    params = {"i": ident, "u": unidade_codigo.strip(), "n": f"%{_norm(ident)}%"}
    linhas = _sql("""SELECT * FROM painel.vw_contratos WHERE numero_controle_pncp = %(i)s""", params)
    if not linhas:
        extra = " AND unidade_codigo = %(u)s" if unidade_codigo.strip() else ""
        linhas = _sql(f"""SELECT * FROM painel.vw_contratos
                          WHERE {NORM_SQL.format('numero_contrato')} LIKE %(n)s {extra}
                          ORDER BY vigencia_fim DESC NULLS LAST LIMIT 10""", params)
    if not linhas:
        return {"erro": "Contrato não encontrado. Use o número de controle PNCP (ex.: 00394502000144-2-000123/2025) "
                        "ou o número do contrato com o código da OM."}
    if len(linhas) > 1:
        return {"varios_encontrados": [{k: l[k] for k in ("numero_controle_pncp", "numero_contrato", "unidade_nome",
                                                          "fornecedor_nome", "vigencia_fim")} for l in linhas],
                "dica": "Peça ao usuário qual deles e chame de novo com o numero_controle_pncp."}
    c = linhas[0]
    conc = _sql("""SELECT participacao_pct, posicao_na_unidade, qtd_instrumentos, fornecedores_unidade,
                          hhi_unidade, alerta_concentracao
                   FROM painel.vw_concentracao WHERE unidade_codigo = %(u)s AND fornecedor_chave = %(f)s""",
                {"u": c["unidade_codigo"], "f": c["fornecedor_chave"]})
    outros = _sql("""SELECT numero_controle_pncp, numero_contrato, left(objeto, 100) AS objeto, vigencia_fim, situacao
                     FROM painel.vw_contratos
                     WHERE unidade_codigo = %(u)s AND fornecedor_chave = %(f)s AND numero_controle_pncp <> %(i)s
                     ORDER BY vigencia_fim DESC NULLS LAST LIMIT 5""",
                  {"u": c["unidade_codigo"], "f": c["fornecedor_chave"], "i": c["numero_controle_pncp"]})
    pontos = []
    if c["verificar_reposicao"]:
        pontos.append("Encerrou há até 90 dias: verificar se houve prorrogação ou novo contrato.")
    if c["vigencia_anomala"]:
        pontos.append("Vigência acima de 10 anos no registro: provável erro de data; conferir no PNCP.")
    if c["valor_atipico"]:
        pontos.append("Valor global acima de R$ 1 bilhão: provável erro de digitação; conferir no PNCP.")
    if c["vigencia_fim"] is None:
        pontos.append("Registro sem data de fim de vigência: conferir no PNCP e no processo.")
    if (c["numero_retificacoes"] or 0) > 0:
        pontos.append(f"O registro foi retificado {c['numero_retificacoes']} vez(es) no PNCP: conferir a versão atual.")
    if conc and conc[0]["alerta_concentracao"]:
        pontos.append(f"O fornecedor concentra {conc[0]['participacao_pct']}% do valor vigente da OM: "
                      "dependência a avaliar com o gestor (não é indício de irregularidade).")
    return {
        "identificacao": {
            "numero_controle_pncp": c["numero_controle_pncp"], "numero_contrato": c["numero_contrato"],
            "tipo_instrumento": c["tipo_instrumento"], "categoria": c["categoria_processo"],
            "om": c["unidade_nome"], "unidade_codigo": c["unidade_codigo"],
            "cidade_uf": f"{c['unidade_municipio']}/{c['unidade_uf']}",
            "objeto": c["objeto"], "fornecedor": c["fornecedor_nome"],
            "contratacao_de_origem": c["numero_controle_compra"] or "não ligada no kit",
            "link_pncp": link_pncp(c["numero_controle_pncp"]),
        },
        "valores": {"valor_inicial": brl(c["valor_inicial"]), "valor_global": brl(c["valor_global"]),
                    "valor_parcela": brl(c["valor_parcela"]), "numero_parcelas": c["numero_parcelas"]},
        "prazos": {"assinatura": dbr(c["data_assinatura"]), "inicio_vigencia": dbr(c["vigencia_inicio"]),
                   "fim_vigencia": dbr(c["vigencia_fim"]), "data_referencia": dbr(c["data_referencia"]),
                   "dias_para_vencer": c["dias_para_vencer"], "situacao": c["situacao"],
                   "faixa": c["janela_vencimento"], "acao_indicada": ACAO_FAIXA.get(c["janela_vencimento"], "")},
        "registro_publico": {"publicado_no_pncp_em": dbr(c["data_publicacao"]),
                             "dias_entre_assinatura_e_publicacao": c["dias_assinatura_publicacao"],
                             "retificacoes": c["numero_retificacoes"]},
        "fornecedor_na_om": conc[0] if conc else None,
        "outros_contratos_do_fornecedor_na_om": outros,
        "pontos_de_atencao": pontos,
        "nao_consta_no_kit": ["ações e relatórios do fiscal anterior", "termos aditivos e apostilamentos",
                              "medições, notas fiscais e pagamentos", "garantia contratual", "nome do gestor e dos fiscais"],
        "fonte": FONTE, "limitacao": LIMITACAO,
    }


@mcp.tool()
def ficha_contrato(identificador: str, unidade_codigo: str = "") -> dict:
    """Ficha completa de UM contrato, para o fiscal entender o que vai acompanhar: identificação,
    objeto, fornecedor, valores, prazos, dias para vencer, faixa e ação indicada, o que consta no
    registro público (assinatura, publicação, retificações), participação do fornecedor na OM,
    outros contratos do mesmo fornecedor, pontos de atenção e o que NÃO consta no kit.
    - identificador: número de controle PNCP (ex.: 00394502000144-2-000123/2025) ou número do contrato.
    - unidade_codigo: opcional, ajuda quando se informa só o número do contrato."""
    return _ficha(identificador, unidade_codigo)


@mcp.tool()
def listar_alertas(unidade_codigo: str = "", tipo: str = "") -> dict:
    """Alertas prontos (sem SQL) de uma OM ou da Marinha inteira, com a ação indicada.
    - unidade_codigo: código UASG da OM; vazio = Marinha inteira.
    - tipo: "vencimento" (até 180 dias), "ate_90", "91_180", "encerrados" (encerrados há até 90 dias,
      verificar reposição), "concentracao" (fornecedor com 40% ou mais do valor vigente da OM),
      "qualidade" (registro a conferir) ou vazio (todos).
    Só contratos (empenhos ficam fora dos alertas de prazo)."""
    tipos = {"vencimento": ("Vence em até 90 dias", "Vence em 91 a 180 dias"),
             "ate_90": ("Vence em até 90 dias",), "91_180": ("Vence em 91 a 180 dias",),
             "encerrados": ("Encerrado há até 90 dias",), "concentracao": ("Fornecedor concentrado na OM",),
             "qualidade": ("Qualidade do dado",)}
    filtro, params = "(familia_instrumento = 'Contrato' OR tipo_alerta = 'Fornecedor concentrado na OM')", {}
    if unidade_codigo.strip():
        filtro += " AND unidade_codigo = %(u)s"
        params["u"] = unidade_codigo.strip()
    if tipo in tipos:
        filtro += " AND tipo_alerta = ANY(%(t)s)"
        params["t"] = list(tipos[tipo])
    contagem = _sql(f"SELECT tipo_alerta, count(*) AS quantidade FROM painel.vw_alertas WHERE {filtro} GROUP BY 1 ORDER BY 1", params)
    linhas = _sql(f"""SELECT tipo_alerta, gravidade, unidade_codigo, unidade_nome, referencia,
                             left(objeto, 120) AS objeto, fornecedor_nome, valor, dias_para_vencer, mensagem
                      FROM painel.vw_alertas WHERE {filtro}
                      ORDER BY gravidade, abs(coalesce(dias_para_vencer, 99999)) LIMIT 40""", params)
    for l in linhas:
        l["valor_fmt"] = brl(l["valor"])
        l["link_pncp"] = link_pncp(l["referencia"])
    return {"data_referencia": _data_ref(), "contagem_por_tipo": contagem, "alertas": linhas,
            "mostrando": len(linhas), "fonte": FONTE, "limitacao": LIMITACAO,
            "aviso": "Alerta é sinal para verificar, nunca conclusão de irregularidade."}


@mcp.tool()
def guia_do_fiscal(tema: str = "") -> dict:
    """Guia prático para o fiscal de contrato iniciante (texto conferido pela equipe, baseado na
    Lei 14.133/2021). Temas: "primeiros_passos" (o que fazer ao ser designado e o que pedir ao
    fiscal anterior), "rotina", "vencimento" (contrato perto de vencer), "ocorrencias" (registrar
    e cobrar a contratada), "recebimento", "limites" (o que não é papel do fiscal e quando escalar),
    "glossario". Vazio = lista os temas. Use para ensinar o passo a passo, em vez de responder de memória."""
    texto = GUIA.read_text(encoding="utf-8")
    secoes = dict(re.findall(r"^## (\S+)\n(.*?)(?=^## |\Z)", texto, flags=re.S | re.M))
    if not tema or tema not in secoes:
        return {"temas": list(secoes), "dica": "Chame de novo com um dos temas."}
    return {"tema": tema, "conteudo": secoes[tema].strip(),
            "aviso": "Orientação geral: confira no contrato, na norma e com o gestor ou a assessoria jurídica."}


@mcp.tool()
def gerar_relatorio(formato: str = "docx", numero_controle_pncp: str = "", unidade_codigo: str = "",
                    texto_analise: str = "", destinatario: str = "gestor do contrato") -> dict:
    """Gera documento pronto para o fiscal: relatório em Word (formato="docx") ou texto de e-mail
    (formato="email", o sistema NÃO envia; o usuário copia). Os números vêm do banco.
    - numero_controle_pncp: gera o relatório de UM contrato; ou
    - unidade_codigo: gera o relatório da carteira da OM (vencimentos e alertas).
    - texto_analise: escreva aqui a sua análise e as orientações ao fiscal (o que verificar, o que
      cobrar, próximos passos), em português claro. Vai marcado como subsídio gerado por IA.
    - destinatario: para quem é o documento (padrão: gestor do contrato).
    Devolve o link de download (Word) ou o assunto e o corpo (e-mail)."""
    if numero_controle_pncp.strip():
        f = _ficha(numero_controle_pncp)
        if "identificacao" not in f:
            return f
        idt, pz, vl = f["identificacao"], f["prazos"], f["valores"]
        titulo = f"Relatório do contrato {idt['numero_contrato']} — {idt['om']}"
        blocos = [
            ("Identificação", [("Nº de controle PNCP", idt["numero_controle_pncp"]), ("Contrato", idt["numero_contrato"]),
                               ("OM", f"{idt['om']} ({idt['unidade_codigo']})"), ("Objeto", idt["objeto"]),
                               ("Fornecedor", idt["fornecedor"]), ("Valor global", vl["valor_global"]),
                               ("Registro no PNCP", idt["link_pncp"] or "—")]),
            ("Prazo", [("Vigência", f"{pz['inicio_vigencia']} a {pz['fim_vigencia']}"),
                       ("Dias para vencer (ref. " + pz["data_referencia"] + ")", str(pz["dias_para_vencer"])),
                       ("Faixa", pz["faixa"]), ("Ação indicada", pz["acao_indicada"])]),
        ]
        listas = [("Pontos de atenção", f["pontos_de_atencao"] or ["Nenhum ponto de atenção no registro."]),
                  ("Não consta no kit (pedir no processo ou ao fiscal anterior)", f["nao_consta_no_kit"])]
        nome_base = f"relatorio_contrato_{_slug(idt['numero_contrato'] or idt['numero_controle_pncp'])}"
    elif unidade_codigo.strip():
        u = unidade_codigo.strip()
        cart = meus_contratos(u, situacao="vigentes")
        al = listar_alertas(u)
        titulo = f"Carteira de contratos — {cart['om']} ({u})"
        blocos = [("Resumo por faixa de vencimento",
                   [(r["janela_vencimento"], f"{r['quantidade']} contrato(s) · {brl(r['valor_total'])}") for r in cart["resumo_por_faixa"]] or [("—", "Sem contratos vigentes")]),
                  ("Alertas", [(a["tipo_alerta"], str(a["quantidade"])) for a in al["contagem_por_tipo"]] or [("—", "Sem alertas")])]
        listas = [("Contratos que vencem em até 180 dias",
                   [f"{c['numero_contrato']} · {c['fornecedor_nome']} · vence em {c['vigencia_fim_fmt']} "
                    f"({c['dias_para_vencer']} dias) · {c['valor_global_fmt']} · PNCP {c['numero_controle_pncp']}"
                    for c in cart["contratos"] if c["dias_para_vencer"] is not None and c["dias_para_vencer"] <= 180]
                   or ["Nenhum contrato vence em até 180 dias."])]
        nome_base = f"relatorio_carteira_{u}"
    else:
        return {"erro": "Informe numero_controle_pncp (um contrato) ou unidade_codigo (carteira da OM)."}

    analise = texto_analise.strip() or "(sem análise da IA)"
    if formato == "email":
        linhas = [f"Prezado(a) {destinatario},", "", f"Segue o resumo: {titulo}.", ""]
        for nome, itens in blocos:
            linhas.append(nome.upper())
            linhas += [f"- {k}: {v}" for k, v in itens]
            linhas.append("")
        for nome, itens in listas:
            linhas.append(nome.upper())
            linhas += [f"- {i}" for i in itens]
            linhas.append("")
        linhas += ["ANÁLISE E ORIENTAÇÕES", analise, "", SELO, FONTE, LIMITACAO, "", "Respeitosamente,"]
        return {"assunto": titulo, "corpo": "\n".join(linhas),
                "aviso": "O sistema não envia e-mail: copie o texto e envie pelo seu correio."}

    from docx import Document
    from docx.shared import Pt
    doc = Document()
    doc.styles["Normal"].font.size = Pt(11)
    doc.add_heading(titulo, level=1)
    doc.add_paragraph(f"Destinatário: {destinatario}. Gerado em {datetime.now():%d/%m/%Y %H:%M}. "
                      f"Data de referência dos dados: {dbr(_data_ref())}.")
    for nome, itens in blocos:
        doc.add_heading(nome, level=2)
        t = doc.add_table(rows=0, cols=2)
        t.style = "Light Grid Accent 1"
        for k, v in itens:
            row = t.add_row().cells
            row[0].text, row[1].text = str(k), str(v)
    for nome, itens in listas:
        doc.add_heading(nome, level=2)
        for i in itens:
            doc.add_paragraph(str(i), style="List Bullet")
    doc.add_heading("Análise e orientações", level=2)
    for par in analise.split("\n"):
        if par.strip():
            doc.add_paragraph(par.strip())
    doc.add_heading("Fonte e limitações", level=2)
    for t in (SELO, FONTE, LIMITACAO):
        doc.add_paragraph(t)
    nome = f"{nome_base}_{datetime.now():%H%M%S}.docx"
    doc.save(PASTA_SAIDA / nome)
    return {"arquivo": nome, "link_download": f"{URL_ARQUIVOS}/arquivos/{nome}", "titulo": titulo,
            "aviso": SELO}


@mcp.tool()
def exportar_agenda(unidade_codigo: str = "", numero_controle_pncp: str = "") -> dict:
    """Cria um arquivo de agenda (.ics) com o fim da vigência dos contratos vigentes da OM (ou de um
    contrato), com lembretes 180 e 90 dias antes. O usuário importa no Outlook ou Google Agenda.
    Devolve o link de download e quantos eventos foram criados."""
    if numero_controle_pncp.strip():
        filtro, params, nome_base = "numero_controle_pncp = %(i)s", {"i": numero_controle_pncp.strip()}, "contrato"
    elif unidade_codigo.strip():
        filtro, params, nome_base = ("unidade_codigo = %(u)s AND familia_instrumento = 'Contrato'",
                                     {"u": unidade_codigo.strip()}, f"om_{unidade_codigo.strip()}")
    else:
        return {"erro": "Informe unidade_codigo ou numero_controle_pncp."}
    linhas = _sql(f"""SELECT numero_controle_pncp, numero_contrato, unidade_nome, left(objeto, 200) AS objeto,
                             fornecedor_nome, vigencia_fim
                      FROM painel.vw_contratos
                      WHERE {filtro} AND situacao = 'vigente' AND vigencia_fim IS NOT NULL
                      ORDER BY vigencia_fim LIMIT 300""", params)
    if not linhas:
        return {"erro": "Nenhum contrato vigente com data de fim para exportar."}

    def esc(t: str) -> str:
        return (t or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", " ")

    agora = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    ev = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Hackathon C-ApA-IM//Mentor do Fiscal//PT", "CALSCALE:GREGORIAN"]
    for c in linhas:
        d = c["vigencia_fim"].replace("-", "")
        uid = re.sub(r"[^0-9A-Za-z]", "", c["numero_controle_pncp"]) + "@mentor-fiscal"
        ev += ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{agora}", f"DTSTART;VALUE=DATE:{d}",
               f"SUMMARY:{esc('Fim da vigência — contrato ' + (c['numero_contrato'] or '') + ' — ' + (c['fornecedor_nome'] or ''))}",
               f"DESCRIPTION:{esc((c['objeto'] or '') + ' | PNCP ' + c['numero_controle_pncp'] + ' | ' + (c['unidade_nome'] or '') + ' | Decidir com o gestor entre prorrogação e nova contratação.')}"]
        for gatilho, texto in (("-P180D", "180 dias: iniciar o planejamento"), ("-P90D", "90 dias: decidir prorrogação ou nova contratação")):
            ev += ["BEGIN:VALARM", "ACTION:DISPLAY", f"TRIGGER:{gatilho}", f"DESCRIPTION:{esc(texto)}", "END:VALARM"]
        ev.append("END:VEVENT")
    ev.append("END:VCALENDAR")
    nome = f"agenda_{nome_base}_{datetime.now():%H%M%S}.ics"
    (PASTA_SAIDA / nome).write_text("\r\n".join(ev) + "\r\n", encoding="utf-8")
    return {"arquivo": nome, "link_download": f"{URL_ARQUIVOS}/arquivos/{nome}", "eventos": len(linhas),
            "primeiro_vencimento": dbr(linhas[0]["vigencia_fim"]), "fonte": FONTE}


@mcp.tool()
def consultar_sql(sql: str) -> dict:
    """Consulta livre e SÓ DE LEITURA nas views do schema painel, para perguntas que as outras
    ferramentas não cobrem. Escreva um único SELECT (ou WITH ... SELECT) em PostgreSQL.
    Views: painel.vw_contratos (1 linha por contrato/empenho: unidade_codigo, unidade_nome, objeto,
    fornecedor_nome, familia_instrumento ['Contrato','Empenho','Outros instrumentos'], categoria_processo,
    valor_global, vigencia_inicio, vigencia_fim, dias_para_vencer, situacao ['vigente','encerrado',
    'a iniciar','sem data de fim'], janela_vencimento, numero_controle_pncp),
    painel.vw_concentracao (OM x fornecedor: participacao_pct, hhi_unidade, alerta_concentracao),
    painel.vw_concentracao_marinha (fornecedor: participacao_marinha_pct, unidades_atendidas),
    painel.vw_alertas. Filtre familia_instrumento = 'Contrato' quando falar de contratos.
    Devolve até 200 linhas e o SQL executado."""
    s = sql.strip().rstrip(";").strip()
    if not re.match(r"^(select|with)\b", s, flags=re.I) or ";" in s:
        return {"erro": "Só é permitido um único SELECT (ou WITH ... SELECT)."}
    try:
        linhas = _sql(f"SELECT * FROM ({s}) AS consulta LIMIT 200")
    except Exception as e:  # erro de SQL volta para o agente corrigir
        return {"erro": f"O banco recusou a consulta: {str(e).splitlines()[0]}", "sql": s}
    return {"sql_executado": s, "linhas": linhas, "quantidade": len(linhas), "fonte": FONTE, "limitacao": LIMITACAO}


# --------------------------------------------------------------------------- rotas HTTP
@mcp.custom_route("/arquivos/{nome}", methods=["GET"])
async def baixar_arquivo(request: Request):
    nome = Path(request.path_params["nome"]).name
    caminho = PASTA_SAIDA / nome
    if not caminho.is_file():
        return PlainTextResponse("Arquivo não encontrado.", status_code=404)
    return FileResponse(caminho, filename=nome)


@mcp.custom_route("/saude", methods=["GET"])
async def saude(request: Request):
    try:
        n = _sql("SELECT count(*) AS n FROM painel.vw_contratos")[0]["n"]
        return JSONResponse({"ok": True, "contratos_no_banco": n})
    except Exception as e:
        return JSONResponse({"ok": False, "erro": str(e)}, status_code=503)


# --------------------------------------------------------------------------- API da tela web
# O painel da tela do fiscal usa as MESMAS funções das ferramentas, sem passar pela IA:
# abre na hora e continua funcionando mesmo se o Gemini estiver fora do ar.
def _param(request: Request, nome: str, padrao: str = "") -> str:
    return (request.query_params.get(nome) or padrao).strip()[:200]


async def _responder(func, *args, **kwargs):
    try:
        return JSONResponse(await run_in_threadpool(func, *args, **kwargs))
    except Exception as e:
        return JSONResponse({"erro": f"Falha ao consultar a base: {str(e).splitlines()[0]}"}, status_code=500)


@mcp.custom_route("/api/om", methods=["GET"])
async def api_om(request: Request):
    return await _responder(buscar_om, _param(request, "q"))


@mcp.custom_route("/api/carteira", methods=["GET"])
async def api_carteira(request: Request):
    situacao = _param(request, "situacao", "vigentes")
    if situacao not in {"vigentes", "encerrados_recentes", "todos"}:
        situacao = "vigentes"
    return await _responder(meus_contratos, _param(request, "uasg"), busca=_param(request, "busca"),
                            situacao=situacao)


@mcp.custom_route("/api/alertas", methods=["GET"])
async def api_alertas(request: Request):
    return await _responder(listar_alertas, _param(request, "uasg"), tipo=_param(request, "tipo"))


@mcp.custom_route("/api/ficha", methods=["GET"])
async def api_ficha(request: Request):
    return await _responder(_ficha, _param(request, "id"), _param(request, "uasg"))


@mcp.custom_route("/api/guia", methods=["GET"])
async def api_guia(request: Request):
    return await _responder(guia_do_fiscal, _param(request, "tema"))


@mcp.custom_route("/api/agenda", methods=["GET"])
async def api_agenda(request: Request):
    return await _responder(exportar_agenda, unidade_codigo=_param(request, "uasg"),
                            numero_controle_pncp=_param(request, "id"))


if __name__ == "__main__":
    mcp.run("streamable-http", host="0.0.0.0", port=int(os.getenv("PORTA", "8080")),
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))
