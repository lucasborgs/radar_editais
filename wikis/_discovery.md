# Descoberta de Oportunidades — vocabulário de busca (item 2.2)

Config autoritativa do agente de descoberta (`core/opportunity_discovery.py`).
Queries que varrem a web atrás de editais/chamadas/desafios de fomento espalhados
pelo Brasil. **Regra vive aqui (doc), não no `.py`** — ajuste sem deploy.

Princípio: queries amplas o bastante para recall, específicas o bastante para não
afogar em ruído. Resultados passam por triagem agêntica + entram no KG como
`provisorio` (§5.11). Ver `docs/spec_descoberta_oportunidades.md`.

```yaml
discovery:
  # Queries de busca (Tavily). Tunáveis conforme a taxa de aprovação observada.
  queries:
    - "edital aberto inovação tecnológica empresas 2026 Brasil"
    - "chamada pública fomento à inovação PME"
    - "edital subvenção econômica pesquisa desenvolvimento"
    - "desafio de inovação aberto empresas inscrições"
    - "FAP estadual edital inovação chamada aberta"
    - "programa apoio pesquisa desenvolvimento inovação edital vigente"
  # Caps por execução (controle de custo do crawl diário).
  max_results_per_query: 8
  max_candidates: 40
```
