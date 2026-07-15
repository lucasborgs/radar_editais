# Spec — Integração Crawl4AI na Descoberta

**Status:** vigente — rollout do coletor sob flag · **Data:** 2026-07-14

**Antecedente:** bake-off local de 2026-07-13, deliberadamente não versionado.

### Resultado do bake-off

A hipótese de substituir todos os adapters por um extrator universal foi
**reprovada pelos critérios definidos no próprio experimento**:

- 6/6 URLs completaram, mas nenhuma atingiu a classe `high`;
- 0/6 descrições chegaram ao piso de 500 caracteres;
- 5/6 preencheram ao menos três seções;
- a latência média do v2 foi 26,2 s/URL, acima do teto de 15 s; e
- houve corrupção factual em campo financeiro: na amostra FAPEMIG, valores como
  `R$ 500.000,00`/`15%` viraram `R$ 50.000.000,00`/`1.500%`.

A comparação com adapters dedicados também não tinha golden humano nem
persistia vereditos por campo; portanto, não sustentava remoção dos scrapers.
A decisão implementada abaixo é propositalmente mais estreita: Crawl4AI coleta
markdown e links de documentos como **evidência opcional**, sob flag e gate
humano, sem fazer a extração LLM/BM25 do experimento e sem substituir adapters.

## 1. Resultado

Fazer da coleta/enriquecimento uma camada reutilizável da Descoberta, sem criar
um pipeline paralelo depois da aprovação.

```text
adapter dedicado / busca web
  → evidências de origem
  → enriquecimento Crawl4AI opcional
  → pacote canônico de evidências
  → staging e decisão humana
  → materialização única no pipeline nativo
      → bronze → silver → gold/Radar
      → source docs → chunks → embeddings → RAG
```

## 2. Contrato canônico de evidências

Introduzir um objeto serializável `DiscoveryEvidencePackage`, persistido no
campo `raw` de staging enquanto estiver pendente e nunca exposto ao cliente
final. Ele contém:

- identidade: URL original e canônica, `url_hash`, fonte, data de coleta e
  versão do coletor;
- página: texto bruto limitado, conteúdo filtrado quando houver, hash e status
  de coleta;
- documentos: URL, rótulo, tipo inferido, motivo de seleção, status de
  download, hash/tamanho/páginas e texto limitado quando recuperado;
- campos extraídos: valor, evidência/origem (`adapter`, `page`, `document`) e
  confiança/estado de ausência; e
- operação: tempos, budgets aplicados, erros sanitizados e variante usada.

Conteúdo pendente pode existir no staging para revisão, mas não em bronze,
`source_docs`, gold, chunks ou embeddings antes da aprovação.

## 3. Precedência e composição

O pacote não escolhe um “vencedor global” entre adapter e crawler. Compõe fatos
por campo com regras determinísticas:

1. valor estruturado e validado pelo adapter dedicado é autoritativo;
2. valor extraído de regulamento/edital oficial selecionado complementa campos
   ausentes e pode alimentar evidência, mas não sobrescreve silenciosamente o
   adapter;
3. página Crawl4AI complementa campos ainda ausentes quando há trecho de
   origem; e
4. conflito fica explícito para o operador/curador; não é resolvido por score
   ou LLM implícito.

Cada campo materializado preserva sua proveniência. Um adapter futuro pode
passar a preencher mais campos sem alterar o contrato de staging ou promoção.

## 4. Quando usar Crawl4AI

- **Fonte genérica:** após triagem aprovada, coleta página, links e documentos
  com budgets; o segundo passe só roda se campos decisórios estiverem ausentes
  e houver evidência adicional selecionada.
- **Fonte dedicada:** o adapter roda como hoje; Crawl4AI é um enriquecimento
  opcional para JS, documentos ou lacunas, configurado por fonte e sem remover
  o adapter.
- **Fonte estratégica futura:** pode começar genérica e ganhar adapter sem
  mudar o restante do fluxo.

Todo uso ocorre em worker/job. Timeout, retry e estado seguem a operação de
descoberta; uma falha de enriquecimento não apaga evidência válida do adapter
nem publica conteúdo parcial.

## 5. Aprovação e materialização

Ao aprovar, o operador aprova uma versão congelada do pacote de evidências. A
promoção cria uma execução auditável e:

1. materializa o texto/página e documentos aprovados em bronze `web` com o
   mesmo `edital_id`/identidade canônica;
2. persiste os documentos no `source_docs` quando disponíveis;
3. dispara os jobs existentes de structurer/silver → gold/Radar e de
   `chunk_edital` → RAG; e
4. atualiza estados independentes de Radar e RAG, conforme a spec de operação
   da descoberta.

Se a coleta falhou ou não há evidência suficiente, o operador pode manter o
item pendente, corrigir link, rejeitar ou solicitar retry. A promoção não deve
voltar a buscar uma URL diferente da evidência aprovada sem registrar uma nova
versão do pacote.

## 6. Limites

- Não substitui adapters FINEP/FAPESP/FAPESC existentes.
- Não altera match v3, ontologia, elegibilidade, chunker ou modelo de embedding.
- Não torna Crawl4AI dependência obrigatória do backend; fica em extra/runtime
  do worker de descoberta até decisão de rollout.
- Não expõe pacote bruto, erros internos ou staging no Explorer/Radar público.

## 7. Critérios de aceite

1. Uma fonte genérica aprovada usa evidência congelada e chega ao mesmo bronze,
   gold/Radar e RAG de uma oportunidade nativa.
2. Um adapter dedicado preserva seus campos autoritativos quando enriquecido;
   documentos/página adicionam proveniência e lacunas, não sobrescrita oculta.
3. Documento oficial selecionado antes da aprovação é o mesmo materializado
   após ela, ou a divergência gera nova versão e revisão.
4. Timeout/falha do Crawl4AI permanece isolado, auditável e reprocessável, sem
   bloquear discovery, ETL ou publicação de outras fontes.
5. Nenhum conteúdo pendente aparece em catálogo, match ou RAG.
6. Testes cobrem composição por campo, budgets, versionamento, promoção,
   idempotência e os dois ramos de prontidão Radar/RAG.

## 8. Plano após aprovação

1. Definir o modelo/serialização do pacote e fixtures de fonte genérica e
   dedicada.
2. Implementar coletor Crawl4AI opcional no worker e a composição determinística.
3. Estender staging/migrations para versão, estado e referências de evidência.
4. Ligar a promoção ao pacote congelado e aos jobs nativos.
5. Integrar a observabilidade/retry da spec de operação da descoberta e executar
   evals de extração, RAG e matching antes de rollout por flag.

## 9. Implementação entregue

- `DiscoveryEvidencePackage` é serializado em `raw.evidence_package` para toda
  descoberta nova; a variante legada produz o mesmo contrato quando o extra
  não está instalado.
- `DISCOVERY_CRAWL4AI_ENABLED=1` habilita o enriquecimento apenas no worker da
  descoberta. O import é tardio, portanto Crawl4AI não entrou nas dependências
  padrão do backend. Falhas retornam à evidência legada e ficam registradas no
  pacote, sem publicar conteúdo pendente.
- A promoção materializa a versão aprovada em bronze `web` e grava a página e
  os PDFs selecionados como Documento Canônico em `edital_source_docs` quando
  há banco. Em seguida enfileira, sem modificar suas assinaturas, os jobs
  `ingest_promoted_edital` e `chunk_edital`.
- A migration 038, os endpoints administrativos de detalhe/retry e a tela
  `/discovered` separam a prontidão do Radar da prontidão do RAG. A coleta de
  uma URL pendente só vira bronze por um retry explícito que reutiliza o
  `WebScraper` existente.
- O rollout recomendado é manter a flag desligada, aplicar a migration, validar
  uma fonte genérica no staging e só então habilitá-la no worker de descoberta.
