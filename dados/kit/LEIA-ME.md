# Pasta do kit

Descompacte aqui o `kit_hackathon_PNCP.zip` (pasta "Disponibilizados aos Grupos" do Drive da organização).
Todo `.csv` desta pasta (inclusive em subpastas) vira uma tabela `kit.<nome>` no banco na primeira subida.

Os arquivos do kit não vão para o git (ver `.gitignore`).

Se o kit for colocado ou trocado depois da primeira subida:

```
docker compose down -v
docker compose up -d
```
