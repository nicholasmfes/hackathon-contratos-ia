# Hackathon C-ApA-IM · Desafio 4 — Mentor do Fiscal

1º Hackathon "Intendência com IA!" (CIANB, 26–27/11/2026).

**Problema.** Um oficial acaba de ser designado fiscal de contrato e nunca fiscalizou. Ele não sabe quais contratos são seus, o que o fiscal anterior deixou, o que vence e o que precisa cobrar.

**Solução.** Um agente de IA (Google ADK + Gemini) que conversa com o fiscal em português, consulta a base de contratos do PNCP fornecida pela organização e o ensina, passo a passo: quais são os contratos, o que diz cada um, o que vence, o que pedir na passagem de fiscalização, e gera o relatório, o e-mail ao gestor e a agenda.

## Arquitetura

```
Fiscal ─► Interface web do Google ADK (http://localhost:8000)
            └─ Agente "mentor_do_fiscal" (Gemini) ── escolhe a ferramenta e escreve os textos
                  └─ MCP (http://mcp:8080/mcp) ── servidor de ferramentas do sistema
                        ├─ buscar_om · meus_contratos · ficha_contrato · listar_alertas
                        ├─ guia_do_fiscal (passo a passo conferido, Lei 14.133/2021)
                        ├─ gerar_relatorio (Word ou texto de e-mail) · exportar_agenda (.ics)
                        └─ consultar_sql (só leitura)
                              └─ PostgreSQL, schema painel (views sem dado pessoal, usuário agente_ro)
Metabase (http://localhost:3000) continua disponível para painéis.
```

- **Quem calcula é o banco; a IA explica e escreve.** Nenhum número sai do modelo.
- **MCP**: as ferramentas do sistema viram um servidor MCP padrão; qualquer agente compatível pode usá-las.
- **Art. 5º**: o agente só enxerga o schema `painel` (sem CPF e sem nome de pessoa física), com usuário só de leitura.

## Como rodar (Windows, macOS ou Linux)

Pré-requisito: Docker Desktop aberto.

```
git clone https://github.com/nicholasmfes/hackathon-contratos-ia.git
cd hackathon-contratos-ia
copy .env.example .env        (no macOS/Linux: cp .env.example .env)
```

Abra o `.env` e preencha `GOOGLE_API_KEY` (chave do Gemini, gratuita em https://aistudio.google.com/apikey). Depois:

```
docker compose up -d --build
```

Na primeira vez a carga do kit leva de 1 a 3 minutos. Acompanhe com `docker compose ps` até `hk-db` ficar `healthy`.

- Agente: **http://localhost:8000** → escolha `mentor_fiscal` no canto superior esquerdo.
- Teste das ferramentas sem IA: http://localhost:8080/saude deve mostrar `"contratos_no_banco": 16946`.
- Metabase (opcional): http://localhost:3000.

Se o Gemini gratuito bloquear por limite de uso: troque a chave ou o `MODELO_GEMINI` no `.env` e rode `docker compose up -d agente`.

## Roteiros de demonstração (fiscal recém-designado)

Escreva as frases no chat, na ordem. A OM de exemplo é o Centro de Intendência da Marinha em Natal (UASG 783810): 20 contratos vigentes, 3 vencem em até 90 dias.

**Roteiro 1 — "Cheguei: quais são meus contratos?"** (conhecer a carteira)
1. `Olá, fui designado fiscal de contrato e nunca fiscalizei. Por onde começo?`
2. `Sou do Centro de Intendência da Marinha em Natal. Quais contratos a OM tem vigentes?`
3. `Quais vencem primeiro e o que eu preciso fazer com cada um?`
4. `Exporte esses vencimentos para a minha agenda.` → baixa o .ics

**Roteiro 2 — "Herdei um contrato: o que o fiscal anterior deixou?"** (entender um contrato)
1. `Vou fiscalizar o contrato 00028 do Centro de Intendência de Natal. Me explique esse contrato em linguagem simples.`
2. `O que o fiscal anterior fez nesse contrato?` → o agente diz que o kit não traz isso e lista o que pedir na passagem
3. `Ele vence em 7 dias. O que eu faço agora?` → passo a passo de vencimento (guia + ficha)
4. `Gere um relatório em Word desse contrato para eu levar ao gestor.` → baixa o .docx

**Roteiro 3 — "Preciso cobrar e avisar o gestor"** (agir)
1. `Quais alertas a minha OM tem hoje?`
2. `A empresa atrasou uma entrega. Como eu registro e cobro?` → guia de ocorrências
3. `Escreva um e-mail ao gestor avisando dos contratos que vencem em até 180 dias e do que precisa ser decidido.` → texto pronto para copiar
4. `Na Marinha, quantos contratos de serviços estão vigentes?` → consulta livre (SQL visível no rastro)

Na interface do ADK, cada chamada de ferramenta aparece no chat e no painel de eventos: é a prova de que os números vêm do banco.

## Limitações

O kit não traz aditivos, prorrogações, execução, pagamentos nem o nome dos fiscais; um contrato prorrogado pode aparecer como encerrado. Os prazos contam a partir de 31/10/2026 (data de corte do kit). Alertas são sinais para verificar, nunca conclusão de irregularidade. Resumos, relatórios e orientações são subsídio e precisam de revisão humana (Art. 2º do regulamento). No plano gratuito do Gemini, o Google pode usar o conteúdo enviado para melhorar seus produtos: por isso só dado público do kit, sem pessoa física, sai do sistema.

---

## Versão anterior (LangChain + Metabase)

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

1. Descompacte o `kit_hackathon_PNCP.zip` dentro de `dados/kit/`. Pode ficar a pasta `dados/kit/kit_hackathon/...`, porque a carga procura os CSV em todas as subpastas.
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
| `db/init/02_views_painel.sh` + `db/init/sql/painel.sql` | Views do schema `painel` (ver abaixo) e a marca de carga concluída |
| `agente/agente.py` | Agente LangChain com as ferramentas `consultar`, `criar_grafico` e `resumir_contrato` (**esqueleto**) |
| `notebooks/sidec_contratos_colab.py` | Lógica de vencimento, concentração, resumo pela IA e conferência em pandas (testada com dados simulados) |

## O que o Metabase e o agente enxergam (schema `painel`)

| View | Grão | Para quê |
|---|---|---|
| `painel.vw_contratos` | 1 contrato ou empenho | Dados tipados: dias para vencer, situação, faixa de vencimento, verificar reposição, contrato × empenho, qualidade do dado |
| `painel.vw_concentracao` | OM × fornecedor | Participação do fornecedor (CNPJ raiz) no valor vigente da OM, HHI da OM e alerta de concentração |
| `painel.vw_concentracao_marinha` | fornecedor | Valor vigente na Marinha inteira, OM atendidas e participação no total |
| `painel.vw_alertas` | 1 alerta | Lista única: vence em até 90 dias, vence em 91 a 180, encerrado há até 90 dias, fornecedor concentrado, qualidade do dado |
| `painel.parametro` | 1 linha | Data de referência e limiares dos alertas |

**Data de referência:** o kit está congelado em **31/10/2026**, e os prazos contam a partir dessa data (`painel.parametro.data_referencia`). Contar a partir de hoje marcaria como vencido o que pode ter sido prorrogado depois do corte, porque o kit não traz aditivos. Em produção, a referência passa a ser `CURRENT_DATE`.

**Conferência com o kit real:** a regra de vigência das views bate com a marca `vigente_na_data_corte` da organização em **16.917 de 16.917 instrumentos (100%)**. Os totais também coincidem com o LEIAME: 3.335 instrumentos vigentes, dos quais 2.873 contratos (termo inicial).

Alertas gerados sobre o kit (referência 31/10/2026):

| Alerta | Quantidade |
|---|---|
| Vence em até 90 dias | 670 |
| Vence em 91 a 180 dias | 412 |
| Encerrado há até 90 dias (verificar reposição) | 597 |
| Fornecedor concentrado na OM | 119 |
| Qualidade do dado | 56 |

Os números incluem contratos, empenhos e outros instrumentos; filtre por `familia_instrumento`.

Para mudar um parâmetro, por exemplo o limiar de concentração:
`docker compose exec db psql -U postgres -d hackathon -c "UPDATE painel.parametro SET limiar_participacao_pct = 50"`

## Status

- [x] Docker: banco + Metabase; carga do kit testada com o kit real (9 tabelas, contagens iguais às do LEIAME, cerca de 4 s)
- [x] Views `painel` sobre o kit real, com conferência de 100% na regra de vigência; o agente lê o kit só pelas views
- [ ] Painel fixo de alertas (vencimento ≤90 / 91–180 dias, concentração por CNPJ raiz) montado por script
- [ ] Agente: `consultar` + `criar_grafico` funcionando de ponta a ponta
- [ ] `resumir_contrato` com conferência dos números
- [ ] Tela de chat
- [ ] Bateria de validação (perguntas com SQL-gabarito: "acertou X de Y") e vídeo de contingência

## Origem e limitações

A lógica de vencimento, de concentração e as regras de dados vêm do **SIDEC-MB** (trabalho de MPI), adaptadas à base do hackathon.

Limitações declaradas:
- A base é um recorte público do PNCP congelado em 31/10/2026, só da Marinha no caso dos contratos, sem sanções (CEIS/CNEP) nem avaliações de fiscalização.
- O kit não traz aditivos, prorrogações nem execução: um contrato prorrogado pode aparecer como encerrado. Por isso o alerta diz "verificar reposição", e não "vencido sem reposição".
- A sucessão de contratos não é deduzida dos dados.
- A concentração soma objetos diferentes da mesma OM, então participação alta é dependência a verificar.
- Valores acima de R$ 1 bilhão saem da concentração e viram alerta de qualidade do dado.
- A IA pode errar o SQL. Por isso o SQL fica visível e há uma bateria de validação.
- O sistema não conclui sobre irregularidade: é apoio à decisão, sujeito a revisão humana (Art. 2º, parágrafo único).
