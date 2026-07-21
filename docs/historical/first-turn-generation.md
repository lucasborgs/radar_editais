# Spec — First-Turn Generation + Correction Scope Detection

> **Registro histórico:** a geração em lote do primeiro turno permanece ativa;
> o `scope_classifier` e o ripple descritos na parte B foram removidos. Consulte
> [`docs/architecture.md`](../architecture.md).

Status original: **proposta** · 2026-06-25 · escopo: dois aprimoramentos na WritingSession — (A) primeiro
turno gera draft completo + compliance de uma vez, (B) detecção de escopo de correção com ripple
automático

---

## Decisões pinadas (não revisitar)

| # | Decisão |
|---|---------|
| D1 | Primeiro turno especial **não cria endpoint novo** — o `WritingSession.turn()` detecta `_turn_count == 0` + seções vazias e desvia para geração em lote |
| D2 | Descrição do projeto entra como bloco extra no prompt de geração (`DESCRIÇÃO DO PROJETO PELO USUÁRIO`), entre perfil e instrução de seção |
| D3 | Checklist auto-review roda **em background paralelo** via SSE/async após as seções serem geradas. O checkpoint retorna imediatamente as seções; o compliance chega como evento separado (não bloqueia a resposta HTTP) |
| D9 | **Depth limit = 1 no ripple:** o classificador de escopo (D6) só dispara em `save_draft` acionado por ação humana direta. Reescreituras automáticas do ripple NÃO passam pelo classificador — evitam loop de cascata |
| D4 | Classificador de escopo roda **após cada `save_draft`** no modo conversacional (nunca no batch) |
| D5 | Ripple conceitual é **sugerido, não automático** — o sistema pergunta se o usuário quer atualizar as seções downstream |
| D6 | Classificador usa GPT-4o-mini (custo sub-centavo). Matriz estática de dependência mantida como fallback |
| D7 | O primeiro turno e o classificador compartilham o mesmo `section_dependency_matrix` — dicionário por outline |
| D8 | Métrica de sucesso: first-turn gera ≥6 de 8 seções com conteúdo válido (não placeholders); classificador acerta ≥85% cosmetic vs. conceptual |

---

## Contexto

Dois problemas de UX distintos, mesma camada (WritingSession):

**Problema A — Primeiro turno vazio.** Usuário chega à proposta e vê seções com placeholder.
Precisa conversar turno por turno para ter um draft. Referência: NotebookLM gera o documento
completo de uma vez a partir de uma instrução descritiva.

**Problema B — Correção sem ripple.** Usuário corrige o escopo do projeto ("não é IoT, é
blockchain") e o agente só reescreve a seção atual. As outras 7 seções continuam falando de IoT.
Não há detecção de "essa correção é cosmética ou conceitual?"

---

## Parte A — First-Turn Batch Generation

### Fluxo

```
POST /writing/start → session criada, seções vazias
         ↓
GET  /writing/{session_id} → info retorna section_drafts vazios
         ↓
Usuário digita no chat: "Meu projeto é um sistema de rastreamento..."
         ↓
POST /writing/turn (turn_count=0, sections vazias)
  → detecta first-turn mode
  → **gate de densidade:** descrição vaga? → cai no modo conversacional
  → armazena user_message como project_description
  → generate_full_proposal() com descrição injetada
  → [resposta HTTP imediata] {document, sections_done, failed_sections}
  → [SSE/background] checklist auto-review → push compliance_issues
         ↓
Frontend: preenche seções imediatamente, compliance chega em separado
```

### Mudanças no backend

**1. `WritingSession.__init__()` — novo campo**

```python
self._project_description: str | None = None
```

**2. `WritingSession.turn()` — point of entry**

```python
def turn(self, user_message: str, section_hint: str | None = None) -> dict:
    if self._turn_count == 0 and self._all_sections_empty():
        return self._first_turn_with_generation(user_message)
    # ... existing code (unchanged)
```

`_all_sections_empty()`: True se nenhuma seção do outline tem conteúdo com >50 chars.

**3. `WritingSession._first_turn_with_generation()` — novo método**

```python
def _first_turn_with_generation(self, user_message: str) -> dict:
    # Gate de densidade: descrição muito vaga? Cai no modo conversacional
    if self._is_vague_description(user_message):
        return self._turn_agent(user_message, None, 1)

    self._project_description = user_message

    # Batch generation (síncrono, seções em paralelo via orquestrador)
    outcome = self.generate_full_proposal()

    # Agenda checklist em background (não bloqueia a resposta)
    self._schedule_checklist_async()

    # Registra turno no transcript (best-effort)
    self._record_first_turn(user_message, outcome)

    return {
        "session_id": self.session_id,
        "assistant_message": self._first_turn_summary(outcome),
        "draft_content": None,
        "sections_done": outcome["sections_done"],
        "failed_sections": outcome["failed_sections"],
        "turn_number": 1,
        "success": True,
    }
```

`_is_vague_description()`: LLM rápido (GPT-4o-mini, 1 chamada, ~100 tokens) avalia se a
descrição tem densidade informacional mínima (menção a tecnologia, problema, solução ou
escopo). Se vago, retorna `True` — o sistema cai no chat conversacional normal e o agente
faz as perguntas necessárias antes de escrever.

**4. `_build_generation_section_messages()` — injeta descrição**

```python
def _build_generation_section_messages(self, section: str) -> list[dict]:
    messages = [
        {"role": "user", "content": f"PERFIL DA EMPRESA:\n{self._profile_context}"},
    ]
    if self._project_description:
        messages.append({
            "role": "user",
            "content": f"DESCRIÇÃO DO PROJETO PELO USUÁRIO:\n{self._project_description}",
        })
    # ... rest unchanged (pitch/programa context, library, reflection, instruction)
```

**5. `WritingSession._schedule_checklist_async()` — novo helper**

O checklist roda em background e entrega o resultado via SSE (Server-Sent Events) ou
polling. A resposta HTTP do primeiro turno nunca fica bloqueada esperando o compliance.

```python
def _schedule_checklist_async(self) -> None:
    """Agenda o checklist auto-review em background. O resultado é entregue
    via SSE (evento `compliance_ready`) ou consultado via GET /writing/{id}/checklist."""
    from radar.core.services.checklist_service import build_checklist, auto_review_checklist
    from radar.core.tasks import procrastinate  # ou Celery, ou asyncio.create_task

    requirements = build_checklist(self.edital_id)
    if not requirements:
        return  # sem requisitos → sem compliance

    proposal_text = "\n\n".join(
        f"# {t}\n{self._doc_sections.get(t, '')}"
        for t in self._proposal_outline
        if self._doc_sections.get(t, "").strip()
    )

    # Roda em thread separada; resultado publicado no DB (tabela compliance_results)
    # e notificado via SSE channel `checklist:{session_id}`.
    asyncio.create_task(self._run_checklist_worker(proposal_text, requirements))
```

Caminhos alternativos:
- **SSE:** Frontend assina canal `checklist:{session_id}` e recebe evento `compliance_ready`
- **Polling:** Frontend consulta `GET /writing/{session_id}/compliance` até status=ready
- **Fallback síncrono:** Se SSE/polling não disponíveis, checklist pode ser disparado
  explicitamente pelo usuário via botão "Verificar compliance"

O schema de resposta do primeiro turno reflete a natureza assíncrona — `compliance_ready`
indica se o resultado já está disponível (nunca no primeiro request):

```python
class WritingTurnResponse(BaseModel):
    session_id: str
    assistant_message: str
    draft_content: str | None
    compliance: dict | None = None        # presente apenas se já disponível
    compliance_status: str = "pending"    # NOVO: "pending" | "ready" | "unavailable"
    sections_done: list[str] = []
    failed_sections: list[str] = []
    pending_user_input: dict | None
    turn_number: int
    success: bool
    tool_trace: list[dict] | None
```

### Mudanças no frontend

- Após `/writing/start`, o input do chat já foca com placeholder "Descreva seu projeto em 2-3
  frases..."
- Resposta do primeiro turno: se `compliance` presente, exibe painel colapsável com issues de
  compliance abaixo do chat
- Seções do documento são preenchidas diretamente (como já acontece quando `save_draft` persiste)

---

## Parte B — Correction Scope Detection

### Fluxo

```
Turno conversacional: usuário envia correção
  → agente reescreve seção X
  → save_draft(X, force=False) → critic aprova → salva
  → [NOVO] scope classifier roda após o save
      ├─ "cosmetic" → nada, segue o fluxo
      └─ "conceptual" → agenda ripple, retorna na resposta:
           "ripple_suggestion": {
               "type": "conceptual",
               "affected_sections": ["Metodologia", "Orçamento", "Cronograma"],
               "message": "Sua correção mudou o escopo e impacta outras seções..."
           }
  → frontend exibe sugestão, usuário confirma ou ignora
         ↓
  Se usuário confirma (próximo turno):
    → agente recebe "O usuário autorizou o ripple da seção X para {seções}"
    → reescreve cada seção downstream, uma por vez
```

### Matriz de dependência (estática, zero LLM)

```python
# core/services/writing_session.py

PROPOSAL_DEPENDENCY_MATRIX: dict[str, list[str]] = {
    "1. Identificação da empresa":         [],
    "2. Objeto do projeto":               ["3. Justificativa", "4. Objetivos",
                                            "5. Metodologia", "6. Equipe",
                                            "7. Cronograma", "8. Orçamento"],
    "3. Justificativa e relevância":      ["4. Objetivos"],
    "4. Objetivos":                       ["5. Metodologia", "7. Cronograma"],
    "5. Metodologia e plano de trabalho": ["7. Cronograma", "8. Orçamento"],
    "6. Equipe técnica":                  ["8. Orçamento"],
    "7. Cronograma":                      [],
    "8. Orçamento":                       [],
}

PITCH_DEPENDENCY_MATRIX: dict[str, list[str]] = {
    "1. Problema":                        ["2. Solução"],
    "2. Solução e diferencial tecnológico": ["3. Mercado", "5. Time", "6. Fit com tese"],
    "3. Mercado (TAM/SAM/SOM)":           ["4. Tração", "7. Ask"],
    "4. Tração":                          ["7. Ask"],
    "5. Time":                            [],
    "6. Fit com a tese do fundo":         ["7. Ask"],
    "7. Ask e uso dos recursos":          [],
}
```

### Classificador

Novo módulo: `core/llm/agent_tools/scope_classifier.py`

```python
CORRECTION_SCOPE_SYSTEM = """You analyze a user correction to a proposal section and classify
its scope.

The user sent a correction request, and the agent rewrote section "{section_title}" accordingly.

Compare the OLD content (before correction) with the NEW content (after correction).
Then classify:

COSMETIC: tone, phrasing, formatting, word choice, grammar, reordering of information.
    The factual content, scope, technology, approach, numbers, and structure are unchanged.
CONCEPTUAL: scope, technology/methodology, objectives, team composition, budget values,
    timeline, approach/methodology, or any factual claim changed.

Output JSON:
{
    "type": "cosmetic" | "conceptual",
    "reasoning": "1-sentence justification",
    "changed_elements": ["list", "of", "changed", "aspects"]
}
"""
```

Chamado em `writing_tools.py` dentro de `save_draft`, após o critic aprovar, **apenas em
save_draft acionado por ação humana direta** (D9):

```python
# After successful save, in conversational mode (not batch),
# and NOT triggered by automatic ripple (D9 depth limit):
if not force and not getattr(session, '_batch_mode', False) \
       and not getattr(session, '_ripple_active', False):
    scope = classify_correction_scope(old_content, content, target_title, session)
    if scope and scope.get("type") == "conceptual":
        session._ripple_suggestion = {
            "source_section": target_title,
            "affected_sections": scope.get("changed_elements", []),
        }
```

O flag `_ripple_active` é setado `True` durante a reescrita automática de seções downstream
e impede que o ripple gere novos ripples (depth limit = 1, D9).

Na resposta do turno, se `_ripple_suggestion` estiver setado, inclui no retorno.

### Ripple (confirmação do usuário)

Quando o usuário confirma (próximo turno), o agente detecta a intenção + `_ripple_suggestion`
pendente. Durante a reescrita das seções downstream, `_ripple_active = True` para bloquear
novas classificações de escopo (D9 — depth limit = 1):

```python
# In _build_agent_initial_messages, if pending ripple:
if self._ripple_suggestion:
    self._ripple_active = True  # bloqueia re-classificação durante ripple
    messages.append({
        "role": "system",
        "content": (
            f"O usuário AUTORIZOU a atualização das seções impactadas pela correção "
            f"em '{self._ripple_suggestion['source_section']}'. "
            f"Reescreva agora: {', '.join(self._ripple_suggestion['affected_sections'])}."
        )
    })
    self._ripple_suggestion = None
```

---

## Arquivos afetados

| Arquivo | Mudança |
|---------|---------|
| `core/services/writing_session.py` | `turn()` detection, `_first_turn_with_generation()`, `_is_vague_description()`, `_project_description`, `_build_generation_section_messages()` injection, `_schedule_checklist_async()`, dependency matrices, `_ripple_suggestion`, `_ripple_active` |
| `core/llm/agent_tools/writing_tools.py` | `save_draft()` — invoke scope classifier after save, inject old_content; guarded by `_ripple_active` |
| `core/llm/agent_tools/scope_classifier.py` | **Novo** — classifier system prompt + `classify_correction_scope()` |
| `backend/routers/writing.py` | `WritingTurnResponse` schema — add `sections_done`, `failed_sections`, `compliance_status`, `ripple_suggestion` |
| `backend/routers/writing.py` | **Novo endpoint** `GET /writing/{session_id}/compliance` para polling de resultado do checklist |
| `core/tasks.py` ou similar | **Novo** background worker para executar `auto_review_checklist` em thread separada e publicar resultado |
| `frontend/src/...` | Handle compliance polling/SSE + ripple suggestion UI |

---

## Custo por sessão

| Operação | Modelo | Tokens | Custo |
|----------|--------|--------|-------|
| Gate de densidade | GPT-4o-mini | ~100t | ~$0.00005 |
| Batch 8 seções | Claude Sonnet 4 | ~24k total | ~$0.17 |
| Checklist 3 passes (background) | GPT-4o-mini | ~13.5k | ~$0.001 |
| Scope classifier (por save_draft humano) | GPT-4o-mini | ~1.2k | ~$0.0001 |
| **Total (primeiro turno, síncrono)** | | | **~$0.17** |
| **Total (compliance, assíncrono)** | | | **+$0.001** |
| **Total (correção + ripple, por ocorrência)** | | | **+$0.02-0.05** |

Ripple custa mais apenas se o usuário confirmar: cada seção downstream é uma geração similar ao
batch, mas só as impactadas.

---

## O que testar antes de ir a prod

### Parte A — First-Turn Generation

| Teste | Critério |
|-------|----------|
| **Qualidade do draft** | 8 seções geradas, todas com conteúdo real (não placeholders). Amostra de 10 editais reais |
| **Latência P95** | Geração + checklist < 90s. Se estourar, considerar SSE ou timeout maior no uvicorn |
| **Descrição vaga** | "Quero fazer um projeto de inovação" → gate de densidade rejeita, cai no modo conversacional. Agente faz perguntas para extrair escopo. **Não gera draft vazio nem inventa conteúdo** |
| **Gate de densidade: falso positivo** | Descrição válida (ex: "sistema IoT para monitoramento de equipamentos") classificada como vaga → geração abortada desnecessariamente. Usuário pode chamar `/writing/{id}/generate` manualmente |
| **Regeneração** | Chamar `/writing/turn` de novo (segundo turno) não regenera tudo — segue fluxo normal |
| **Checklist sem requisitos** | Edital sem `key_requirements` no KG → `build_checklist` retorna vazio → compliance = None, não quebra |

### Parte B — Correction Scope

| Teste | Critério |
|-------|----------|
| **Acurácia do classificador** | 30 amostras: 15 cosméticas, 15 conceituais. ≥85% de acerto |
| **Falso positivo (cosmética classificada como conceitual)** | Não gera ripple falso — apenas incomoda o usuário |
| **Falso negativo (conceitual classificada como cosmética)** | Seção downstream fica stale — mitigado pela matriz estática como fallback |
| **Cadeia de ripple** | Objeto muda → Metodologia reescrita (depth=1). Ripple NÃO re-classifica escopo — `_ripple_active` previne cascata. Cronograma e Orçamento são reescritos apenas se explicitamente listados como `affected_sections` |
| **Depth limit violado** | Ripple tenta reescrever seção que, por sua vez, dispara outro ripple → bloqueado por `_ripple_active` |
| **Ripple rejeitado** | Usuário diz "não" → sistema não reescreve, marca `_ripple_suggestion` como descartada |

---

## Aprovação

| Gate | Quem |
|------|------|
| Review de design | Eng |
| Shadow-run do classificador (30 amostras rotuladas) | Eng + Product |
| Teste de latência em staging | Eng |
| Aprovação de custo | Product |
