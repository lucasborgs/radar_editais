# 04 — Consolidar duplo planejamento do Redator (Finding D)

**Fase:** 2 (redator) · **Validação:** eval escrita + shadow · **Esforço:** baixo

## Problema

O Redator recebe **dois** mecanismos de planejamento que se sobrepõem, e o system
prompt manda usar um para alimentar o outro — desperdício de uma chamada LLM.

## Estado atual

- Toolset do Redator inclui ambos (`writing_tools.py:360-371`):
  - `plan_writing_session` (`writing_tools.py:171-239`): faz **chamada LLM real**
    (`OPENAI_MODEL`, default gpt-4o-mini, `max_tokens=600`); recebe estado das
    seções e retorna texto com ordem estratégica sugerida.
  - `write_todos` (`planning_tools.py:69-122`): puramente estado local (`PlanState`)
    + render com marcadores; sem LLM, sem persistência cross-turn.
- `WRITER_AGENT_SYSTEM` instrui ambos e diz explicitamente
  (`writing_session.py:160-163`): *"Se usar plan_writing_session, transcreva a
  estratégia para write_todos antes de executar."*
- `PITCH_WRITER_AGENT_SYSTEM` **não menciona** nenhum dos dois
  (`writing_session.py:210-220`) — duplo-planejamento só existe no modo proposta.

→ Custo: uma chamada LLM (`plan_writing_session`) cujo output o modelo precisa
copiar manualmente para `write_todos`. Dois afordances competindo pela atenção.

## Mudança proposta — duas opções

**Opção 1 (recomendada): remover `plan_writing_session`.**
- O modelo já raciocina sobre a ordem; `write_todos` captura o plano sem custo LLM.
- Remover a tool do toolset e a instrução de "transcreva" do `WRITER_AGENT_SYSTEM`;
  reescrever a orientação para "planeje com write_todos no início".
- Mais simples, uma chamada LLM a menos por sessão de planejamento.

**Opção 2: manter `plan_writing_session`, mas fazê-la popular `PlanState` direto.**
- A tool grava os todos ela mesma (retorna + seta estado), eliminando a
  transcrição manual. Mantém a "estratégia via LLM" mas remove o passo redundante.
- Útil se a ordenação estratégica via LLM agregar qualidade comprovada.

## Validação

- **Eval gate:** `python -m radar.core.eval writing` — comparar score entre baseline
  (atual), Opção 1 e Opção 2. **GATE:** não mergear se qualidade cair (ver
  precedente em `[[project-robustez-spec]]`).
- **Shadow:** logar nº de turns / chamadas LLM por sessão antes vs depois para
  quantificar a economia.

## Risco

Médio (qualidade) — por isso eval-gated. Se a Opção 1 derrubar a qualidade do
planejamento, cair para Opção 2.

## Pergunta em aberto

**Opção 1 vs 2** — decidir após o eval comparativo. Default: tentar 1 primeiro
(maior economia); 2 como fallback se eval reprovar.
