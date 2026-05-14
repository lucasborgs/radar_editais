# Wiki Feedback Loop — Design para o Passo 2

## Contexto

A arquitetura atual (Karpathy + NotebookLM) flui numa direção só:

```
Raw sources → wiki page → sessão de escrita
```

O passo 2 fecha o ciclo: insights gerados nas sessões de escrita retroalimentam
a wiki, tornando-a mais rica a cada interação.

```
Raw sources → wiki page → sessão de escrita → lições aprendidas → wiki (enriquecida)
```

Isso transforma o produto de "LLM com contexto" para "sistema que aprende com o uso".


## Por que isso é a proposta de valor central

A fonte primária (PDFs do edital) qualquer LLM acessa. O que diferencia é o
**contexto acumulado** — conhecimento que não está em nenhum documento, mas emerge
das interações ao longo do tempo:

- Quais argumentações têm maior taxa de aprovação em editais de determinado perfil
- Que a FINEP valoriza "impacto econômico mensurável" mais que "inovação incremental"
- Que editais com contrapartida > 30% raramente são aprovados por startups sem
  faturamento consolidado
- Padrões de linguagem que funcionaram em propostas aprovadas

Esse conhecimento não pode ser comprado — só pode ser acumulado com uso real.


## O que a wiki page precisa ganhar

Campo novo no schema: `lessons_learned`

```json
{
  "id": "782",
  "objective": "...",
  "proposal_sections": [...],
  "lessons_learned": [
    {
      "insight": "Editais desta linha valorizam parceria com ICT mesmo quando não obrigatória",
      "source": "session",
      "session_id": "uuid",
      "created_at": "2026-04-19",
      "confidence": "low"
    }
  ]
}
```

`confidence` evolui de `low` → `medium` → `high` conforme o insight é confirmado
por múltiplas sessões independentes.


## Quando o feedback é coletado

Três gatilhos possíveis, em ordem crescente de qualidade do sinal:

### Gatilho 1 — Encerramento da sessão (sinal fraco, automático)
Quando o usuário fecha a sessão ou clica "Proposta finalizada", uma LLM analisa
a conversa e extrai padrões:

```
conversa completa → LLM extrai → lista de insights candidatos
```

Custo: ~1 chamada LLM por sessão encerrada. Sinal fraco porque não sabemos
se a proposta foi boa.

### Gatilho 2 — Feedback explícito do usuário (sinal médio, ativo)
O usuário marca trechos da proposta como "funcionou bem" ou avalia a sessão.
Esses trechos alimentam a wiki com sinal mais qualificado.

### Gatilho 3 — Resultado da submissão (sinal forte, tardio)
Quando o usuário registra que a proposta foi aprovada/reprovada, os insights
da sessão recebem peso máximo. Requer o pipeline de submissões (P2 do roadmap).


## Como os insights são usados na WritingSession

O `WRITER_SYSTEM` recebe os `lessons_learned` da wiki page como contexto adicional:

```python
# Em _build_messages():
if wiki_lessons:
    messages.append({
        "role": "user",
        "content": f"LIÇÕES APRENDIDAS PARA ESTE EDITAL:\n{wiki_lessons}"
    })
```

Esse bloco fica entre o perfil da empresa e os documentos — parte do prefixo
estático, portanto cacheado.


## Arquitetura do extrator de insights

Novo módulo: `core/wiki_updater.py`

```python
def extract_session_insights(session: WritingSession) -> list[dict]:
    """
    Analisa a conversa da sessão e extrai insights sobre o edital.
    Chamado ao encerrar a sessão.
    """

def update_wiki_lessons(edital_id: str, insights: list[dict]) -> None:
    """
    Persiste os insights na wiki page, deduplicando e atualizando confidence.
    """

def promote_confidence(edital_id: str) -> None:
    """
    Promove insights de low → medium → high quando confirmados por
    múltiplas sessões independentes (threshold: 3 sessões).
    """
```

Novo endpoint no backend:

```
POST /writing/{session_id}/finalize
  → extrai insights da sessão
  → persiste na wiki page
  → retorna resumo dos insights adicionados
```


## Questões em aberto para o design

1. **Granularidade dos insights**: insights por edital específico vs. por tema/mecanismo?
   Um insight sobre "subvenção não reembolsável valoriza X" pode ser transversal
   a vários editais.

2. **Privacidade**: insights de um usuário devem ser visíveis para outros?
   Sim — é o que cria o efeito de rede. Mas requer anonimização.

3. **Curadoria**: insights contraditórios entre usuários. Quem desempata?
   O sistema de `confidence` resolve parcialmente, mas pode precisar de curadoria
   humana para insights de alta confiança.

4. **Wiki de temas e mecanismos**: além dos insights por edital, quando faz sentido
   criar páginas transversais (`wiki/temas/bioeconomia.md`)? Provavelmente quando
   um insight aparece em 3+ editais do mesmo tema.

5. **Cold start**: como tornar o sistema útil antes de ter dados de sessões?
   Seed manual com conhecimento do domínio (ex: melhores práticas FINEP documentadas
   publicamente).
