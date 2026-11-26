# Hackathon C-ApA-IM · Desafio 4 — Gestão e acompanhamento de contratos com IA

1º Hackathon "Intendência com IA!" (CIANB, 26–27/11/2026).

**Problema:** o gestor descobre o vencimento do contrato tarde demais e não enxerga quando a OM depende demais de um fornecedor.

**Solução:** um chat em que o gestor ou o fiscal pergunta em português. Um agente de IA consulta a base de contratos do PNCP fornecida pela organização, cria os gráficos no Metabase e resume os contratos para o fiscal. Banco, Metabase e agente rodam em Docker.

## Arquitetura

```
Usuário ──► Chat ──► Agente (LangChain + modelo de IA)
                        │  escolhe a ferramenta e escreve o SQL
                        ├─ consultar()         ──► PostgreSQL (views do schema painel, só leitura)
                        ├─ criar_grafico()     ──► API do Metabase (pergunta SQL nativa)
                        └─ resumir_contrato()  ──► fatos do banco + resumo da IA + conferência pelo Python
```

- **Quem calcula é o banco, quem mostra é o Metabase.** O modelo escolhe a ferramenta e escreve o SQL, e o SQL de cada gráfico fica visível no Metabase.
- O agente entra no banco com o papel `agente_ro`: só leitura, só o schema `painel`, com limite de 15 s por consulta. As views do `painel` escondem o CPF de pessoa física, então nenhum dado pessoal vai para a IA externa (Art. 5º do regulamento).
- Um painel fixo de alertas de vencimento e concentração não depende do agente acertar ao vivo.

## Como rodar

Pré-requisito: Docker com Compose.

1. Descompacte o kit do PNCP em `dados/kit/` (ver `dados/kit/LEIA-ME.md`).
2. `cp .env.example .env` e troque as senhas.
3. `docker compose up -d`. Na primeira vez, a carga do kit leva alguns minutos; acompanhe com `docker compose logs -f db`.
4. Abra http://localhost:3000, crie o usuário administrador e conecte o banco:
   - tipo: PostgreSQL · host: `db` · porta: `5432` · banco: `hackathon` · usuário: `metabase_leitor` · senha: `LEITOR_PWD` do `.env`.
5. Para conferir a carga: `docker compose exec db psql -U postgres -d hackathon -c "SELECT * FROM controle.tabelas_carregadas"`.

Agente (com o banco e o Metabase de pé): `pip install -r agente/requirements.txt` e depois `python agente/agente.py`. Antes, preencha no `.env` o `LLM_MODELO`, a chave da IA e `MB_USUARIO`/`MB_SENHA`.

## Estrutura

| Caminho | O que é |
|---|---|
| `docker-compose.yml` | PostgreSQL 18 + Metabase (com base no SIDEC-MB) |
| `db/init/01_carga_kit.sh` | Carga automática de todo `.csv` do kit em `kit.*` (tudo texto, colunas em snake_case), papéis e permissões |
| `db/init/02_views_painel.sql` | Views tipadas `painel.vw_contratos` e `painel.vw_concentracao` (**pendente**) |
| `agente/agente.py` | Agente LangChain com as ferramentas `consultar`, `criar_grafico` e `resumir_contrato` (**esqueleto**) |
| `notebooks/sidec_contratos_colab.py` | Lógica de vencimento, concentração, resumo pela IA e conferência em pandas (testada com dados simulados) |

## Status

- [x] Docker: banco + Metabase; carga do kit testada com CSV simulado (BOM, `;`, LATIN1, subpastas)
- [ ] Views `painel` sobre as colunas reais de contratos do kit
- [ ] Painel fixo de alertas (vencimento ≤90 / 91–180 dias, concentração por CNPJ raiz) montado por script
- [ ] Agente: `consultar` + `criar_grafico` funcionando de ponta a ponta
- [ ] `resumir_contrato` com conferência dos números
- [ ] Tela de chat
- [ ] Bateria de validação (perguntas com SQL-gabarito: "acertou X de Y") e vídeo de contingência

## Origem e limitações

A lógica de vencimento, de concentração e as regras de dados vêm do **SIDEC-MB** (trabalho de MPI), adaptadas à base do hackathon.

Limitações declaradas:
- A base é um recorte público do PNCP, sem sanções (CEIS/CNEP) nem avaliações de fiscalização.
- A sucessão de contratos não é deduzida dos dados.
- A diferença entre o valor global e o inicial inclui prorrogações.
- A IA pode errar o SQL. Por isso o SQL fica visível e há uma bateria de validação.
- O sistema não conclui sobre irregularidade: é apoio à decisão, sujeito a revisão humana (Art. 2º, parágrafo único).
