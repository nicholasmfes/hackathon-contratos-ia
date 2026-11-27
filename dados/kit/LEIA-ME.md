# Pasta do kit

Já vêm aqui os dois arquivos do kit que o Desafio 4 usa, para o sistema subir sem baixar nada:

- `contratos.csv` — 16.946 contratos e empenhos da Marinha (PNCP, base congelada em 31/10/2026). O nome das 204 linhas de pessoa física foi apagado antes de versionar (Art. 5º do regulamento); o CPF já vinha mascarado.
- `unidades.csv` — 1.494 unidades compradoras (UASG).

Todo `.csv` desta pasta (inclusive em subpastas) vira uma tabela `kit.<nome>` no banco na primeira subida.
Se quiser o kit completo, descompacte o `kit_hackathon_PNCP.zip` aqui também.

Se o kit for colocado ou trocado depois da primeira subida:

```
docker compose down -v
docker compose up -d
```
