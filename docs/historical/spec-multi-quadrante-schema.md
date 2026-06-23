# spec_multi_quadrante_schema.md — Contrato fundacional (diff de schema)

> **Status:** diff PROPOSTO (2026-06-09). **Não aplicar ao vivo isolado** — cada
> bloco entra na PR da fase que o usa, junto do código que o consome, com
> `tests/test_wiki_schema_consistency.py` **verde** no mesmo commit. Aplicar o
> diff no WIKI.md sem o código que trata os `node_types` novos quebra o validador
> e o `build_knowledge_graph`. Este doc é a ponte entre
> [spec_multi_quadrante.md](spec_multi_quadrante.md) (arquitetura) e a 1ª PR.
> Honra as 6 invariantes de de-risk (§6-bis da spec): **aditivo, nunca rename**.

---

## 0. Os dois discriminadores (e a ortogonalidade que de-risca tudo)

A confusão a evitar: **`opportunity_type` ⊥ `source`/adapter.** São eixos
independentes.

| Eixo | Pergunta | Valores | Decide |
|---|---|---|---|
| **`kind_class`** | evento ou entidade? | `evento` \| `entidade` | o **caminho no pipeline** (temporal? gate? merge?) |
| **`opportunity_type`** | que tipo de oportunidade? | `edital` \| `desafio` \| `programa` \| `investidor` | **UI, triagem, modo de match/escrita** |
| **`source`** (já existe) | de onde vieram os bytes? | `finep` \| `fapesp` \| `web` \| … | qual **adapter** chunka (§12.4) |

Consequência prática: **um `desafio` da Petrobras numa página HTML usa o adapter
`web`** — `opportunity_type=desafio` é um *campo*, não um adapter. Por isso o
`source_adapters` **quase não muda** (§3 abaixo). Adicionar tipo de oportunidade
≠ adicionar fonte.

`kind_class` deriva de `opportunity_type`:
```
evento   = {edital, desafio, programa}
entidade = {investidor}
```

---

## 1. Diff — `WIKI.md` §6.1 `node_types`

Adiciona 4 tipos (`kind_class` anotado nos que carregam oportunidade; nós-ponte e
`ict`/`home` ficam sem). **Existentes inalterados** exceto a anotação `kind_class`
em `edital`.

```yaml
node_types:
  # ─── OPORTUNIDADE: EVENTO (têm prazo; fluem por core/temporal.py) ───
  edital:                       # (existe) + anotação kind_class
    folder: editais
    kind_class: evento          # NOVO (anotação; comportamento idêntico)
    tags: [finep, edital, "<status_tag>", "mecanismo/<mechanism>", "tema/<slug>", "setor/<slug>", "subprograma/<slug>", "trl/<faixa>", "ano/<pub_year>"]
    emoji: "<status_emoji>"
  desafio:                      # NOVO — Q2 (open innovation / obrigação regulatória)
    folder: desafios
    kind_class: evento
    tags: [desafio, "<status_tag>", "tema/<slug>", "setor/<slug>", "ancora/<slug>", "trl/<faixa>", "ano/<pub_year>"]
    emoji: "🎯"
  programa:                     # NOVO — Q4 (aceleração / incubação)
    folder: programas
    kind_class: evento
    tags: [programa, "<status_tag>", "tema/<slug>", "setor/<slug>", "modelo/<equity|no-equity>", "ano/<pub_year>"]
    emoji: "🚀"

  # ─── OPORTUNIDADE: ENTIDADE (persiste; NÃO flui por temporal; artefato próprio) ───
  investidor:                   # NOVO — Q3 (VC / anjo / corporate venture)
    folder: investidores
    kind_class: entidade
    artifact: "knowledge_graph/investidores.json"   # espelha icts.json
    tags: [investidor, "estagio/<slug>", "tese/<slug>", "setor/<slug>"]
    emoji: "💸"

  # ─── PONTE (sem kind_class; nós de navegação, não oportunidades) ───
  setor:                        # NOVO — ponte universal (cruza evento↔entidade)
    folder: setores
    tags: [setor]
    emoji: "🏭"
  tema: {folder: temas, ...}            # (existe, inalterado)
  publico: {folder: publicos, ...}      # (existe; event-side: gate)
  subprograma: {...}                    # (existe)
  fonte: {...}                          # (existe)
  ict: {...}                            # (existe; entidade-parceira, caso especial)
  home: {...}                           # (existe)
```

**Notas de modelagem:**
- `publico` **não** ganha aresta de `investidor` — no fundo, "público" se expressa
  como `estagio`+`setor` (decisão da spec §3.7). Evento usa `publico` (gate).
- `setor` (vertical de indústria: óleo-gás, saúde, agro) é **distinto** de `tema`
  (domínio tecnológico: biotec, IA). Para deep-tech costumam coexistir
  (tema=biotecnologia, setor=saúde).
- `<status_tag>`/`<status_emoji>` de `desafio`/`programa` reusam o vocab `status`
  existente (ABERTA/ENCERRADA) — são eventos.

---

## 2. Diff — `WIKI.md` §6.2 `link_types`

Generaliza os links de evento (aceitam os 3 tipos-evento) e adiciona os de `setor`
e `investidor`. **Backward-compat:** `from: edital` continua válido onde já existe;
a mudança é alargar para lista.

```yaml
link_types:
  # ─── generalização: os links de evento aceitam edital|desafio|programa ───
  opportunity_has_theme:        # era edital_has_theme
    from: [edital, desafio, programa]
    to: tema
    section: "## Temas"
  opportunity_has_target_audience:   # era edital_has_target_audience (event-side)
    from: [edital, desafio, programa]
    to: publico
    section: "## Público-Alvo"
  opportunity_has_fonte:        # era edital_has_fonte
    from: [edital, desafio, programa]
    to: fonte
    section: "## Fonte"
  # edital_has_subprograma: mantém-se específico de edital (FINEP)

  # ─── ponte nova: setor (universal evento↔entidade) ───
  opportunity_has_sector:       # NOVO
    from: [edital, desafio, programa, investidor]
    to: setor
    section: "## Setor"

  # ─── específicos novos ───
  desafio_posted_by:            # NOVO — empresa-âncora (decisão §8 #4: nó fonte vs campo, PENDENTE)
    from: desafio
    to: fonte
    section: "## Âncora"
  investidor_has_thesis_theme:  # NOVO — ponte de tese (Stage 2 GraphRAG)
    from: investidor
    to: tema
    section: "## Tese"

  # ─── RESERVADO (Camada B induzida, pós-MVP — ver BACKLOG + spec §3.9) ───
  # investidor_invests_alongside: {from: investidor, to: investidor, induced: true}
  #   → aresta de co-investimento, PROVISÓRIA até reconciliação. NÃO no MVP.
```

> **Custo de rename de link_type:** `edital_has_theme → opportunity_has_theme` toca
> `build_knowledge_graph` (emissão) e `_find_analogue_ids`/`resolve_scope` (que
> casam por `folder`, não pelo nome do link — ver [kg_match_service.py:534](../core/kg_match_service.py)).
> Verificar se o nome do link é load-bearing em algum parse antes de renomear; se
> for, **manter o nome `edital_has_*` e só alargar `from`** (menor risco).

---

## 3. Diff — `WIKI.md` §12.4 `source_adapters` (quase nada muda)

```yaml
source_adapters:
  finep:  {module: pipeline.adapters.finep, raw_dir: finep_raw, strategy: pdf}        # (existe)
  fapesp: {module: pipeline.adapters.fapesp, raw_dir: fapesp_raw, strategy: html_body} # (existe)
  web:    {module: pipeline.adapters.web, raw_dir: web_raw, strategy: html_clean}      # (existe)
  # SEM adapter novo. desafio/programa vêm via `web` (HTML de página de desafio/
  # programa → html_clean). DOU é FEEDER de descoberta, não fonte: produz
  # candidatos no web_raw (texto + agency rica do XML INLABS) e o adapter `web`
  # os chunka. investidor NÃO usa adapter — tem loader de diretório (§5).
```

**DOU não é adapter.** É torneira de descoberta (Fase A): o feeder INLABS gera
candidatos de alta precisão → `web_raw` → adapter `web`. O ganho do DOU é
*precisão de descoberta* + `agency` do XML, não extração melhor. (Se no futuro a
estrutura do XML justificar preservação, vira adapter `dou`/`xml_inlabs` — débito
consciente, fora do MVP.)

---

## 4. Diff — domain models (`domain/edital_extraction.py`)

**Invariante ②: NÃO renomear `EditalExtraction`.** Ela continua o schema de
EVENTO; ganha (a) o discriminador e (b) campos opcionais de desafio/programa
(default `absent`/None → extração de edital inalterada).

```python
# ── adições a EditalExtraction (todas opcionais; edital ignora as novas) ──
class EditalExtraction(BaseModel):
    source: str
    native_id: str
    opportunity_type: str = "edital"      # NOVO: "edital"|"desafio"|"programa"

    # DECISÃO/GATE (inalterado) — eligible_entities, themes, trl_range,
    #   mechanism, counterpart, requires_ict_partner
    # CONTEXTO (inalterado) — title, objective, key_requirements,
    #   funding_amount, project_duration_months, eligibility_constraints

    # ── NOVO: campos de DESAFIO (Q2), opcionais ──
    empresa_ancora: str | None = None          # quem traz a dor (vira nó fonte? §8#4)
    poc_scope: str | None = None               # escopo do piloto/PoC esperado

    # ── NOVO: campos de PROGRAMA (Q4), opcionais ──
    modelo_participacao: str | None = None     # "equity" | "no-equity"
    beneficios: list[str] = Field(default_factory=list)  # capital, mentoria, espaço

    # status/deadline continuam FORA (SSOT = core/temporal.py) — vale p/ os 3 eventos
```

`GATE_FIELDS`/`DECISION_FIELDS`/`CONTEXT_FIELDS` **inalterados** (os campos novos
são contexto, não gate). `CONTEXT_FIELDS` ganha `empresa_ancora`, `poc_scope`,
`modelo_participacao`, `beneficios`.

### 4.1 NOVO arquivo — `domain/investor_entity.py`

Entidade. **Não herda** `EditalExtraction` (forma diferente: sem evento, sem
gate). Reusa o padrão `Extracted[]`/`absent` para os campos de DECISÃO de tese.

```python
class TicketRange(BaseModel):          # espelha TrlRange/FundingAmount
    min_brl: float | None = None
    max_brl: float | None = None

class InvestorEntity(BaseModel):
    """Extração de um fundo/investidor (kind_class=entidade). SEM status/deadline,
    SEM gate. id_format: investidor:<slug>."""
    source: str = "investidor"
    native_id: str                      # slug, ex.: "kptl"
    name: str

    # ── TESE (alimenta o match de tese — Stage 2 GraphRAG, spec §3.8) ──
    tese: str | None = None             # texto da tese de investimento
    tese_themes: Extracted[list[str]] = Field(default_factory=absent)  # temas canônicos (ponte c/ edital.themes)
    setores: Extracted[list[str]] = Field(default_factory=absent)      # ponte setor
    estagio_alvo: Extracted[list[str]] = Field(default_factory=absent) # pre-seed|seed|serie-a (vocab §5)
    ticket_range: Extracted[TicketRange] = Field(default_factory=absent)
    lead_follow: str | None = None      # "lead"|"follow"|"ambos"

    # ── CONTEXTO (escrita do pitch + display + semente da Camada B) ──
    portfolio: list[str] = Field(default_factory=list)        # empresas investidas
    co_investidores: list[str] = Field(default_factory=list)  # syndication → semente induzida
    site: str | None = None
    contato: dict | None = None

# Sem GATE_FIELDS (entidade não elimina). Grupo de match:
THESIS_FIELDS = ("tese_themes", "setores", "estagio_alvo", "ticket_range")
```

### 4.2 Diff — `domain/user_profile.py` (`CompanyProfile`, +5 campos opcionais)

```python
# investor/challenge-facing — opcionais, não quebram match de edital
estagio: str = ""                  # pre-seed|seed|serie-a (≠ TRL)
mrr_arr: float | None = None       # tração financeira
round_alvo_brl: float | None = None # casa com investidor.ticket_range
cap_table_resumo: str = ""
tracao_resumo: str = ""            # clientes, pilotos, LOIs (≠ portfolio_projetos)
```
`to_context()` serializa os novos condicionalmente (padrão atual).
`is_complete()`/`completion_pct()` passam a ser **por quadrante** (perfil completo
p/ edital ≠ completo p/ investidor) — sinal de UX.

---

## 5. NOVO — `investidores.json` (espelha `icts.json`, WIKI.md §6.1.2)

```yaml
investidor_schema:
  artifact: "knowledge_graph/investidores.json"
  id_format: "investidor:<slug>"
  node_fields: [id, name, tese, tese_themes, setores, estagio_alvo, ticket_range, lead_follow, portfolio, co_investidores, site, contato]
  required_fields: [id, name, tese_themes, setores, estagio_alvo]
  notes:
    - "tese_themes: temas CANÔNICOS (mesma representação de edital.themes) — é a ponte investidor↔edital."
    - "Populado por CURADORIA MANUAL (~30-50 fundos, decisão §8 #3), não descoberta automática."
    - "co_investidores: semente da Camada B induzida (rede de fundos, BACKLOG) — fica inerte no MVP."
    - "investidores.json espelha icts.json: {investidores:[...], total, themes_index, last_updated}."
    - "NÃO entra no index.json (invariante ①) — radar junta na leitura (match/UI)."
```

---

## 6. Vocabulários novos (`WIKI.md` §5)

```yaml
setor_vocab:        # verticais de indústria (distinto de tema_vocab)
  [oleo-gas, energia, saude, agro, defesa, industria, financeiro, mobilidade, meio-ambiente, espacial, ...]
estagio_vocab:      # estágio de investimento (investidor.estagio_alvo + profile.estagio)
  [pre-seed, seed, serie-a]
modelo_vocab:       # programa.modelo_participacao
  [equity, no-equity]
```
(Listas a fechar com você antes de aplicar — vocab é decisão de produto.)

---

## 7. Impacto no validador (`tests/test_wiki_schema_consistency.py`)

O validador ganha o **eixo `kind_class`**. Asserções novas a adicionar:
1. Todo `node_type` com oportunidade tem `kind_class ∈ {evento, entidade}`; ponte
   e `ict`/`home` não têm.
2. `kind_class` derivável de `opportunity_type` bate com o mapa em código.
3. `investidor` tem `artifact` (como `ict`); eventos não.
4. `link_types.from` aceita lista; todo tipo em `from`/`to` é `node_type` válido.
5. Campos de `InvestorEntity` ⊇ `investidor_schema.node_fields`.

---

## 8. Ordem de aplicação (mantém aditivo + validador verde)

Cada bloco entra **na PR da sua fase**, nunca solto:

| Fase | Aplica deste doc | Não toca ainda |
|---|---|---|
| **A — DOU feeder** | nada de schema (DOU é feeder; reusa `web`) | tudo o resto |
| **B — desafio/programa** | §1 (desafio/programa+setor), §2, §4 (campos evento), §6, §7 | investidor |
| **C — investidor** | §1 (investidor), §2 (tese/setor), §4.1, §4.2, §5, §6 (estagio) | induzido (Camada B) |

**Pré-requisito de TODAS:** decisão §8 #4 (`empresa_ancora` nó vs campo) só
trava antes da Fase B; vocabs (§6) fecham com você antes de B/C.

---

## 9. O que ainda falta depois deste doc (specs de build por fase)

Este artefato fecha **schema + contratos de dado**. Cada fase ainda pede um spec
fino de *comportamento* antes da PR:
- **A:** parser XML INLABS + mapeamento matéria→candidato + filtro + plug no
  `discover_opportunities()` (bloqueado na conta INLABS).
- **B:** triagem per-`opportunity_type`; formatador de índice por tipo no match.
- **C:** prompt de match de tese (subgrafo montado, shape do score); `mode=pitch`
  na escrita; card de investidor + view de radar unificado (frontend); Layer 2
  merge/rank (ainda que ingênuo); golden de pitch p/ eval.
