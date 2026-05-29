-- 013: feature flags por workspace para rollout gradual do agent runtime
--
-- Cenário B da decisão de arquitetura: agentificar WritingSession e KGMatch.explore
-- com harness próprio (core/agent_runtime.py). Para fazer rollout sem risco,
-- cada workspace tem flags binárias controlando se os endpoints servem o
-- agente novo ou o pipeline determinístico antigo.
--
-- Default OFF: nenhum workspace é migrado automaticamente. O ramp 10% → 50% →
-- 100% acontece via UPDATE direcionado (ou via UI admin futura).
--
-- Quando o ramp atingir 100% E ficar estável por N dias, podemos remover as
-- colunas e o código antigo. Por enquanto coexistem.

alter table public.workspaces
  add column if not exists agent_writing_enabled boolean not null default false,
  add column if not exists agent_explore_enabled boolean not null default false;

comment on column public.workspaces.agent_writing_enabled is
  'Quando true, /writing/turn usa o agente harness (core/agent_runtime) com tools. '
  'Quando false, mantém o pipeline determinístico (RAG fixo + 1 LLM call) atual.';

comment on column public.workspaces.agent_explore_enabled is
  'Quando true, /explore usa o agente harness com tools sobre o catálogo. '
  'Quando false, mantém o catálogo inteiro injetado no prompt.';
