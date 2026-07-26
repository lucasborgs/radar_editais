# RT03-T01 — Contrato de domínio e registry de cobertura

## Objetivo

Criar o registry autoritativo dos nove canais intencionais e um loader puro que
o leia de `docs/domain/sources/_coverage.md`. O registry declara cobertura;
não aciona, cadastra ou descobre fontes.

## Arquivos prováveis

- `docs/domain/sources/_coverage.md` (novo, bloco YAML autoritativo);
- `src/radar/core/kg/schema.py` (helper de carga/validação, sem lista espelho);
- `tests/unit/test_source_coverage_registry.py` (novo).

## Passos

1. Definir um bloco YAML único (`source_coverage`) com `source_key`,
   `display_name`, `mode`, `scope_note`, `enabled_by_default` e, somente onde
   aplicável, `expected_interval_hours`, `feature_flag` e identificador lógico
   de artefato. Registrar: `finep`, `fapesp`, `fapesc`, `web`; `tavily`, `dou`;
   `investidores`, `programas`, `ict_embrapii`.
2. Declarar os quatro scrapers com 24 horas. Declarar DOU com o nome da flag
   `DISCOVERY_DOU_ENABLED`, sem ler/gravar seu valor no documento ou banco.
   Não atribuir intervalo/SLA fictício à descoberta aberta nem a catálogos.
3. Acrescentar ao loader existente um acesso específico, cacheável e puro para
   o bloco, que valide chaves estáveis lowercase, modos permitidos, unicidade,
   campos exigidos e a regra de intervalo. Mensagem de erro deve identificar só
   a regra/chave documental, nunca conteúdo sensível.
4. Testar o documento real e fixtures mínimas inválidas: chave duplicada ou em
   maiúscula, modo inválido, intervalo indevido/ausente e flag vazia. Confirmar
   que o loader não mantém um segundo catálogo em Python.

## Invariantes

- A documentação é a fonte de verdade; `SCRAPER_REGISTRY` continua sendo
  identidade operacional dos scrapers, não a regra de cobertura.
- Sem query completa, URL parametrizada, credencial, segredo ou valor de flag.
- Nenhum canal novo é adicionado além dos produtores existentes definidos na
  spec, e o registry não promete completude institucional.

## Testes direcionados

- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_registry.py`;
- `ruff check src/radar/core/kg/schema.py tests/unit/test_source_coverage_registry.py`;
- `git diff --check`.

## Pare

Pare se o runtime não puder identificar inequivocamente um dos nove canais, se
uma chave conflitar com o produtor existente, ou se for necessário copiar
queries/URLs/segredos para tornar o registry útil. A decisão volta à governança,
não é inferida no código.

## Entrega e ambiente hermético

Entregar o registry, loader e teste, mais o relatório `RT03-T01-*.md` com a
tabela de canais e invariantes validadas. Confirmar no relatório: `ENVIRONMENT=test`,
sem `.env`, sem banco, rede, LLM, worker ou produção; a única leitura externa ao
teste é o Markdown versionado no repositório.
