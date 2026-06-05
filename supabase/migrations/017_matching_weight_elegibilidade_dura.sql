-- 017 — peso global da dimensão `elegibilidade_dura` (HybridMatch Stage 1)
--
-- Nova dimensão CONDICIONAL do scoring determinístico: pontua os pares
-- organizacionais duros (região/idade/faturamento) quando o card declara
-- `eligibility_constraints`. Espelha o fallback `_WEIGHTS` em
-- core/hybrid_match_service.py. Dormente no catálogo atual (cards ainda sem o
-- campo) → não altera ranking até o extrator v2 popular o card.
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

-- 1. Estende o CHECK da coluna `dimension` para aceitar a nova dimensão.
--    O constraint original é auto-nomeado `matching_weights_dimension_check`.
alter table public.matching_weights
  drop constraint if exists matching_weights_dimension_check;

alter table public.matching_weights
  add constraint matching_weights_dimension_check
  check (dimension in (
    'elegibilidade', 'tematico', 'trl', 'mecanismo', 'contrapartida',
    'elegibilidade_dura'
  ));

-- 2. Seed do peso global default (workspace_id IS NULL).
insert into public.matching_weights (workspace_id, dimension, weight, source) values
  (null, 'elegibilidade_dura', 10, 'manual')
on conflict do nothing;
