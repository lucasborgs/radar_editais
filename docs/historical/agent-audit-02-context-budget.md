# 02 — Orçamento de contexto nas tool-results (Finding G)

**Fase:** 1 (plumbing) · **Validação:** teste + eval · **Esforço:** baixo-médio

## Problema

As tool-results do Redator são strings appendadas ao histórico do agente sem
nenhum truncamento. Em sessão longa (`reflect_every=3`, `max_steps`), o contexto
cresce sem teto — o mesmo problema de ">150k" no nível do produto.

## Estado atual (sem cap)

- `read_full_proposal` (`writing_tools.py:158-168`): concatena **todas** as seções,
  sem cap. Proposta de 8 seções × ~1000 palavras → 8000+ palavras numa tool-result.
  A docstring já avisa "Caro de tokens" (`:148-150`).
- `search_edital` (`writing_tools.py:48`, `k=5`): chunks devolvidos **inteiros**;
  `retriever.py:516` faz `c.get('text','').strip()` sem corte por chunk nem total.
- `search_library` (`writing_tools.py:88`, `k=3`): summary + até 4 key_facts/item,
  sem cap de chars.
- `read_section` (`:141`), `recall_company_learnings` (`reflection_service.py:283`,
  `max_total=6`): sem cap de chars.

**Caps que já existem** (para referência de padrão): `fetch_page` 12k
(`profile_tools.py:36`), `fetch_url` 3k (`deep_research.py:25`),
`_documents_text[:12000]` (`writing_session.py:593`).

## Mudança proposta

1. **Cap central no loop:** em `run_agent`, após `output = t.call(...)`
   (`agent_runtime.py:665`), aplicar `output = _cap(output, TOOL_RESULT_CHAR_CAP)`
   com marcador de truncamento (`…[truncado: N chars omitidos]`).
   `TOOL_RESULT_CHAR_CAP` via env (default ex.: 8000).
2. **Caps por tool onde faz sentido semântico:**
   - `read_full_proposal`: cap total + aviso para o modelo usar `read_section`
     quando precisar de detalhe.
   - `search_edital`: cap por chunk (ex.: 1500 chars/chunk) além do cap total.
3. **Observabilidade (shadow leve):** logar quando o cap dispara (qual tool, quanto
   cortou) para calibrar o limite antes de apertar.

## Validação

- **Teste:** tool que retorna 50k chars → result capado com marcador.
- **Eval gate (essencial):** `python -m radar.core.eval writing` — truncar pode derrubar
  qualidade se cortar informação necessária. Promover só se score se mantém.

## Risco

Médio: cap agressivo demais remove contexto útil. Mitigação = começar folgado,
logar disparos, apertar com eval no loop.

## Perguntas em aberto

- Cap por chars (simples) vs token-aware (mais preciso, exige tokenizer) vs
  sumarização-no-overflow (mais caro)? Recomendado: chars primeiro, medir.
