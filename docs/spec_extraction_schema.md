# Schema de Extração Canônico — contrato extração-LLM ↔ scoring determinístico

> **Status:** Fase 1 (rascunho para validação). Artefato de avaliação, não de
> implementação. Derivado de `core/hybrid_match_service.py` (Stage 1 + Stage 2).

## Propósito

Definir **um schema-alvo único** que toda fonte (FINEP-PDF, FAPESP, web) deve
preencher. A extração (fonte bruta → schema) passa a ser responsabilidade de uma
**LLM com saída estruturada e abstenção**; o **scoring continua determinístico**,
rodando sobre a extração já normalizada e **congelada**. O schema é o contrato
entre as duas camadas.

```
LLM (extração, por-fonte)  →  SCHEMA canônico (frozen)  →  scoring determinístico
   messy, abstém quando não sabe      o contrato tipado        Stage 1 + combinação
```

## O problema que motiva (a cegueira, com números)

O Stage 1 **não tem gate duro**: cada campo ausente devolve `peso/2` (meio-crédito
"neutro") — e `counterpart_required` ausente devolve o **peso cheio**. Pesos:
`elegibilidade 30 · tematico 25 · trl 20 · mecanismo 15 · contrapartida 10` (=100),
threshold de eliminação = **25**.

Um card **totalmente vazio** (fonte web sem extração estruturada) pontua:

| dimensão | ausente → | pontos |
|----------|-----------|--------|
| elegibilidade | peso/2 | 15 |
| tematico | peso/2 | 12,5 |
| trl | peso/2 | 10 |
| mecanismo | peso/2 | 7,5 |
| contrapartida | **peso cheio** (ausência lida como "não exige") | 10 |
| **total** | | **55** → ≥ 25 → **ELEGÍVEL** |

Ou seja: **não extrair nada hoje vira elegibilidade automática com score mediano-alto.**
É exatamente o ruído que fontes heterogêneas vão injetar. O schema resolve isso ao
tornar "não consta" um **estado conhecido** que o scoring trata de propósito — em vez
de meio-crédito silencioso.

## Inventário de campos (o que o scoring consome hoje)

| campo | Stage 1 (determinístico) | Stage 2 (LLM) | escrita | default em ausência (HOJE) | classe |
|-------|--------------------------|---------------|---------|----------------------------|--------|
| `eligible_entities` / `publico_alvo` | elegibilidade (w=30) | — | — | meio-crédito (15) | **decisão** |
| `themes` / `eligible_sectors` | tematico (w=25) | fit temático | — | meio-crédito (12,5) + Stage 2 sem sinal | **decisão** |
| `trl_range` `{min,max}` | trl (w=20) | — | — | meio-crédito (10) | **decisão** |
| `mechanism` | mecanismo (w=15) | — | — | meio-crédito (7,5) | **decisão** |
| `counterpart_required` (bool) | contrapartida (w=10) | — | — | **crédito cheio (10)** ⚠️ | **decisão** |
| `status` / `deadline` | vigência (filtro temporal, `core/temporal.py`) | — | — | **único gate duro** (expirado é removido) | **decisão (temporal)** |
| `objective` | — | fit temático | resumo p/ RAG | `""` | contexto |
| `key_requirements` | — | fit temático | resumo p/ RAG | `[]` | contexto |
| `title` | — | fit temático | display | `""` | contexto |

**Decisão** = alimenta a elegibilidade/ranking determinístico → exige confiança alta
e abstenção explícita. **Contexto** = alimenta o fit semântico do Stage 2, a escrita
(que já tem RAG sobre chunks) e display → tolera ruído.

## Modelo de abstenção (proposta)

Cada campo **decisão** carrega estado, não só valor:

```jsonc
"trl_range": {
  "value": { "min": 4, "max": 6 },
  "state": "stated",          // stated | inferred | absent
  "evidence": "TRL 4 a 6 (item 3.2 do edital)"   // span/trecho da fonte; null se absent
}
```

- `stated` — explícito na fonte. `inferred` — deduzido pela LLM (confiança menor).
  `absent` — **não consta** (a LLM é instruída a abster, não inventar).
- Campos **contexto** podem ser `valor | null` simples (sem estado), pois não decidem.

## Mudança no scoring (decisões resolvidas — 2026-06-04)

Política de `absent` em campo decisão: **excluir do gate determinístico.** O campo
`absent` sai do cálculo do threshold — **fora do numerador E do denominador**. A
elegibilidade passa a ser a **proporção de pontos alcançáveis entre os campos
presentes**, não um teto absoluto:

```
eligible  ⇔  earned / achievable ≥ 0,25
  earned     = Σ score dos campos decisão PRESENTES (state ≠ absent)
  achievable = Σ peso dos campos decisão PRESENTES
  (threshold normalizado = 25/100 = 0,25, espelhando o corte atual)
```

Implicações:
- **Card vazio** (web mal-extraído): nenhum campo decisão presente → `achievable = 0`
  → **sem decisão determinística** → roteia para `provisorio`/HITL. Mata o smoking gun
  (não vira mais elegível-com-55 nem é filtrado em silêncio).
- **Exceção `counterpart_required`:** mantém o default de domínio (ausência lida como
  "não exige" → crédito cheio). **Decisão validada como intencional** — a maioria dos
  editais não exige contrapartida. Logo, NÃO entra na política de exclusão; está sempre
  "presente" no denominador.
- Campos sujeitos à exclusão-por-absent: `eligible_entities`/`publico_alvo`, `themes`/
  `eligible_sectors`, `trl_range`, `mechanism`.
- Borda: se TODOS os substantivos (`eligible_entities`, `themes`, `trl`, `mechanism`)
  forem `absent`, não há base temática/elegibilidade → `provisorio`/HITL (não decidir
  só pela contrapartida).

> ⚠️ Isso muda o comportamento do matching atual (FINEP/FAPESP também) → **mudança
> gated por eval**: rodar `python -m core.eval matching` antes/depois e só promover se
> a precisão@K segurar. A normalização exata (e o valor 0,25) é detalhe de Fase 3 a
> pinar com o eval.

## Decisões validadas (2026-06-04)

1. **Split decisão/contexto:** ✅ confirmado. `objective`/`key_requirements` são
   contexto (a escrita pega profundidade via RAG sobre chunks, não desses campos).
2. **`counterpart_required` ausente = crédito cheio:** ✅ **intencional, manter** (viés
   de domínio aceitável).
3. **Política de `absent`:** ✅ **excluir do gate** (normalizado; all-absent → provisório).

## Próximas fases (após validar este schema)

- **Fase 2:** golden rotulado (bootstrap + correção humana; FINEP+FAPESP+web) + suíte
  `extraction` no harness (`python -m core.eval extraction`): precisão/recall por campo
  + faithfulness (não inventar) + taxa de abstenção correta.
- **Fase 3:** extrator LLM (structured outputs + abstenção), 1×/edital, congelado.
- **Fase 4:** shadow vs normalizadores atuais; promoção gated por eval.
