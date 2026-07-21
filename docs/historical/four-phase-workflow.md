# Especificação: Fluxo de 4 Fases (Intake → Planning → Execution → Refinement)

**Versão:** 1.0  
**Data:** 2026-07-05  
**Autores:** Lucas Borges, Claude Code  
> **Registro histórico:** plano que originou o workspace multi-modo. Os detalhes
> de implementação abaixo incluem nomes substituídos; o runtime vigente está em
> [`docs/architecture.md`](../architecture.md).

**Status original:** Design Review

---

## 1. Visão Geral Arquitetural

### Objetivo
Separar exploração (Intake) de planejamento (Planning) e execução (Execution) para criar fluxo fluido controlado pelo usuário, eliminar truncamento, integrar compliance e permitir esclarecimentos laterais durante escrita.

### Premissas
- Um grafo LangGraph único com nós distintos (não dois grafos separados)
- Transições controladas pelo usuário (não automáticas)
- Planning é **condicional** (skip se pergunta simples)
- Compliance integrado no Critic (seção por seção), não monitor paralelo
- WritingSession pode chamar ExploreAgent como subagente pra esclarecimentos

### Arquitetura de Alto Nível
```
FASE 0: Intake (ExploreAgent, 25 steps)
  ↓ [user clica "Estruturar" ou "Começar"]
  
├─→ FASE 1: Planning (novo nó, condicional, 15 steps)
│     ↓ [user aprova plano]
│     
└─→ FASE 2: Execution (WritingSession, 50 steps/seção)
      ↓ [durante execution: user pode chamar Explore subagente]
      ↓ [per-seção: Critic integrado]
      ↓ [user clica "Refinement"]
      
      FASE 3: Refinement (opcional, WritingSession modo local)
```

---

## 2. FASE 0: INTAKE (Exploração)

### Propósito
Usuário faz perguntas abertas sobre o edital/oportunidade. ExploreAgent responde com contexto enriquecido (RAG hipergrado + web search condicional).

### Contrato de Entrada
```python
ExploreAgent.explore(
    message: str,                    # pergunta do user
    history: list[dict],             # histórico da conversa
    edital_ids: list[str] | None,    # contexto de clique
    node_id: str | None,             # nó focado (ex: tema)
    node_type: str | None,           # tipo do nó
    has_profile: bool,               # user logado?
    profile_text: str | None,        # perfil da empresa (ex: resumo)
    workspace_id: str | None,        # DB access
    db=None,
    profile: dict | None,            # dados estruturados
) → str, meta
```

### Comportamento
1. **MAX_STEPS = 25** (era 10, sem truncamento)
2. **Tools disponíveis:**
   - explore_tools: list_editais, get_edital, get_node_neighborhood, find_matching_editais, find_matching_entities
   - planning_tools: write_todos (opcional, pra perguntas multi-parte)
   - research_tools: deep_research (subagente web) **se `EXPLORE_DEEP_RESEARCH_ENABLED=true`**
   - exploration_log: log_exploration_decision (memória cross-session, se autenticado)

3. **Resposta:** texto diretivo (2-3 frases) + cards visuais (frontend renderiza)

4. **Cache:** LLM + RAG hits com TTL 900s (padrão)

5. **Profile enrichment (se autenticado):** Ao final do Intake (antes de oferecer transição), se `has_profile=True`, roda `ProfileExtractor` sobre o histórico da conversa para extrair/atualizar `company_nodes` (nós da empresa: temas, tecnologias, aplicações). Esses nodes alimentam Planning com contexto enriquecido.

### Critério de Saída (transição para próxima fase)
Agente termina naturalmente (step count < 25, stop_reason="end_turn") e oferece:
```
"Pronto explorar esse edital. Quer estruturar uma proposta completa ou já começar a escrever?"
```

**Opções apresentadas:**
- "Estruturar" → Planning (FASE 1)
- "Começar" → Execution (FASE 2) **sem plan**

### Metadados Expostos
```python
{
    "stop_reason": "end_turn" | "max_steps" | "error",
    "truncated": bool,  # true se stop_reason == "max_steps"
    "steps_taken": int,
}
```

Se `truncated=true`, UI avisa: "Resposta extensa — continuar na próxima conversa" (não bloqueia transição).

---

## 3. FASE 1: PLANNING (Planejamento de Proposta)

### Propósito
Estruturar a proposta em seções, mapear afinidades empresa↔edital, gerar pré-preenchimentos via RAG/hipergrado.

### Decisão: Quando ativar Planning
Planning é **auto-detectado** em Intake via heurística `is_complex_proposal(message, analysis)`:

**Oferta ao user:**
- Se `is_complex_proposal=true` → "Estruturar proposta" (Planning) + "Começar a escrever" (Execution direto)
- Se `is_complex_proposal=false` → só "Começar a escrever" (Execution direto)

**Heurística:** `is_complex_proposal = True` se (1) message contém "proposta" ou "escrever", **E** (2) analysis retornou >300 palavras **E** (3) history.length > 1 (não é primeira pergunta). Senão, false.

### Contrato de Entrada
```python
planning_node(
    intake_context: {
        "question": str,           # pergunta original
        "analysis": str,           # resposta do Intake
        "edital_id": str,
        "company_nodes": [
            {"type": str, "names": list[str]},  # ex: {"type": "tema", "names": ["IA", "saúde"]}
            # tipos: "tema", "tecnologia", "aplicacao", "setor"
        ] | None,
    }
) → plan: dict
```

### Saída: Estrutura de Plano
```python
{
    "title": "Proposta: FINEP Mais Inovação (ID:FINEP:773)",
    "sections_total": 5,  # NEW: número exato de seções
    "sections": [
        {
            "id": "impacto",
            "title": "Impacto e Aplicabilidade",
            "description": "Demonstrar relevância da solução",
            "key_points": [
                "Alinhar com as 3 áreas do edital: IA, agritech, energia",
                "Citar expertise em IA em saúde (nó da empresa)",
            ],
            "estimated_length": "300-400 palavras",
            "pre_fill": "Sua solução de IA para diagnóstico...",  # RAG-preenchido
        },
        {
            "id": "metodologia",
            "title": "Metodologia e Abordagem",
            "description": "Detalhar cronograma, equipe, deliverables",
            "key_points": [...],
            "estimated_length": "250-350 palavras",
            "pre_fill": None,
        },
        # ... mais seções
    ],
    "alignment": {
        "company_themes": ["IA em saúde", "dispositivos médicos"],
        "edital_themes": ["IA", "saúde", "inovação aberta"],
        "match_score": 0.78,
        "critical_gaps": ["Menção de parceria com ICT — edital exige"],
    },
    "compliance_hints": [
        "Edital aceita apenas PMEs e startups — sua empresa qualifica?",
        "Mecanismo é subvenção, não crédito — sem juros, mas reembolso exigido",
    ],
}
```

### Tools disponíveis em Planning
- get_edital, get_node_neighborhood (leitura hipergrado)
- RAG retrieve_chunks (pré-preenchimentos)
- (Opcional) deep_research se web search ativado

### MAX_STEPS = 15
**Justificativa:** Planning é estruturação de dados coletados em Intake (não exploração); média ~8-10 steps (RAG pré-preenchimentos + cosine ranking). Margem 1.5x pra casos complexos.

### Fluxo de Usuário
1. **UI renderiza plano** como outline clicável
2. **Opções:**
   - ✅ "OK, começar" → Execution com este plan
   - ✏️ "Ajustar seções" → User edita o plano inline (frontend), muda armazenadas em `user_adjustments: dict`
   - ↩️ "Voltar" → retorna a Intake (descarta plano)

### Saída: Approval Gate
```python
approval = {
    "approved": bool,
    "plan": dict,  # estrutura acima (com sections_total)
    "user_adjustments": {
        "sections": {
            "impacto": {"description": "novo texto", "key_points": [...]},
            # ... seções ajustadas
        }
    } | None,
}
```

**Fluxo Execution:** Mescla `plan + user_adjustments` antes de usar (adjustments override plan).

---

## 4. FASE 2: EXECUTION (Escrita da Proposta)

### Propósito
Escrever a proposta seção-por-seção, usando plano como guia (se gerado em Planning). Integrar Critic pra qualidade+compliance. Permitir esclarecimentos laterais via ExploreAgent subagente.

### Contrato de Entrada
```python
writing_session.start(
    edital_id: str,
    workspace_id: str,
    plan: dict | None,              # se None, WritingSession é livre/resumo
    user_adjustments: dict | None,  # mudanças feitas em Planning (mergeado ao plan)
    user_brief: str | None,         # briefing adicional do user
    db=None,
) → session_id: str
```

**Inicialização:**
```python
final_plan = plan
if user_adjustments:
    final_plan = merge(plan, user_adjustments)  # user_adjustments override
state["sections_total"] = len(final_plan.get("sections", []))
state["sections_completed"] = []
```

### Comportamento

#### 4.1 Inicialização
1. Se `plan` fornecido: WritingSession lê as seções em ordem
2. Se `plan=None`: WritingSession cria outline dinâmico (comportamento atual, mas mais rápido)
3. Carrega edital_chunks (RAG) indexados por seção (prefetch em paralelo)

#### 4.2 Loop por Seção
```
Para cada seção:
  1. Agente lê: plan[section] + contexto RAG
  2. Escreve conteúdo (LLM, max 500-1000 palavras)
  3. Checkpoint de turno
  4. User vê seção renderizada
  5. Opções:
     ✅ "Próxima seção" → salva e avança
     ✏️ "Refazer esta" → agente reataca a mesma
     ❓ "Saiba mais sobre..." → call ExploreAgent subagente (veja 4.3)
     ⏸️ "Salvar e revisar depois" → checkpoint persistido
     ↩️ "Replanejar" → volta a Planning (com seções já feitas em contexto)
```

#### 4.3 **Subagente Explore Inline (NEW)**

**Quando:** User clica "Saiba mais sobre X" durante Execution

**Fluxo:**
```python
# WritingSession roda uma tool:
def ask_about_edital(question: str) -> str:
    """Chama ExploreAgent como subagente pra esclarecimento lateral."""
    from radar.core.services.explore_agent import ExploreAgent
    from radar.core.llm.agent_runtime import run_subagent
    
    # Subagente + contexto da seção
    result = run_subagent(
        name="explore_in_writing",
        system="Você é um assistente que responde perguntas sobre o edital "
               "enquanto o usuário escreve a proposta. Responda concisamente.",
        user_message=question,
        tools=[...explore_tools...],  # mesmas tools de Intake
        max_steps=10,
    )
    return result.final_text
```

**Resultado:** resposta inline no chat, volta ao contexto de escrita automaticamente.

**Cache:** O contexto de Execution (seções já escritas) fica preservado; Explore subagente não acessa.

#### 4.4 Critic Integrado (Qualidade + Compliance)

**Quando:** Após agente escrever seção, **antes** de checkpoint

```python
def evaluate_section(section_id: str, content: str, edital_id: str) -> dict:
    """Critic sub-agente avalia qualidade + compliance de 1 seção."""
    
    result = run_subagent(
        name="critic",
        system="""Você é um revisor de propostas para editais de fomento.
        Checklist:
        1. Qualidade textual (gramática, clareza, coesão)
        2. Completude (respondeu às instruções da seção?)
        3. Compliance (requisitos específicos do edital — veja abaixo)
        4. Alinhamento (empresa vs. edital)
        
        Requisitos obrigatórios deste edital:
        {edital_requirements_from_hypergraph}
        
        Responda JSON:
        {
            "quality_score": 0-10,
            "compliance_issues": [
                {"requirement": "str", "status": "pass|fail|warning", "suggestion": "str"}
            ],
            "overall_recommendation": "approve|revise|reject",
            "feedback": "str (2-3 frases)"
        }""",
        user_message=f"Seção: {section_id}\nConteúdo:\n{content}",
        max_steps=5,
    )
    return json.loads(result.final_text)
```

**Fluxo de User:**
1. Agente escreve seção
2. Critic roda automaticamente
3. Se `overall_recommendation="approve"` → mostra "✅ Pronta"
4. Se `"revise"` → mostra feedback + opção "Refazer" ou "Salvar assim mesmo"
5. Se `"reject"` → mostra razão crítica + força "Refazer"

#### 4.5 MAX_STEPS = 50 (por seção completa)

Se bater o teto em 1 seção → oferece "continuar depois" (salva checkpoint).

#### 4.5 Error Handling em Sub-Agentes

**Ask About Edital (Explore subagente):**
- **Timeout (>10s):** Fallback: "Não consegui esclarecer agora. Continuar com a seção?" (preserva escrita, pula esclarecimento)
- **JSON parse fail / invalid response:** Fallback: resposta em texto limpo (não estruturado), agente continua
- **Retry policy:** 0 (single-shot; user pode perguntar novamente)

**Critic (Qualidade + Compliance):**
- **Timeout (>10s):** Assume `overall_recommendation="approve"` com flag `critic_skipped=true` (seção salva com aviso visual)
- **JSON parse fail:** Assume `overall_recommendation="approve"`, flag `critic_error=true`
- **Retry policy:** 1 retry se JSON fail; se falhar 2x, trata como timeout

**Execution timeout (bate MAX_STEPS=50):**
- Oferece "Salvar e continuar depois" (checkpoint durável)
- Próxima entrada no WritingSession resume naquela seção

### Contrato de Saída
```python
{
    "session_id": str,
    "status": "in_progress" | "ready_for_review",
    "sections_completed": int,
    "sections_total": int,
    "draft": {
        "edital_id": str,
        "sections": [
            {
                "id": str,
                "title": str,
                "content": str,
                "critic_feedback": dict | None,
                "version": int,
            },
            ...
        ],
    },
    "checkpoints": [
        {
            "timestamp": str,
            "section_id": str,
            "status": "saved",
        },
        ...
    ],
}
```

### Saída: Approval Gate (seção por seção)
```python
# Após escrita:
{
    "section_content": str,
    "critic_score": 0-10,
    "compliance_passed": bool,
    "user_options": [
        {"label": "Próxima", "action": "next_section"},
        {"label": "Refazer", "action": "rewrite_section"},
        {"label": "Saiba mais", "action": "ask_explore"},
        {"label": "Replanejar", "action": "back_to_planning"},
        {"label": "Salvar e voltar", "action": "checkpoint"},
    ]
}
```

---

## 5. FASE 3: REFINEMENT (Revisão e Ajustes)

### Propósito
User lê draft completo, pede ajustes pontuais em seções específicas. WritingSession roda em modo local (1 seção por vez).

### Acionamento
Quando `sections_completed == sections_total` e user clica "Revisar antes de enviar".

### Fluxo
1. **UI renderiza draft completo** (seções 1..N, readeable format)
2. **User clica em seção X** → abre editor
3. **Opções:**
   - ✏️ "Refazer esta seção" → WritingSession escreve a seção X (nova) com contexto
   - 🔧 "Editar manualmente" → UI text editor (não envolve agente)
   - ↩️ "Voltar ao draft" → descarta mudanças
4. **Após refazer:** Critic roda novamente, mesmo fluxo da Execution (approve/revise/reject)
5. **Após aprovação:** seção é atualizada, user continua com próxima (ou termina)

### Contrato
```python
refinement_turn(
    session_id: str,
    section_id: str,
    user_instruction: str,  # "deixa mais técnico", "resumir", "adicionar dados"
) → updated_section: dict
```

### MAX_STEPS = 20 (por refinement)
**Justificativa:** Refinement é instrução user muito focada (ex: "deixa mais técnico"); reescrita é lightweight (~5-8 steps). Critic menos rigoroso (seção já passou 1x). Margem 2.5x.

### Saída
```python
{
    "section_updated": bool,
    "new_content": str,
    "critic_feedback": dict,
    "options": ["approve", "refazer_novamente", "voltar"]
}
```

---

## 6. Integração: ExploreAgent como Subagente

### Objetivo
Permitir que WritingSession (Execution + Refinement) chame ExploreAgent internamente pra responder perguntas laterais sobre edital/oportunidade.

### Implementação
```python
# core/llm/agent_tools/writing_tools.py

from radar.core.deep_research import run_deep_research  # já existe
from radar.core.services.explore_agent import ExploreAgent  # novo

@tool
def ask_about_edital(question: str) -> str:
    """Esclarece dúvidas sobre o edital sem sair do contexto de escrita.
    
    Usado quando user clica 'Saiba mais...' durante escrita.
    Retorna resposta concisa (~200 palavras).
    """
    explore_agent = ExploreAgent()
    
    # Contexto: edital_id + seção atual (do WritingSession.state)
    edital_id = current_session_state["edital_id"]
    current_section = current_session_state["current_section"]
    
    answer = explore_agent.explore(
        message=question,
        edital_ids=[edital_id],
        has_profile=False,  # stateless, public explore
        profile_text=None,
    )
    
    return f"Sobre '{current_section}':\n{answer}"
```

### Chamada
```
WritingSession.tools = [write_section, ask_about_edital, save_checkpoint, ...]
```

---

## 7. Compliance: Integração Architural

### Mudança: De Bolted-On a Integrado

**Antes (paralelo, quebrado):**
```
Agent escreveu seção → ComplianceMonitor roda em paralelo → retorna flags genéricas
```

**Depois (integrado):**
```
Agent escreveu seção → Critic subagente (qualidade + compliance) → passa/falha estruturada
```

### Critic Checklist
```python
compliance_rules = {
    "elegibilidade_dura": {
        "requirement": "Empresa deve ser PME ou startup",
        "rule": lambda company: company["is_pme"] or company["is_startup"],
        "suggestion": "Se não qualifica, remover essa aplicação",
    },
    "mecanismo_subvencao": {
        "requirement": "Subvenção não reembolsável — não mencionar juros/devolução",
        "rule": lambda text: not any(w in text.lower() for w in ["juros", "devolução", "reembolso"]),
        "suggestion": "Reescrever: benefício não reembolsável, apenas contrapartida",
    },
    "parceria_ict_exigida": {
        "requirement": "Edital exige parceria com ICT (EMBRAPII, universidade, etc)",
        "rule": lambda text, edital: edital["requires_ict"] and any(ict in text for ict in edital["eligible_icts"]),
        "suggestion": "Mencionar parceria específica (ex: UFSC, EMBRAPII)",
    },
}
```

### Fluxo User
1. Agente escreve
2. Critic roda (automático)
3. Se compliance issue:
   ```
   ⚠️ Requisito não atendido: Subvenção não exige reembolso
   → "Refazer esta seção" ou "Salvar assim mesmo"
   ```
4. Se user clica "Salvar assim mesmo" → seção salva com `compliance_warning=true` (visível em draft)

---

## 8. Fluxo de Dados e Persistência

### Estado Durável (Postgres)
```sql
-- writing_sessions
id, workspace_id, edital_id, plan (JSONB), created_at, updated_at

-- writing_checkpoints
id, session_id, section_id, content (TEXT), critic_feedback (JSONB), 
version, created_at

-- exploration_log (já existe)
id, workspace_id, edital_id, decision (recommended|discarded), reason
```

### Estado Efêmero (LangGraph)
```python
# agent_graph.State
class WritingState(TypedDict):
    messages: list  # LangGraph messages
    sections_completed: list[str]
    current_section: str | None
    plan: dict | None
    edital_id: str
    # ... mais campos
```

### Fluxo de Salvar
```
User clica "Salvar" → WritingSession.save_checkpoint() → 
  → Critic passa → Postgres writing_checkpoints + update session → 
  → UI: "Seção salva ✅" + próxima opção
```

---

## 9. Transições e Contratos

### Transição Intake → Planning
```
URL: POST /explore/turn
Body: { message, history, edital_ids, ... }
Response: {
    answer: str,
    meta: { truncated, stop_reason },
    next_action: {
        "offer": "Estruturar proposta?",
        "options": [
            { "label": "Estruturar", "action": "goto_planning" },
            { "label": "Começar", "action": "goto_execution" },
        ]
    }
}
```

### Transição Planning → Execution
```
URL: POST /writing/start
Body: { edital_id, plan (JSONB), workspace_id, ... }
Response: {
    session_id: str,
    status: "in_progress",
    first_section: {
        id, title, content (agent-written), critic_feedback
    },
    options: [...]
}
```

### Transição Execution → Refinement
```
URL: GET /writing/sessions/{id}/document  # draft completo
Response: {
    status: "ready_for_review",
    sections: [...],
    overall_critic_score: 0-10,
    options: [
        { "label": "Enviar", "action": "submit" },
        { "label": "Ajustar", "action": "refinement_mode" },
    ]
}
```

---

## 10. Critérios de Sucesso

### Por Fase

#### Fase 0 (Intake)
- ✅ MAX_STEPS=25, sem truncamento em >90% das perguntas
- ✅ Oferece transição explícita (Planning ou Execution direto)
- ✅ Meta: <30s resposta (P95)

#### Fase 1 (Planning)
- ✅ Plano estruturado com 4-6 seções
- ✅ Pré-preenchimentos úteis (>50% reutilizados em Execution)
- ✅ User aprova/ajusta sem retorno a Intake
- ✅ Meta: <15s geração

#### Fase 2 (Execution)
- ✅ Seção por seção, cada uma <20s
- ✅ Critic integrado (0 flags genéricas, 100% estruturadas)
- ✅ ExploreAgent subagente responde esclarecimentos em <10s
- ✅ Compliance issues detectadas pré-salvamento
- ✅ Meta: draft completo (5 seções) <3 min

#### Fase 3 (Refinement)
- ✅ Ajuste pontual <10s por seção
- ✅ Critic roda novamente
- ✅ User não precisa replanejar

### Global
- ✅ **Fluidez:** Nenhuma truncagem surpresa (meta: 0 "continue a conversa")
- ✅ **Controle:** User decide quando transição (não automático)
- ✅ **Compliance:** 100% issues detectadas antes de persistir
- ✅ **Contexto:** Draft preserva histórico (seções 1-2 em contexto se volta de seção 3)

---

## 11. Implementação: Ordem de PRs

1. **PR1:** Planning (novo nó) + transição Intake→Planning
2. **PR2:** ExploreAgent como subagente (ask_about_edital tool)
3. **PR3:** Critic integrado (replace ComplianceMonitor)
4. **PR4:** Refinement mode
5. **PR5:** Ajustes finais + testes e2e

---

## 12. Decisões de Design Finalizadas

- ✅ **Compliance:** Warning não bloqueia (flag `compliance_warning=true` visível em draft); Reject bloqueia (reescrever obrigatório)
- ✅ **Contexto Planning→Execution:** `user_adjustments` mergea ao plano; contexto preservado via LangGraph state
- ✅ **Web search:** Apenas em Intake se `EXPLORE_DEEP_RESEARCH_ENABLED=true`; Planning/Execution não acionam web (foco em RAG + hipergrado)
- ✅ **Chars por seção:** 500-1000 palavras (~2-5k chars); seções longas são auto-chunked para RAG
- ✅ **Error handling:** Veja seção 4.6 (timeouts = fallback gracioso, parse fails = approve_with_flag)
