# Spec: Evolução do Ecossistema de Conhecimento

**Data:** 2026-06-15
**Status:** Proposto — aguarda implementação
**Branch sugerida:** `feat/knowledge-evolution`

## Princípio transversal

*Autonomia com observabilidade.* O sistema age, mas todo ato autônomo é logado, reversível, e
surfaçado numa review feed. "AI crafts, Human decides" vira "AI acts, Human can correct."

Isso se aplica especificamente a: pesos de matching, playbooks (learned overlays), e
reflection_insights. Para `deep_research → library`, o gate humano permanece — só a
fricção do gate é reduzida (staging area + um clique).

---

## Item 1 — Fechamento do loop de pesos

**Contexto:** `reflection_service.py` gera `weight_suggestions` com confidence level mas nunca
aplica. O humano precisa editar `matching_weights` manualmente.

**Decisão:** Aplicação automática condicionada a três guardas simultâneos:
- `confidence == "high"`
- `outcomes_considered >= 5`
- `|delta| <= 5` por dimensão por ciclo

Toda aplicação cria linha em `weight_change_log` (rationale, delta, confidence,
outcomes_window). Feed de auditoria na UI com botão de reverter.

**Consequências:**
- Positivo: loop fecha sem fricção para casos claros
- Risco: confidence é auto-reportada pelo mesmo LLM — sem validação cruzada.
  Mitigação: cap de delta + reversibilidade eliminam lock-in
- Evolutivo: após N ciclos, comparar performance do match antes/depois como sinal real

**Plano:**
1. Verificar o que `core/weight_approval.py` já faz (scaffolding existente)
2. Migration: tabela `weight_change_log`
3. Modificar `reflect_workspace` para chamar aprovação condicional no final
4. Endpoint `GET /me/weight-changes` para feed de auditoria
5. UI: banner "X dimensões ajustadas automaticamente" com link para log

---

## Item 2 — `deep_research` → memória permanente (Fase B)

**Contexto:** Sub-agente descobre fatos com fontes, tudo morre no turno. Gate humano
permanece — o que falta é construir a staging area.

**Decisão:** Tabela `research_findings` onde resultados chegam automaticamente, tagueados
`source="deep_research"` e `verified=false`. Usuário vê fila de findings pendentes e decide
o que promover para `content_library`.

Guardrails: TTL de 30 dias para findings não revisados; limite de N findings pendentes por
workspace antes de o `deep_research` parar de criar novos.

**Plano:**
1. Migration: `research_findings` (workspace_id, question, answer, sources, query,
   created_at, reviewed_at, promoted_to_library_id)
2. Modificar tool `deep_research` para inserir em `research_findings` além de retornar string
3. Endpoints `GET /research-findings` e `POST /research-findings/{id}/promote`
4. UI: fila no painel de library com badge "não verificado"

---

## Item 3 — Aprendizado inter-workspace (destilação de playbooks)

**Contexto:** Insights ficam isolados por `workspace_id`. Padrões cross-tenant nunca
enriquecem os playbooks.

**Decisão:** Dois componentes:

**A) Meta-reflexão cross-tenant** (job periódico): agrega `application_log` anonimizado
por `(mechanism, source)`, roda reflexão LLM, gera padrões de nível 3 (cross-workspace).

**B) Learned overlays no banco**: tabela `playbook_overlays`
`(mechanism, source, section, body, confidence, origin)`. O loader de `core/skills.py`
faz merge em 4 camadas:

```
base git (mechanism/*.md)
  + source git (source/<fonte>/global.md + source/<fonte>/<mech>.md)
  + learned overlay do banco  ← o sistema escreve aqui
  + overlay deste workspace   ← futuro
```

Os arquivos `.md` em git continuam sendo o seed canônico humano; o banco é onde o
sistema aprende. Learned overlays adicionam por seção, nunca sobrescrevem o arquivo todo.

**Consequências:**
- Positivo: playbooks evoluem com evidência real sem PR humano
- Risco: learned overlays podem contradizer o seed. Mitigação: UI de "playbook atual"
  mostra o que veio de onde (camada por camada)
- Dependência: precisa de volume cross-tenant — threshold configurável por (mechanism, source)

**Plano:**
1. Migration: `playbook_overlays` + `meta_reflection_runs`
2. Job `run_meta_reflection` (semanal): agrega outcomes anonimizados, chama LLM, escreve overlays
3. Modificar `load_playbook` em `core/skills.py` para consultar `playbook_overlays` como 4ª camada
4. Endpoint `GET /playbooks/{mechanism}/layers` — playbook resolvido camada por camada (auditoria)
5. UI: indicador "X overlays aprendidos" no painel de configurações

---

## Item 4 — Compressão episódica com extração de sinal

**Contexto:** Ao comprimir sessões longas (>10 turnos), o que falhou no processo — rejeições
do Critic, correções do usuário — se perde na narrativa.

**Decisão:** Dois prompts separados ao comprimir, rodando em paralelo:

**Prompt A (existente, refinado):** compressão narrativa → `writing_sessions.summary`.
Alimenta o próximo turno. Captura "o que foi dito".

**Prompt B (novo):** extração de sinal estruturado → `reflection_insights`. Extrai:
- Seções que o Critic rejeitou + número de iterações até aprovar
- Afirmações que o usuário corrigiu explicitamente
- Seções que fluíram sem atrito (sinal positivo)

Saída JSON no schema de `reflection_insights` (level=1), marcado `origin="episodic_compression"`.

O passo de "extrator de por que falhou" colapsa dentro da compressão — mesmo momento, dois propósitos.

**Plano:**
1. Implementar `extract_session_signal(turns, session_id, workspace_id)` → lista de insights
2. Chamar em paralelo com compressão narrativa (`asyncio.gather`)
3. Inserir em `reflection_insights` com `origin="episodic_compression"`
4. Rodar também em `POST /writing/{id}/close` para sessões abaixo do threshold de compressão

---

## Item 5 — Critic como sub-agente

**Contexto:** Critic atual é 1-shot. Faz um único retrieve (primeiros 500 chars do rascunho
como query) e decide. Não tem acesso ao `CompanyProfile` — não detecta elegibilidade incorreta.

**Decisão:** Refatorar `run_critic` para usar `run_subagent` com 3 tools e `max_steps=3`:

| Tool | Função |
|------|--------|
| `search_edital(query)` | Retrieve RAG dirigido — Critic escolhe a query |
| `read_company_profile()` | Retorna CompanyProfile serializado → checagem de elegibilidade |
| `read_proposal_sections()` | Retorna outras seções já redigidas |

Contrato preservado: **só bloqueia por contradição, nunca por omissão, na dúvida aprove.**
Temperature: 0.05. Falha graciosa: erro no sub-agente → `CriticResult(approved=True)`.

**Consequências:**
- Positivo: pega contradições que retrieve inicial perdia; detecta elegibilidade incorreta (novo)
- Risco: mais raciocínio pode gerar falsos positivos. Mitigação: temperatura mínima + instrução
  reforçada + max_steps baixo limita exploração
- Observabilidade: logar steps count por decisão — média > 1.5 retrieves indica retrieve inicial fraco

**Plano:**
1. Criar `build_critic_tools(session)` com as 3 tools
2. Refatorar `run_critic` para usar `run_subagent(name="critic", max_steps=3, ...)`
3. Adicionar `CompanyProfile` via tool `read_company_profile`
4. Logar `steps` count por decisão para calibração futura

---

## Dependências e ordem de implementação

```
Item 4 (compressão + sinal)
    └→ alimenta Item 1 (pesos) com mais evidência
    └→ alimenta Item 3 (cross-workspace) com insights mais ricos

Item 1 (pesos)           ← pode ser feito agora, infraestrutura existe (weight_approval.py)
Item 5 (Critic)          ← independente, arquiteturalmente isolado
Item 2 (deep_research)   ← independente, nova tabela
Item 3 (cross-workspace) ← depende de volume; após Item 4 consolidado
```

**Sprint sugerida:**
- Sprint 1: Item 1 + Item 5 (menores, impacto imediato)
- Sprint 2: Item 4 (fundação do aprendizado)
- Sprint 3: Item 2 (Fase B do deep_research)
- Sprint 4: Item 3 (cross-workspace — precisa de dados acumulados)
