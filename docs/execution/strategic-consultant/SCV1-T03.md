# SCV1-T03 — Caminho normativo: subvenção pública não reembolsável

## 1. Objetivo e resultado perceptível

Entregar a primeira vertical de decisão substantiva. Dado um `ProjetoInovacao`, o consultor encontra financiamento público não reembolsável, explica regra, prazo, elegibilidade, valor/contrapartida quando houver, possível necessidade de ICT e evidencia cada afirmação. O usuário percebe o que está confirmado, o que é inferência, o que falta validar e qual ação vem antes da escrita.

## 2. Estado atual encontrado

- `entity_catalog` lê entidades gold e relações, `temporal_read_model` resolve validade e `provenance_read` expõe citações.
- `match_v3` executa Stage 0 temporal, Stage 1 de elegibilidade determinística, Stage 2 de afinidade por `company_chunks`/`match_chunks` e Stage 3 opcional de rerank/veredito.
- `eligibility.evaluate_opportunity` distingue `elegivel`, `nao_verificada` e `inelegivel`, mantendo unknown fora da eliminação silenciosa.
- `domain_paths` classifica edital/programa, mas ainda admite tipos fora do escopo ativo e retorna `caminho`/`explicacao` sem identidade, versão, decisão ou evidência localizada suficiente.
- ICTs já são buscadas por temas em `find_ict_partners`, com capacidades declaradas, porém sem um caminho ligado a requisitos do projeto.
- `/radar/matches` e seus cards são a superfície atual; vereditos são assíncronos e o fluxo de escrita começa clicando num edital.

## 3. Escopo funcional

- Implementar o adapter `Knowledge` para a consulta necessária à vertical: oportunidade ativa/contínua, regras, temporalidade, documentos/evidências, relações de operador/programa/ICT e entidades de capacidade.
- Implementar `Pathways.propose` normativo com recuperação de recall, filtros temporais e regras duras determinísticas, sem score universal e sem LLM decidindo inelegibilidade.
- Construir `CaminhoInovacao` rico: tipo `subvencao`, oportunidade, atores, fatos confirmados + `Evidencia`, inferências, requisitos, lacunas, riscos, confiança, validade e próximo passo.
- Representar ICT como parceiro/capacidade possível, distinguindo competência, equipamento, acesso e afinidade inferida; não prometer disponibilidade ou aprovação.
- Mostrar cards comparáveis dentro da jornada do consultor, não apenas no Radar. A ausência de prazo, conflito de fonte e data desconhecida devem aparecer como `needs_review`.
- Selecionar a subvenção como caso de smoke e permitir encaminhamento para uma escrita fundamentada, sem ainda implementar toda a escrita (T06).

## 4. Contratos introduzidos ou alterados

- `Knowledge.search/get/paths` passa a devolver `KnowledgeSignal` com entidade, papel, nível de conhecimento, validade, `evidence_refs` e motivo da recuperação.
- `CaminhoInovacao` ganha `opportunity_ref`, `actors`, `facts[]`, `inferences[]`, `requirements[]`, `gaps[]`, `risks[]`, `confidence`, `temporal_state`, `last_evaluated_at` e `next_step` tipado.
- `Pathways.propose(project, mode="normative")` devolve caminhos persistíveis e uma explicação por caminho; `select` só registra intenção, não submissão.
- `Evidencia` deve incluir documento, localizador preciso quando disponível, trecho e hash/versão da fonte. Link isolado não satisfaz regra crítica.
- O contrato de decisão determinística deve declarar `satisfied`, `unknown`, `unsatisfied` e a regra/evidência que sustenta cada resultado.

## 5. Módulos provavelmente afetados

- `src/radar/core/kg/entity_catalog.py`, `temporal_read_model.py`, provenance, `source_docs.py`, `source_bundles.py` e `constraints_producer.py`.
- `match_v3.py`, `eligibility.py`, `company_chunks.py`, `domain_paths.py`, novo adapter `Knowledge` e implementação `Pathways`.
- Tools de explore/match e `ConsultantGraph`; router/tipos de consultoria e cards frontend.
- Dados/testes representativos de edital FINEP/FAPESP/FAPESC e ICT; harness `matching`, `provenance`, `opportunity_type`, `e2e_health` quando aplicável.

## 6. Dependências

- `SCV1-T02` aceito e projeto confirmado.
- Casos gold com regras, prazo e pelo menos uma evidência útil; quando um campo faltar, o caso deve validar o comportamento unknown.
- Definição do escopo ativo: não reembolsável, apoio público, ICT/parceria; crédito, investidor e bolsa ficam excluídos.

## 7. Passos de implementação em ordem

1. Selecionar dois ou três casos normativos representativos e mapear suas afirmações críticas, evidências, temporalidade e regras rígidas/subjetivas.
2. Fechar o shape público de `KnowledgeSignal`, `Evidencia`, avaliação de regra e `CaminhoInovacao`; adicionar fixtures sem criar um novo vocabulário físico.
3. Encapsular `entity_catalog`, read model temporal, proveniência e `eligibility` atrás de `Knowledge`; o adapter deve filtrar tipos fora do escopo.
4. Implementar `Pathways.propose` normativo: recuperar candidatos, aplicar alive/temporal + regras duras, preservar unknown, anexar evidências e produzir lacunas/próximo passo.
5. Resolver ICT/capacidade como ator associado ao projeto e à oportunidade quando a fonte sustentar a relação; afinidade não vira fato.
6. Ligar o grafo, API e UI para propor/comparar o caminho, abrir detalhes de evidência e encaminhar o caminho selecionado.
7. Promover o caminho novo e retirar a entrega equivalente do Radar quando o smoke substituir o card efêmero.

## 8. Comportamento legado a remover

- Remover o Match v3 Stage 0–3 como contrato de produto e deixar suas partes úteis apenas dentro do adapter `Knowledge`/`Pathways`.
- Remover `caminho`/`explicacao` aninhados no payload `OpportunityMatch` como autoridade; o objeto persistente passa a ser único.
- Retirar classificação/recomendação de crédito e bolsa das listas, filtros, cards, tools e prompts ativos; `find_matching_investors` continua não sendo reativado.
- Retirar veredito LLM assíncrono/poll de `match_verdicts` da jornada normativa; interpretação subjetiva pode enriquecer inferências, mas regra dura e validade não dependem dele.
- Retirar o salto direto `/radar` → `startWritingSession(edital_id)`; o encaminhamento agora exige `CaminhoInovacao` selecionado.

## 9. Critérios de aceite verificáveis

- Para o caso normativo, o consultor mostra pelo menos um caminho com oportunidade, tipo subvenção, prazo/estado temporal, regra(s), evidências localizadas e próximo passo.
- Uma regra desconhecida aparece em `gaps`/`unknown`; não muda para inelegível e não é descrita como aprovada.
- Edital encerrado não aparece como ativo; validade ausente/conflitante aparece como revisão necessária.
- ICT aparece como parceiro possível com competência/acesso declarados separados de inferência.
- Cada recomendação permite abrir a base factual e distingue literalmente fatos, inferências, lacunas e orientação.
- Nenhum item de crédito, investidor ou bolsa acadêmica surge nas superfícies ativas.
- O projeto e o perfil usados no caminho são os mesmos do brief confirmado; a proposta sobrevive a reload.

## 10. Validação proporcional

- Testes focais de temporalidade, regras `satisfied/unknown/unsatisfied`, evidência, filtro de escopo e serialização do caminho.
- Smoke ponta a ponta de um edital normativo gold: conversa → projeto → proposta → caminho com lacuna → seleção.
- Rodar casos representativos das suítes `matching`, `provenance`, `opportunity_type` e `e2e_health`; limitar ao caso tocado quando o harness permitir.
- `ruff check`, `npx tsc --noEmit`, `git diff --check` e inspeção de que nenhuma rota ativa ainda usa o payload legado como autoridade.

## 11. Fora de escopo

- Extração adaptativa/OCR/visão, reificação completa de toda afirmação e correção nacional do catálogo.
- Decisão subjetiva autônoma de aprovação, aconselhamento jurídico ou submissão automática.
- Neo4j, Graph Builder, GraphRAG unificado, benchmark de backend e object storage.
- Escrita completa, caminho aberto e memória entre projetos.
