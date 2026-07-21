# 05 — Skills da app: code-routed → model-routed (Finding F / candidato #2)

**Fase:** 2 (redator) · **Validação:** eval escrita + shadow · **Esforço:** médio

## Contexto (correção do Finding F)

`core/skills.py` **não é** colisão de nome com as skills do Claude Code — é o
**mesmo conceito** (instrução em markdown, externa, carregada contextualmente),
reinventado no produto. A diferença é o *binding*:

| | Claude Code skill | `skills/<fonte>_*.md` (hoje) |
|---|---|---|
| Quem decide carregar | o **modelo** (match por descrição) | o **código** (`load_skill(source)` por chave) |
| Disclosure | metadado visível → corpo sob demanda | injeta o `.md` **inteiro** sempre |
| Natureza | procedimento (age) | conhecimento (restringe saída) |

→ **Não renomear.** A oportunidade é evoluir parte do uso para model-routed.

## Estado atual

- `load_skill(source, skill_type)` (`core/skills.py:32-53`): lookup por chave
  `<source>_<type>.md`; fallback gracioso "".
- `available_skills()` (`core/skills.py:56-73`): **já lista** source/type/file —
  metade de um catálogo, mas **não é exposto ao modelo**.
- Consumido por `ComplianceMonitor` e `WritingSession` por injeção passiva baseada
  no `source` da wiki page. O `_compliance.md` inteiro entra de uma vez.
- Tipo `<source>_writing.md` previsto mas subutilizado.

## Mudança proposta

1. **Tool `load_skill` no Redator:** expor como `@tool` que recebe
   `(skill_type)` (a fonte já vem do contexto da sessão) e retorna o pack — ou,
   melhor, uma **seção** do pack sob demanda.
2. **Catálogo para o modelo:** injetar no system prompt do Redator um resumo de
   `available_skills()` (nome + 1 linha de descrição por pack) para o modelo saber
   o que pode puxar.
3. **Híbrido (recomendado):** manter a injeção automática do compliance como
   default/baseline (não regredir), e **adicionar** a tool para pull granular —
   ex.: puxar regras de orçamento só na seção de orçamento. Migração incremental,
   não big-bang.
4. **Split opcional dos `.md`** em seções nomeadas para permitir pull por seção.

## Por que ganha custo

Hoje o `_compliance.md` inteiro é injetado sempre. Pull por seção carrega **só o
necessário** → menos contexto. O custo extra (decisão de roteamento) é menor que o
contexto economizado em sessões longas.

## Validação

- **Eval gate:** `python -m radar.core.eval writing` — qualidade de compliance não pode
  cair (o risco é o modelo *não* puxar a regra que precisava).
- **Shadow:** medir tamanho de contexto por turno (com injeção total vs pull) e
  taxa de "puxou a skill certa".

## Risco

Baixo-médio: model-routed pode **esquecer** de puxar a regra → daí o modo híbrido
(baseline automático preservado).

## Pergunta em aberto

Híbrido (auto + tool) vs full model-routed? Recomendado: híbrido. Decidir grau
após shadow.
