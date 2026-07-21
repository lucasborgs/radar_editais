# Specs — Auditoria agêntica (2026-06-13)

Specs de implementação dos findings da auditoria da arquitetura agêntica
(`core/llm/agent_runtime.py`, `agent_tools/`, serviços que rodam agentes) +
candidatos a evolução **code-routed → model-routed**.

Origem: auditoria conversacional 2026-06-13. Cada spec é independente e
implementável isolada, salvo dependências anotadas.

## Barra de validação (vale para todas)

- **Plumbing** (correção/perf): testes unitários/integração.
- **Comportamento/qualidade** (toca output): **eval gate** via `python -m radar.core.eval <suíte>` antes de merge.
- **Roteamento** (decide "rodar ou não X"): **shadow/dry-run** comparando decisão nova vs antiga, sem afetar usuário; só promove se não perde sinal.

## Índice

| # | Fase | Item | Spec | Validação | Esforço |
|---|------|------|------|-----------|---------|
| A | 0 · trivial | Registro público incompleto (`__all__`) | 00 | teste | trivial |
| B+C | 1 · plumbing | Tools em paralelo + suporte async | 01 | teste + eval | médio-alto |
| G | 1 · plumbing | Orçamento de contexto nas tool-results | 02 | teste + eval | baixo-médio |
| E | 1 · plumbing | Camada web única + cache | 03 | teste + eval extração | médio |
| D | 2 · redator | Consolidar duplo planejamento | 04 | eval escrita + shadow | baixo |
| F/#2 | 2 · redator | Skills model-routed (`load_skill` tool) | 05 | eval escrita + shadow | médio |
| #1 | 3 · model-routed | Triage dos passes do Checklist | 06 | testes (novos) + shadow | médio |
| #4 | 3 · model-routed | Descoberta: cache negativo + observabilidade | 07 | dry-run | baixo-médio |
| #5 | 3 · model-routed | `reflect_every` dinâmico | 08 | eval escrita | baixo |
| #3 | foresight | Carregamento dinâmico de tools | 09 | — (adiado) | — |

## Ordem sugerida de implementação

1. **00** (trivial, destrava consistência do registro)
2. **03** (camada web) → habilita cache e desacopla, base p/ outros
3. **02** (orçamento de contexto) → independente, ganho imediato
4. **01** (paralelo+async) → maior mudança no loop; depois que 02/03 estabilizam
5. **04** + **05** (redator) → eval-gated juntos
6. **06** + **07** + **08** (model-routed) → cada um com seu shadow/dry-run
7. **09** quando algum agente passar de ~20 tools (gatilho documentado)
