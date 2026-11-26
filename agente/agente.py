"""
Agente de contratos — chat que consulta o banco do kit e cria gráficos no Metabase
=================================================================================
ESQUELETO (passo 3): ainda não testado contra o Metabase nem contra o modelo de IA.

Como funciona (ver README, seção "Como o agente funciona"):
    o modelo lê o cardápio de ferramentas (nome + docstring + parâmetros),
    pede uma ferramenta, o Python executa, o resultado volta ao modelo,
    e o laço segue até a resposta final. Quem calcula é o banco; quem mostra
    é o Metabase; o modelo só escolhe a ferramenta e escreve o SQL.

Uso (com banco e Metabase de pé):
    pip install -r agente/requirements.txt
    python agente/agente.py
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg
import requests
from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain.tools import tool

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

DB = dict(host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORTA", "5432")),
          dbname="hackathon", user="agente_ro", password=os.getenv("AGENTE_RO_PWD", ""))
MB_URL = os.getenv("MB_URL", "http://localhost:3000").rstrip("/")
LIMITE_LINHAS = 50


# ---------------------------------------------------------------------------
# Acesso ao banco: usuário SÓ LEITURA, só schema painel (Art. 5º: sem CPF)
# ---------------------------------------------------------------------------
def _so_leitura(sql: str) -> str:
    s = sql.strip().rstrip(";").strip()
    if ";" in s:
        raise ValueError("Envie uma única consulta, sem ';' no meio.")
    if not re.match(r"(?is)^\s*(select|with)\b", s):
        raise ValueError("Só é permitido SELECT (ou WITH ... SELECT).")
    return s


def _executar(sql: str, limite: int = LIMITE_LINHAS) -> tuple[list[str], list[tuple]]:
    s = _so_leitura(sql)
    with psycopg.connect(**DB) as con, con.cursor() as cur:
        cur.execute(f"SELECT * FROM ({s}) AS consulta LIMIT {int(limite)}")
        return [c.name for c in cur.description], cur.fetchall()


def descrever_painel() -> str:
    """Dicionário das views do schema painel (colunas + comentários) para o system prompt."""
    sql = """
        SELECT c.table_name, c.column_name, c.data_type,
               COALESCE(col_description((c.table_schema||'.'||c.table_name)::regclass,
                                        c.ordinal_position), '')
        FROM information_schema.columns c
        WHERE c.table_schema = 'painel'
        ORDER BY c.table_name, c.ordinal_position"""
    with psycopg.connect(**DB) as con, con.cursor() as cur:
        cur.execute(sql)
        linhas = cur.fetchall()
    if not linhas:
        return "(nenhuma view no schema painel ainda)"
    saida, atual = [], None
    for tabela, coluna, tipo, comentario in linhas:
        if tabela != atual:
            saida.append(f"\npainel.{tabela}:")
            atual = tabela
        saida.append(f"  - {coluna} ({tipo}){': ' + comentario if comentario else ''}")
    return "\n".join(saida)


# ---------------------------------------------------------------------------
# Metabase: sessão + criação de pergunta SQL (mesma API do SIDEC-MB)
# ---------------------------------------------------------------------------
_sessao: dict = {}


def _mb(metodo: str, caminho: str, **kw):
    if "token" not in _sessao:
        r = requests.post(f"{MB_URL}/api/session", timeout=30,
                          json={"username": os.getenv("MB_USUARIO"), "password": os.getenv("MB_SENHA")})
        r.raise_for_status()
        _sessao["token"] = r.json()["id"]
    r = requests.request(metodo, f"{MB_URL}/api{caminho}", timeout=60,
                         headers={"X-Metabase-Session": _sessao["token"]}, **kw)
    r.raise_for_status()
    return r.json() if r.text else None


def _id_banco() -> int:
    if "banco" not in _sessao:
        bancos = _mb("GET", "/database")
        itens = bancos.get("data", bancos) if isinstance(bancos, dict) else bancos
        nome = os.getenv("MB_BANCO", "hackathon")
        _sessao["banco"] = next(b["id"] for b in itens
                                if b.get("details", {}).get("dbname") == nome or b.get("name") == nome)
    return _sessao["banco"]


# ---------------------------------------------------------------------------
# FERRAMENTAS — o que o chat sabe fazer. A docstring é o que o modelo lê.
# ---------------------------------------------------------------------------
@tool
def consultar(sql: str) -> str:
    """Executa UMA consulta SELECT nas views do schema painel e devolve até 50 linhas.
    Use para responder qualquer pergunta sobre contratos, vencimentos ou fornecedores.
    Todo número da resposta ao usuário deve vir desta ferramenta."""
    try:
        colunas, linhas = _executar(sql)
    except Exception as erro:          # o erro volta ao modelo, que corrige o SQL
        return f"ERRO: {erro}"
    if not linhas:
        return "Consulta sem resultados."
    corpo = "\n".join(" | ".join("" if v is None else str(v) for v in l) for l in linhas)
    return f"{' | '.join(colunas)}\n{corpo}\n({len(linhas)} linhas, limite {LIMITE_LINHAS})"


@tool
def criar_grafico(titulo: str, sql: str, tipo: str = "table") -> str:
    """Cria uma pergunta (gráfico) no Metabase com o SQL informado e devolve o link.
    tipo: table, bar, line, pie, row ou scalar. Teste o SQL com consultar() antes."""
    try:
        _so_leitura(sql)
        card = _mb("POST", "/card", json={
            "name": titulo, "display": tipo, "visualization_settings": {},
            "dataset_query": {"type": "native", "native": {"query": sql}, "database": _id_banco()},
            "description": "Criado pelo agente de contratos (SQL visível na pergunta).",
        })
        return f"Gráfico criado: {MB_URL}/question/{card['id']}"
    except Exception as erro:
        return f"ERRO ao criar no Metabase: {erro}"


@tool
def resumir_contrato(numero_controle_pncp: str) -> str:
    """Resumo de um contrato para o fiscal e para o gestor, com os números conferidos."""
    # PASSO 4: reaproveitar fatos_do_contrato() + conferir() de notebooks/sidec_contratos_colab.py
    return "Ferramenta ainda não implementada (passo 4)."


INSTRUCOES = """Você apoia gestores e fiscais de contratos da Marinha do Brasil (Lei 14.133/2021).
Regras:
- Responda em português do Brasil, de forma objetiva.
- Todo número que você citar vem de consultar(); nunca invente valores, datas ou fornecedores.
- Use somente as views do schema painel descritas abaixo.
- Quando o usuário pedir gráfico, painel ou visualização, use criar_grafico() e devolva o link.
- Não conclua sobre irregularidade, fraude ou culpa: aponte o que merece verificação humana.
- Se a pergunta não puder ser respondida com estes dados, diga isso.

Views disponíveis:
{dicionario}
"""


def montar_agente():
    return create_agent(
        model=os.getenv("LLM_MODELO", "anthropic:claude-sonnet-5"),
        tools=[consultar, criar_grafico, resumir_contrato],
        system_prompt=INSTRUCOES.format(dicionario=descrever_painel()),
    )


def main() -> None:
    agente = montar_agente()
    historico: list = []            # memória da conversa: reenviada a cada pergunta
    print("Agente de contratos. Digite 'sair' para encerrar.")
    while True:
        pergunta = input("\nVocê: ").strip()
        if pergunta.lower() in {"sair", "exit", "quit"}:
            break
        historico.append({"role": "user", "content": pergunta})
        resultado = agente.invoke({"messages": historico})
        historico = resultado["messages"]
        print("\nAgente:", historico[-1].content)


if __name__ == "__main__":
    main()
