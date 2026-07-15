# Memória — referência vigente

**Status:** referência vigente · **Verificado em:** 2026-07-14

O sistema não possui uma memória única. Cada store tem escopo, fonte de escrita
e regra de confiança próprios.

| Memória | Store | Escrita | Leitura |
|---|---|---|---|
| identidade | `workspaces.profile` | usuário ou extração aceita | Radar e escrita |
| curadoria | `content_items` | upload/promoção humana | retrieval da Content Library |
| sessão | `writing_sessions`, turnos e checkpointer LangGraph | interação do workspace | retomada da escrita |
| RAG documental | `edital_chunks` | ingest lazy de documento canônico | escrita e tools de retrieval |
| exploração | `exploration_log` | tools do ExploreAgent | sessões futuras do mesmo workspace |
| síntese | `reflection_insights` + PostgresStore | reflexão gateada | contexto e busca semântica |
| outcomes | `application_log` + eventos | ação/estado informado | reflexão e histórico operacional |

## Estado da escrita automática

Leitura de memória curada permanece ativa. A escrita automática de reflexão e
síntese fica desligada por padrão com `AUTO_MEMORY_WRITE=0` e possui
short-circuits em `core/reflection_service.py` e `core/tasks.py`. Isso é uma
decisão de segurança entre workspaces, não ausência acidental de wiring.

O seam `playbook_overlays`/`meta_reflection_runs` não possui job de escrita
ativo. As tabelas e o reader vazio são preservados como capacidade dormente de
laboratório; seu gate está no
[`ciclo de vida das capacidades`](capability-lifecycle.md).

## Isolamento e falha

- dados de cliente usam `workspace_id` ou namespace equivalente;
- checkpointer e Store usam o schema dedicado `agent_memory` e acesso backend;
- ausência de `DATABASE_URL` desliga Store/checkpointer conforme o caminho,
  sem transformar memória em requisito para leitura do catálogo; e
- capacidades de síntese falham isoladamente do fluxo principal.

Arquitetura geral: [`architecture.md`](../architecture.md). O inventário de
junho, hoje obsoleto, está em
[`memory-architecture-2026-06.md`](../historical/memory-architecture-2026-06.md).
