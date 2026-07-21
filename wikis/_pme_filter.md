# Filtro PME — regras determinísticas

Estende [WIKI.md](../WIKI.md). Preserva o vocabulário histórico que classificava
uma chamada como PME/startup (`accept`), puramente acadêmica (`reject`) ou
ambígua (`unclear`).

**Estado:** não há consumidor ativo nem gate implícito no ingest gold. O bloco
YAML permanece legível por `core.kg.schema.pme_filter_rules()` apenas por
compatibilidade de schema.

**Política de dados:** estas regras não escrevem nem removem bronze, silver ou
gold.

**Mudou regra? Edite este doc.** O leitor de compatibilidade está em
`core.kg.schema.pme_filter_rules()`.

---

## 1. Sinais

```yaml
target_relevance_rules:

  # 1.1 Programas-whitelist. Match case-insensitive de qualquer alias listado
  # contra os campos `programa`, `modalidade`, `titulo`, `categoria` do metadata
  # do edital (concatenados em uma string de busca). Match positivo → accept.
  programas_pme_canonicos:
    pipe:                 "PIPE — Pesquisa Inovativa em Pequena Empresa (FAPESP)"
    tecnova:              "Tecnova (FAPs estaduais)"
    # `inova` e `inovacao` soltos disparam falso-positivo em qualquer chamada
    # acadêmica que use 'Inovação' como buzzword (Centros de Pesquisa, Difusão,
    # Cooperação Internacional). Listamos só os aliases multi-palavra do
    # programa específico — match `\b{alias}\b` exige palavra completa.
    inova empresa:        "Inova Empresa (FINEP)"
    inova saude:          "Inova Saúde (FINEP)"
    inova rj:             "Inova RJ (FAPERJ)"
    inovadora:            "Empresa Inovadora (variante de Inova; word-boundary)"
    inovadoras:           "Programa para Inovadoras (variante; word-boundary)"
    auxilio a inovacao:   "Auxílio à Inovação (FAPESP)"
    inovacao regular:     "Inovação Regular (FAPESP)"
    rhae:                 "RHAE Pesquisador na Empresa (CNPq)"
    centelha:             "Programa Centelha (multi-FAP)"
    pappe:                "PAPPE — Programa de Apoio à Pesquisa em Empresas"
    sebraetec:            "SEBRAETec"
    funtec:               "BNDES Funtec"
    subvencao:            "Subvenção Econômica (FINEP)"
    startup:              "Programa Startup / Investimento em Startup (singular)"
    startups:             "Programa de Investimento em Startups (plural)"
    fip:                  "FIP — Fundo de Investimento em Participações (vehicle PME)"
    eureka:               "Rede Eureka (cooperação P&D internacional empresarial)"
    mover:                "Programa MOVER (Mobilidade Verde)"

  # 1.2 Públicos-whitelist. Match contra os valores canonicalizados do campo
  # `publico_alvo` do metadata (após normalização §5.5 do WIKI.md). Interseção
  # não-vazia → accept.
  publicos_pme_canonicos:
    - Empresas
    - Startups
    - Cooperativas

  # 1.3 Exclusores acadêmicos. Match case-insensitive de qualquer termo na
  # string de busca → vota reject. Termo deve ser específico de programa
  # acadêmico (evitar falso-positivo: "pesquisa" sozinho não exclui).
  exclusores_academicos:
    - "bolsa de mestrado"
    - "bolsa de doutorado"
    - "bolsa de iniciação científica"
    - "bolsa de pós-doutorado"
    - "iniciação científica"
    - "doutorado direto"
    - "auxílio à pesquisa regular"
    - "auxílio à pesquisa temático"
    - "projeto temático"
    - "auxílio à publicação"
    - "auxílio jovem pesquisador"
    - "jovem pesquisador"
    - "auxílio organização de reunião científica"
    - "organização de reunião científica"
    - "auxílio pesquisador visitante"
    - "pesquisador visitante"
    - "bepe"
    - "espca"
    - "escolas são paulo de ciência avançada"
    - "propasp"
```

---

## 2. Decisão

Algoritmo de `is_target_relevant(metadata) -> Literal["accept","reject","unclear"]`:

1. **Computa `search_text`** = lowercase + strip de acentos da concatenação de
   campos do metadata: `titulo`, `modalidade`, `programa`, `categoria`,
   `descricao_resumo` (os que existirem).

2. **Sinal de programa**: se algum alias de `programas_pme_canonicos.keys()`
   bate como palavra no `search_text` (regex `\b{alias}\b`, sem early-break) →
   **accept**.

3. **Sinal de público**: se `publico_alvo` (após canonicalização §5.5 do
   WIKI.md) tem interseção não-vazia com `publicos_pme_canonicos` → **accept**.

4. **Sinal de exclusor**: se algum termo de `exclusores_academicos` aparece
   como substring no `search_text` → **reject**.

5. **Ordem de precedência:** **accept vence reject**. Uma chamada PIPE que
   menciona "Bolsa de Pesquisa em Pequena Empresa" no texto passa por accept
   (sinal 2), mesmo que "bolsa" apareça — porque os sinais 2 e 3 disparam
   independentemente e accept tem precedência.

6. **Sem sinal nenhum** (nem accept, nem exclusor): **unclear**. O caller deve
   tratar o resultado explicitamente.

---

## 3. Campos do metadata esperados

A função opera sobre um dict — agnóstica à fonte. O scraper L0 produz e o
adapter L1 normaliza estes campos:

```yaml
filter_metadata_shape:
  titulo:            str       # obrigatório
  modalidade:        str | null
  programa:          str | null
  categoria:         str | null   # ex.: rótulo de seção/accordion na listagem
  descricao_resumo:  str | null   # primeiros parágrafos, se disponível
  publico_alvo:      list[str]    # após canonicalização §5.5 WIKI.md
```

Campos ausentes não são erro — só não contribuem com sinal.

---

## 4. Gotchas

- **PIPE Pequena Empresa** contém literalmente "bolsa" (de Bolsa PE), mas é
  PME-elegível. Resolução: sinal de programa (PIPE) dispara accept; precedência
  vence.
- **Auxílio à Inovação Regular** (FAPESP) parece "auxílio à pesquisa" mas é
  PME — por isso o alias `inovacao` está na whitelist e o exclusor
  `auxílio à pesquisa regular` é específico (não casa "auxílio à inovação").
- **Centelha** é tag/subprograma (§5.6) E sinal de programa-whitelist aqui —
  duplo papel é intencional. Chamada Centelha sai da FAP estadual com `programa`
  contendo "Centelha"; o filtro reconhece e o normalizador §5.6 também
  preserva como subprograma no grafo.
- **MOVER** análogo a Centelha.
- **Exclusores são substring-match**, não word-match. "doutorado" casa
  "doutorado direto" também — intencional.
