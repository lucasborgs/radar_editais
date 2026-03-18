# Radar de Editais — Decisões de System Design

Documento vivo. Cada decisão arquitetural relevante deve ser registrada aqui com contexto, motivação e trade-offs considerados.

---

## 1. Estratégia de Chunking para Vetorização

**Localização:** `pipeline/etl_gold_vectors.py`

### Contexto

O pipeline de vetorização opera sobre o arquivo `silver_data_enriched/editais_enriched.parquet`, que contém um registro por edital com dois campos de texto:

- `description` — texto bruto do edital (scraped + normalizado no ETL Silver)
- `search_document` — template semântico gerado pela LLM de enriquecimento no formato:
  `"Objetivo: X. Fonte: Y. Área: Z. Maturidade: TRL A-B. Mecanismo: C. Porte: D. Entidade: E. Uso: F."`

### Decisão: RecursiveCharacterTextSplitter uniforme para todas as fontes

| Parâmetro | Valor | Motivação |
|---|---|---|
| `chunk_size` | 500 chars | Cabe confortavelmente dentro do contexto de embedding do AlBERTina (512 tokens ≈ ~700 chars PT) sem truncamento |
| `chunk_overlap` | 100 chars (20%) | Preserva contexto entre chunks adjacentes; evita corte no meio de frases com informações relevantes (ex: prazos, valores) |
| `min_chunk_size` | 50 chars | Descarta fragmentos de navegação, cabeçalhos ou artefatos de scraping que não carregam semântica |
| Separators | `["\n\n", "\n", ". ", "; ", ", ", " ", ""]` | Prioriza quebra em parágrafos antes de frases, depois palavras — preserva unidades semânticas naturais |

Todas as fontes (FAPESP, FINEP, BNDES, EMBRAPII) usam a **mesma estratégia**. Não há chunking diferenciado por fonte porque o `search_document` — usado para geração de embeddings — já é um template normalizado de ~150 chars independente da fonte.

### Separação Embedding ↔ Documento (Harmonização Semântica Pré-Embedding)

**Princípio central:** o vetor que vai para o ChromaDB **não** é gerado a partir do `chunk_text` (texto bruto), mas sim do `search_document` do edital pai.

```
edital (row) → search_document → SentenceTransformer → vetor (ChromaDB)
               chunk_text      → stored as document    (contexto para o LLM)
```

**Motivação:** `chunk_text` contém ruído de scraping (HTML parcial, bullet points, formatações), enquanto o `search_document` é uma representação limpa e estruturada gerada pela LLM. Usar `search_document` para embedding garante que editais semanticamente similares fiquem próximos no espaço latente, independente de diferenças de formatação entre fontes.

**Consequência:** a busca semântica encontra editais relevantes via `search_document`, mas o LLM recebe `chunk_text` para gerar respostas ricas e contextualizadas.

### Estratégia de Segmentação para Corpus TSDAE (diferente do chunking de produção)

O corpus de pré-treinamento TSDAE (`pipeline/etl_finetune_tsdae.py`) usa segmentação **por palavras**, não por caracteres:

| Parâmetro | Valor |
|---|---|
| `MIN_WORDS` | 40 palavras |
| `MAX_WORDS` | 300 palavras |
| `TARGET_WORDS` | 150 palavras |

Esta granularidade maior é intencional: TSDAE aprende representações de nível de parágrafo/seção, capturando vocabulário de domínio em contexto completo. Segmentos pequenos demais não carregam jargão suficiente para o denoising ser eficaz.

---

## 2. Fine-Tuning — Estágio 1: TSDAE

**Localização:** `pipeline/etl_finetune_tsdae.py` | Modelo: `models/albertina_fomento_tsdae/`

### Objetivo

Adaptar o modelo base AlBERTina ao vocabulário de domínio de CT&I e fomento público brasileiro **sem labels**. O modelo aprende a reconstruir sentenças corrompidas (denoising), forçando-o a aprender representações ricas de termos como: subvenção econômica, TRL, matching EMBRAPII, CNPJ, fluxo contínuo, chamada pública, etc.

### Modelo base

`PORTULAN/albertina-900m-portuguese-ptbr-encoder-brwac` — AlBERTina 900M, pré-treinado em português do Brasil com o corpus BrWaC.

### Corpus

| Fonte | Tipo | Registros | Origem |
|---|---|---|---|
| FAPESP | Páginas HTML dos editais | 13.267 segmentos | `bronze_data/fapesp_pages/` |
| FINEP | PDFs de chamadas públicas | 9.060 segmentos | `bronze_data/finep_pdfs/` |
| **Total** | | **22.327 segmentos** | |

Split: 80% treino (17.862) / 20% validação (4.465). Seed: 42.

Estatísticas dos segmentos: média 131,9 palavras, range 40–730 palavras.

### Função de Perda

**TSDAE (Transformer-based Sequential Denoising Auto-Encoder)**

- Opera sobre sentenças individuais (sem pares ou labels)
- Corrompimento: deleção aleatória de tokens da sentença de entrada
- Objetivo: o decoder reconstrói a sentença original a partir da representação codificada corrompida
- Força o encoder a capturar semântica robusta e invariante a ruídos

### Por que FAPESP e FINEP (e não BNDES/EMBRAPII)?

FAPESP e FINEP produzem documentos longos e densos em jargão técnico-científico (PDFs de editais, páginas de chamadas), ideais para aprendizado não-supervisionado de vocabulário. BNDES e EMBRAPII têm textos mais curtos e estruturados — reservados para o estágio supervisionado onde o contexto completo de cada programa é usado como documento positivo.

---

## 3. Fine-Tuning — Estágio 2: TripletLoss Supervisionado

**Localização:** `finetune_tripletloss_colab.ipynb` | Modelo: `models/albertina_tripletloss/`

### Objetivo

Ajustar o espaço latente do modelo TSDAE para que **perguntas de gestores de inovação** e **descrições de programas/editais elegíveis** fiquem próximas no espaço vetorial, enquanto programas incompatíveis (hard negatives) ficam distantes.

### Modelo base

`models/albertina_fomento_tsdae/` — saída do Estágio 1. O TripletLoss parte de um modelo que já conhece o vocabulário de domínio.

### Corpus e Geração de Pares

**Script:** `pipeline/etl_generate_pairs.py`

| Fonte | Tipo de dado | Triplas geradas | Estratégia |
|---|---|---|---|
| FAPESP | Chunks de editais (corpus TSDAE, filtro ≥ 2023) | 5 triplas/chunk | Perfil de empresa fictício elegível → chunk do edital |
| FINEP | Chunks de editais (filtro ≥ 2018) | 5 triplas/chunk | Idem FAPESP |
| BNDES | Documentos completos de programas (68 segmentos) | 25 triplas/doc | Perguntas de gestor de inovação → documento completo |
| EMBRAPII | Documentos completos de unidades (100 unidades, exceto holdout) | 10 triplas/doc | Idem BNDES |

**Formato das triplas:**
```json
{
  "anchor": "Pergunta em 1ª pessoa: 'Quero financiamento para desenvolver...'",
  "positive": "Documento/chunk do programa compatível",
  "negative": "Documento/chunk de programa relacionado mas incompatível (hard negative)"
}
```

**Holdout EMBRAPII:** 13 unidades temáticas (farmacêutica, TICs, materiais, agro, mobilidade, energia...) excluídas do treino e salvas em `finetune_data/eval_pairs.jsonl` para avaliação de generalização.

### Função de Perda

**TripletLoss com hard negatives**

```
L = max(0, d(anchor, positive) - d(anchor, negative) + margin)
```

- `d(·, ·)` = distância cosseno no espaço latente
- Hard negatives: programas da mesma área temática mas com requisitos diferentes (ex: BNDES Inovação vs BNDES Crédito) — mais difíceis que negatives aleatórios, forçando fronteiras de decisão mais nítidas
- `margin` = 0.5 (padrão sentence-transformers)

### Arquivos de treino

| Arquivo | Tamanho | Conteúdo |
|---|---|---|
| `finetune_data/synthetic_training_pairs.jsonl` | 3,57 MB | Pares de treino (todas as fontes) |
| `finetune_data/supervised_corpus.jsonl` | 292 KB | Documentos completos BNDES + EMBRAPII |
| `finetune_data/eval_pairs.jsonl` | 136 KB | Holdout EMBRAPII (13 unidades) |
| `finetune_data/.generate_pairs_cache.json` | 7,2 MB | Cache MD5 para deduplicação |

---

## 4. Feature de Matching — Opção A: Scoring Determinístico

**Localização:** `core/matching_engine.py` | **Decisão em:** 2026-03-12

### Contexto e Opções Avaliadas

Três abordagens foram consideradas para calcular a compatibilidade entre o perfil de uma empresa e um edital:

| Opção | Mecanismo | Prós | Contras |
|---|---|---|---|
| **A — Determinístico** | Score por regras em 10 dimensões estruturadas | Instantâneo, explicável, sem inferência | Limitado a campos estruturados |
| B — Embedding | `CompanyProfile.to_context()` → vetor → cosine similarity com `search_document` | Captura semântica livre | Menos explicável, requer novo ciclo de fine-tuning com pares empresa↔edital |
| C — Híbrido (A + B) | Determinístico re-rankeia resultado do embedding | Melhor precisão | Mais complexo, latência maior |

### Decisão: Opção A para MVP

**Motivação:** necessidade de validar o produto rapidamente. O modelo estruturado do `CompanyProfile` (15 campos) captura os critérios de elegibilidade mais objetivos e já permite um ranking significativo para o usuário.

### Dimensões de Score (total: 100 pontos)

| Dimensão | Peso | Lógica |
|---|---|---|
| `theme_match` | 30 pts | Sobreposição de termos entre `descricao_atividades + portfolio_projetos` e `themes + keywords` do edital |
| `porte_match` | 15 pts | Porte da empresa dentro dos portes aceitos pelo edital |
| `trl_match` | 10 pts | TRL atual do projeto dentro do range TRL do mecanismo |
| `entity_type_match` | 10 pts | Tipo de entidade elegível (empresa, startup, ICT, universidade) |
| `location_match` | 10 pts | Localização da empresa compatível com restrição geográfica do edital |
| `capital_match` | 5 pts | Capital social acima do mínimo exigido |
| `certification_match` | 5 pts | Certificações da empresa atendem às exigidas |
| `uso_match` | 5 pts | Uso pretendido do recurso alinhado ao perfil da empresa |
| `tipo_financiamento_match` | 5 pts | Instrumento financeiro alinhado à preferência da empresa |
| `valor_match` | 5 pts | Valor buscado dentro da faixa do edital |

### Tiers de Recomendação

| Score | Classificação |
|---|---|
| ≥ 75 | `ALTA_ADERENCIA` |
| 50–74 | `MEDIA_ADERENCIA` |
| 30–49 | `BAIXA_ADERENCIA` |
| < 30 | `INCOMPATIVEL` |

### Caminho para Opção C (futuro)

Quando houver volume suficiente de dados de uso real (empresa preencheu perfil + selecionou editais), gerar pares de fine-tuning empresa↔edital e retreinar o AlBERTina para embeddding de perfis. O score determinístico pode então ser usado como re-ranking sobre os top-K resultados do embedding, combinando precisão semântica com explicabilidade por dimensão.

---

## 5. Feature de Writing — Captura de Informações para Redação de Proposta

**Localização:** `agents/writer_agent.py` | Endpoint: `POST /draft`

### Fluxo de captura de contexto

A geração de proposta conecta dois contextos independentes em um único prompt para a LLM:

```
Contexto A: CompanyProfile.to_context()
              → Nome, CNPJ, porte, localização, certificações, TRL
              → one_liner, problem_statement, solution_summary
              → descricao_atividades, portfolio_projetos, equipe_resumo
              → tipos_financiamento_interesse, uso_financiamento, valor_buscado

Contexto B: edital.description[:6000] + metadados
              → title, source, deadline_date, value_brl
              → texto completo da descrição (truncado em 6.000 chars para caber no contexto)

→ LLM (Ollama/OpenAI) → Proposta em Markdown (5 seções)
```

### Estrutura da proposta gerada

1. **Objeto** — O que será entregue, alinhado ao edital, conectado à experiência da empresa
2. **Justificativa** — Por que a empresa é a escolha ideal; evidências do portfólio
3. **Metodologia** — Fases, marcos, entregas, ferramentas; cronograma macro se prazo disponível
4. **Equipe Técnica** — Perfis com qualificações que atendem ao edital
5. **Resultados Esperados** — Deliverables concretos e mensuráveis; indicadores do edital

### Estilos disponíveis

| Estilo | Uso recomendado |
|---|---|
| `formal` | Licitações públicas, contratos governamentais |
| `consultivo` | Propostas de consultoria, parcerias empresariais |
| `academico` | Projetos FAPESP, CNPq, pesquisa colaborativa |

### Tratamento de informações ausentes

A LLM usa marcadores `[COMPLETAR: descrição do que inserir]` onde informações específicas não estão disponíveis no perfil ou no edital. O rascunho cobre ~80% do conteúdo final — o usuário completa os dados numéricos, cronogramas detalhados e referências específicas.

### LLM Backend

Configurável via variável de ambiente `LLM_BACKEND`:
- `ollama` (padrão) — modelo local `llama3.2` via `http://localhost:11434`
- `openai` — requer `OPENAI_API_KEY`, usa `gpt-4o-mini` por padrão

Temperature: 0.5 (equilíbrio entre criatividade e coerência técnica). `num_predict`: 4.000 tokens.

---

## 6. Feature de Writing — Sessão Conversacional (WritingSession)

**Localização:** `core/writing_session.py` | Endpoint: `POST /writing/start`, `POST /writing/turn` | **Decisão em:** 2026-03-18

### Contexto

O endpoint `/draft` gera uma proposta one-shot. Para casos mais complexos (editais longos, múltiplas seções, refinamento iterativo), é necessária uma sessão conversacional onde o usuário pode pedir seções específicas, ajustar tom, preencher lacunas e revisar iterativamente.

### Decisão: Long Context com gestão ativa de janela (não RAG por chunks)

Três abordagens foram avaliadas:

| Opção | Mecanismo | Prós | Contras |
|---|---|---|---|
| **A — Long Context com seções** | Router LLM seleciona seções relevantes por turno | Coerência entre turnos, custo controlado | Requer section index por documento |
| B — RAG por chunks | Embedding similarity por turno | Sem pré-processamento por documento | Perde coerência narrativa entre turnos; chunks semânticos não mapeiam bem seções de edital |
| C — Long Context puro | Documento inteiro no contexto de todo turno | Simples | Inviável em sessões longas (custo × tokens × turnos) |

**Decisão: Opção A**

### Fluxo por turno

```
user_message
  → Router LLM (modelo leve, temp=0, max_tokens=200)
      input: lista de títulos de seções disponíveis
      output: JSON array com seções relevantes ao pedido
  → SectionRetriever.get_sections(edital_id, selected_titles)
  → Writer LLM (temp=0.5, max_tokens=2000)
      Ordem das mensagens (do mais estático ao mais dinâmico):
        system : WRITER_SYSTEM_PROMPT
        user   : perfil da empresa (estático na sessão → cacheável)
        user   : seções do edital selecionadas pelo Router
        user   : resumo do histórico comprimido (quando aplicável)
        ...    : últimos HISTORY_WINDOW turnos verbatim
        user   : mensagem atual
  → response salvo no histórico
```

### Gerenciamento de histórico

| Parâmetro | Valor | Motivação |
|---|---|---|
| `HISTORY_WINDOW` | 6 turnos | Mantém contexto recente sem explodir o prompt |
| `COMPRESS_THRESHOLD` | 10 turnos | A partir daqui, comprime os mais antigos em resumo via LLM |

O perfil da empresa é **imutável na sessão** — candidato a prompt caching (Anthropic API).

### Sessões em memória

Sessões armazenadas em `dict[session_id, WritingSession]` no processo FastAPI. Aceitável para MVP. Em produção, substituir por Redis com TTL de 24h.

---

## 7. Section Index — Segmentação Estrutural por Documento

**Localização:** `pipeline/etl_section_index.py` | `core/section_retriever.py` | **Decisão em:** 2026-03-18

### Contexto

O `WritingSession` precisa servir seções específicas do edital ao Writer LLM sem carregar o documento inteiro a cada turno. O `section_index` é o artefato que viabiliza isso.

### Formato

`silver_data/section_index/{edital_id}.json`

```json
{
  "edital_id": "abc123def456",
  "source": "FAPESP",
  "title": "Chamada PIPE Fase 1",
  "sections": [
    {"title": "Objeto", "content": "..."},
    {"title": "Elegibilidade", "content": "..."},
    {"title": "Cronograma", "content": "..."}
  ]
}
```

### Estratégias de detecção (prioridade decrescente)

| Prioridade | Estratégia | Quando usado |
|---|---|---|
| 1 | `raw_html` → BeautifulSoup h2/h3/h4 | Quando scraper salvou HTML bruto (novos dados) |
| 2 | Split por `" \| "` | BNDES (etl_silver concatena seções com esse separador) |
| 3 | Campos do parquet (description + category + themes + location) | EMBRAPII |
| 4 | Regex seções numeradas (`\d+\.\s+[A-Z]`) + CAPS headers | FAPESP, FINEP |
| 5 | Parágrafos agrupados (≥200 chars) | Fallback universal |

### Preservação de `raw_html`

Os scrapers de FAPESP, FINEP e BNDES agora salvam o HTML bruto do elemento de conteúdo principal no campo `raw_html` do bronze JSON. O `etl_silver.py` passa esse campo para o parquet (coluna `raw_html: Optional[str]`). O `etl_section_index.py` prioriza BeautifulSoup sobre regex quando `raw_html` está disponível.

**Motivação:** `clean_html()` no ETL Silver transforma HTML em texto plano, destruindo marcadores estruturais (h2, h3, strong). Detecção regex em texto plano é imprecisa. Headers HTML são delimitadores confiáveis de seção.

**Backward compat:** parquets gerados antes dessa mudança não têm coluna `raw_html`. O `etl_section_index.py` detecta a ausência e injeta `None` antes do loop, caindo nas estratégias 2–5.

---

## 8. Live Fetcher — Conteúdo Completo Sob Demanda

**Localização:** `core/live_fetcher.py` | **Decisão em:** 2026-03-18

### Contexto e Problema

O campo `description` no parquet é derivado dos scrapers via `clean_html()`. Para alguns editais:
- HTML structure é destruída → seções difíceis de detectar
- Conteúdo pode ser subconjunto da página original (ex: FINEP extrai apenas `div.item_fields`)
- Regulamentos completos estão em PDFs linkados na página, não capturados pelo scraper

Consequência: o `section_index` gerado offline pode estar incompleto ou mal segmentado.

### Decisão: fetch ao vivo como fallback no início da sessão de escrita

**Alternativas descartadas:**

| Alternativa | Problema |
|---|---|
| Capturar HTML completo nos scrapers | Aumenta muito o tamanho do bronze/silver; a inteligência de extração migra para o ETL |
| Reprocessar todos os scrapers | Escopo muito maior; dados existentes continuam sem cobertura |
| Aceitar section_index incompleto | Prejudica qualidade da escrita de forma silenciosa |

### Fluxo

```
WritingSession.__init__(edital_id, profile, edital_url)
  → SectionRetriever.list_sections(edital_id)
  → se lista vazia E edital_url presente:
      LiveFetcher.fetch_and_save(edital_id, edital_url)
        → GET url → parse HTML → h2/h3/h4 → seções
        → se < 3 seções → _find_pdf_url() → PDF do regulamento
        → pdfplumber → seções numeradas do PDF
        → salva em section_index/{edital_id}.json
      → SectionRetriever.list_sections(edital_id)  [agora populado]
```

### Comportamento de cache

O resultado do live fetch é **persistido** no `section_index/`. Sessões subsequentes para o mesmo edital reutilizam o arquivo local sem novo fetch. A latência de 10–30s ocorre apenas uma vez por edital.

### Prioridade de conteúdo

PDF tem prioridade sobre HTML quando encontrado, pois regulamentos/editais em PDF são documentos normativos completos — mais ricos e estruturados que a página HTML de divulgação.

### `content_source` no response

`GET /writing/start` retorna `content_source: "section_index" | "live_fetch"` para que o frontend possa indicar ao usuário se o conteúdo veio do cache local ou foi buscado ao vivo.
