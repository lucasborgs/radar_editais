# Demo sintética P0

Perfil isolado: Aurora IA, startup de visão computacional sediada em MG. A
demo usa uma conta/workspace de pré-produção normal, com autenticação e RLS;
nunca habilita `DEMO_MODE` em produção.

Preparação: `python scripts/demo_smoke.py` e, no ambiente de pré-produção,
`python -m radar.core.kg.gold` seguido de `python scripts/backfill_chunks.py`
(se disponível). O smoke offline não precisa de LLM, busca externa ou dados
reais. Se embeddings/LLM estiverem indisponíveis, o roteiro demonstra o Radar
com cache aquecido e o Projeto já criado.

Roteiro de cinco minutos: login/perfil; Radar; três cards e suas evidências;
início do Projeto; checklist e salvamento do rascunho.
