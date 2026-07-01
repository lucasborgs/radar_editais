# Spec — Edital Card como InjectedState no WritingAgent

Status: **proposta** · 2026-06-30 · escopo: injetar o card completo do edital (`edital_card(full=True)`) como estado inicial do grafo do WritingAgent, antes de qualquer tool call ou RAG.

---

## Decisões pinadas (não revisitar)

| # | Decisão |
|---|---------|
| D1 | Card é carregado síncrono em `generate_full_proposal()` antes de iniciar o grafo — sem dependência de async |
| D2 | Só os campos **estruturados úteis** são injetados (`key_requirements`, `exclusoes`, `mechanism`, `themes`, `technologies`, `publico_alvo`, `objective`). Campos de exibição pura (`id`, `source`, `status`, `deadline`) ficam de fora |
| D3 | Card entra como **primeiro item** do `documents` dict ([`InjectedState("documents")`](cci:1://file:///Users/lucasborges/radar_editais/core/llm/agent_tools/writing_tools.py:84:78)) — antes dos chunks do RAG. O agente lê sem tool call |
| D4 | `search_edital` continua disponível — card não o substitui, só reduz a necessidade |
| D5 | Custos: ~500-800 tokens adicionais no primeiro turno apenas (reutilizado em turnos seguintes pelo state persistente) |

---

## Motivação

### Problema

Hoje o WritingAgent só conhece o `edital_id` quando a sessão começa. Para descobrir informações básicas como requisitos, exclusões, público-alvo e mecanismo, ele precisa chamar `search_edital` (RAG sobre chunks). Isso:

- Custa 1-2 tool calls extras no primeiro turno (~10-20s de latência)
- A primeira resposta já poderia vir mais alinhada ao escopo do edital
- O agente não tem visibilidade de regras de exclusão até fazer a busca

### Cenário

O WritingAgent recebe a descrição do projeto e precisa escrever 8 seções. Sem o card, ele:

1. Chama `search_edital("quais os requisitos?")`
2. Chama `search_edital("qual o mecanismo?")`
3. Só então começa a escrever

Com o card, os passos 1-2 desaparecem — a informação já está no estado inicial.

---

## Implementação

### Onde

| Arquivo | O que muda |
|---|---|
| `core/services/writing_session.py` | `generate_full_proposal()`: carregar card e converter para documento antes de iniciar o grafo |
| `core/llm/agent_tools/writing_tools.py` | `build_writing_tools()`: sistema pode ler do `documents` sem tool call; system prompt ganha menção ao card |

### Fluxo

```
generate_full_proposal(edital_id, profile):
  card = hypergraph_catalog.get_edital(edital_id)    ← novo
  doc = card_to_writer_doc(card)                     ← novo

  state = {
    "edital_id": edital_id,
    "profile": profile,
    "documents": {"card_edital": doc},                ← card como doc base
  }
  run_generation_turn(state)
```

Onde `card_to_writer_doc()` produz:

```
=== CARD DO EDITAL ===
Objetivo: {objective}
Mecanismo: {mechanism}
Temas: {themes}
Tecnologias: {technologies}
Programas: {programs}
Público-alvo: {publico_alvo}
Requisitos principais:
- {key_requirements[0]}
- {key_requirements[1]}
- ...
Exclusões:
- {exclusoes[0]}
- ...
```

### System prompt

Adicionar no `WRITER_AGENT_SYSTEM` (ou `GENERATION_WRITER_SYSTEM`):

```
VOCÊ TEM ACESSO AOS DADOS DO EDITAL NO state.documents["card_edital"] — leia
antes de chamar search_edital. Esse documento contém objetivo, requisitos,
exclusões, mecanismo, temas, tecnologias e público-alvo do edital. Use-o como
referência primária; search_edital é para consultas complementares.
```

### Tool `search_edital`

A tool já lê `documents` do state. Basta que o card seja o primeiro item — o modelo decide quando buscar mais.

---

## Teste

### Caso 1: primeiro turno sem tool call de busca

1. Iniciar sessão de escrita para `finep:773` (subvenção)
2. Enviar descrição do projeto
3. Verificar que a primeira resposta do agente menciona requisitos e exclusões do card
4. Verificar que `search_edital` NÃO foi chamado

### Caso 2: card não carrega (edital sem card)

1. Iniciar sessão para um `edital_id` que retorna `None` em `get_edital()`
2. Verificar que o sistema cai graciosamente (sem card injetado)
3. Agente usa `search_edital` normalmente

---

## Riscos

| Risco | Mitigação |
|---|---|
| Agente ignora o card e chama search_edital | Inofensivo — duplica informação, sem prejuízo |
| Card grande demais (~1200 tokens) | Máximo observado é ~1200 tokens (finep:739 antes da normalização). Depois da migração caiu ~800. |
| Card desatualizado vs RAG (chunks mais recentes) | Card é derivado do mesmo hipergrado que alimenta os chunks. Mesma base. |
