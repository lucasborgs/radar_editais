-- 015: aposenta o feature flag de escrita (Front 1 — spec robustez match+escrita)
--
-- O path legacy 1-shot de escrita foi removido (core/writing_session.py): turn()
-- sempre roda o agente com tools (search_edital, save_draft com critic, coerência
-- interna). Sem dois paths, o flag `agent_writing_enabled` não tem mais função —
-- todo workspace usa o agente.
--
-- `agent_explore_enabled` permanece: o agente de explore ainda está atrás de
-- flag (fora do escopo desta spec — ver questão aberta #2 do doc).
--
-- Irreversível por design. A remoção é gated pelo eval do Front 1.5
-- (scripts/eval_agent_writing.py) ter mostrado o agente ≥ baseline do legacy.

alter table public.workspaces
  drop column if exists agent_writing_enabled;
