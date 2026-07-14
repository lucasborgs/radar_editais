# Spec — Radar Fase 2: urgência, filtros e comparação

**Status:** concluída · **Data:** 2026-07-14

**Documento-pai:** [radar-initiative.md](radar-initiative.md).

## 1. Resultado de produto

Depois de receber o conjunto pessoal de oportunidades, a pessoa consegue
reduzi-lo, entender o que exige ação mais cedo e comparar alternativas sem
confundir afinidade com probabilidade de aprovação.

```text
Radar pessoal
  → filtrar o que faz sentido agora
  → enxergar prazo e pendências de elegibilidade
  → comparar até três alternativas
  → abrir ficha ou iniciar proposta/pitch
```

## 2. Decisões propostas

| # | Decisão |
|---|---|
| D1 | A fase transforma somente o resultado já devolvido por `POST /radar/matches`; não altera `match_v3`, Stage 0/1, embeddings, corte mínimo ou a API. |
| D2 | A ordenação padrão continua `affinity` dentro de cada trilha. Urgência é um sinal visual e uma ordenação opcional apenas para editais; ela nunca altera o ranking-base nem mistura tipos. |
| D3 | Prazo é derivado exclusivamente do campo `prazo` existente. Sem prazo significa fluxo contínuo/desconhecido, não “urgente” nem “sem oportunidade”. |
| D4 | Filtros são locais e explícitos: tipo de trilha, setor, situação de elegibilidade e janela de prazo. Filtro reduz a lista exibida; não recalcula nem pede um novo match. |
| D5 | A comparação inicial é de até três editais. Programas e investidores continuam navegáveis pelos cards, mas não entram no comparador nesta fase porque seus campos decisórios são diferentes. |
| D6 | Comparar mostra fatos e evidências existentes (prazo, valor, setores, afinidade de escopo, elegibilidade e trechos), sem “vencedor” automático nem recomendação do LLM. |
| D7 | Seleção, filtros e ordenação vivem no cliente e são descartados ao sair/recarregar a página; não entram no perfil, workspace ou banco. |

## 3. Urgência de prazo

Aplicável apenas a editais com `prazo` no formato atual `dd/mm/yyyy`.

| Estado | Regra | Apresentação |
|---|---|---|
| Encerrando | prazo entre hoje e 7 dias | selo destacado com dias restantes |
| Em breve | prazo entre 8 e 30 dias | selo secundário com data e dias restantes |
| Futuro | prazo acima de 30 dias | data normal |
| Contínuo / sem prazo | `prazo = null` ou inválido | “Fluxo contínuo ou prazo não informado” |

- A data é interpretação de interface em fuso `America/Sao_Paulo`.
- Um prazo já vencido não deve surgir: o Stage 0 já decide a vivacidade. Se
  aparecer por dado inconsistente, exibir “prazo a confirmar”, sem escondê-lo
  nem inventar urgência.
- A ordenação “prazo mais próximo” coloca editais datados em ordem crescente e
  mantém fluxos contínuos/sem prazo ao final. Empates preservam `affinity`.

## 4. Filtros

### Controles

- **Trilhas:** Editais, Programas e Capital privado; todas ativas por padrão.
- **Setores:** chips gerados somente dos `setores` presentes no resultado. A
  seleção tem semântica OR entre setores e AND com os demais filtros.
- **Elegibilidade (editais):** Todos, confirmada e a confirmar. Itens
  `inelegivel` não existem no payload e nunca ganham filtro para reaparecer.
- **Prazo (editais):** Todos, encerrando em 7 dias, próximos 30 dias e fluxo
  contínuo/sem prazo.
- **Ordenação de editais:** Afinidade (padrão) ou prazo mais próximo.

O painel informa a contagem exibida e oferece “Limpar filtros”. Quando uma
trilha fica vazia por filtro, explica que o resultado completo continua
disponível ao limpar os filtros — não afirma que não há matches.

## 5. Comparador de editais

### Seleção

- Cada card de edital ganha uma ação secundária “Comparar”.
- No máximo três editais podem ficar selecionados. Ao atingir o limite, a UI
  explica que é preciso remover um antes de incluir outro.
- Uma barra fixa discreta mostra a seleção e abre o comparador; não bloqueia os
  CTAs de escrita nem a leitura dos cards.

### Painel

O painel/modal compara colunas por edital e linhas para:

- prazo e urgência calculada;
- valor/ticket quando disponível;
- setores;
- afinidade de escopo, descrita como evidência e não probabilidade;
- status e critérios de elegibilidade conhecidos ou pendentes;
- até um par de trechos que explica o match;
- link oficial/ficha quando existente; e
- ações de remover da comparação ou iniciar proposta.

Campos ausentes mostram “não informado”. Não há soma de scores, ranking novo,
seleção recomendada ou geração por LLM.

## 6. Arquitetura e limites

- Reutilizar `RadarMatchesResponse`, `MatchedEditalCard` e tipos existentes.
- Extrair helpers puros de prazo/filtro/ordenação para teste; não replicar a
  regra de Stage 0 no TypeScript.
- O endpoint continua sendo chamado uma vez por atualização do Radar. Mudar um
  filtro não cria embeddings, jobs, vereditos ou chamadas LLM.
- Vereditos permanecem secundários e não participam de filtro, urgência ou
  ordenação nesta fase.
- A experiência no Explorer não muda: os cards nele continuam compactos e sem
  painel de comparação; a comparação pertence ao `/radar` explícito.

## 7. Fora de escopo

- Novo ranking por urgência, score composto ou recomendação de “melhor edital”.
- Filtros que mudem o corpus ou chamem o backend com pesos distintos.
- Comparar investidores/programas com editais.
- Persistir favoritos, seleção ou alertas de prazo.
- Notificações, calendário e monitoramento automático de deadlines.
- Mudanças em descoberta, promoção, catálogo gold ou RAG.

## 8. Critérios de aceite

1. O resultado inicial do Radar preserva exatamente a ordem atual por
   `affinity` em cada trilha.
2. Cada edital datado exibe um estado de urgência correto em relação à data de
   teste; itens sem prazo não recebem urgência falsa.
3. Combinações de filtros exibem apenas itens compatíveis e não causam nova
   chamada de match.
4. Ordenar por prazo só muda a ordem dos editais e preserva empates por
   afinidade; programas e investidores não são reordenados.
5. O comparador aceita um a três editais, mostra apenas valores do payload e
   deixa explícitas informações ausentes/pendentes.
6. Nenhuma copy chama afinidade de chance de aprovação, e itens inelegíveis
   permanecem fora da interface.
7. `tsc`, testes dos helpers e QA manual de prazo, filtros, comparação, estado
   vazio por filtro e CTAs existentes passam.

## 9. Plano de implementação após aprovação

1. Criar helpers puros para normalizar prazo, urgência, filtros e ordenação,
   com testes determinísticos usando uma data injetável.
2. Adicionar a barra de controles e contagens ao `/radar`.
3. Adicionar seleção local e painel de comparação de editais.
4. Integrar urgência aos cards sem alterar seus dados ou CTAs.
5. Rodar TypeScript, testes focais e QA manual dos critérios de aceite.
