# Spec — ICTs Fase C (matchmaking + integração com escrita)

> **Objetivo:** transformar os nós ICT (Fase A) de catálogo em **matchmaking**: dado um edital que exige parceiro, sugerir ICTs candidatas por afinidade temática; e, quando o humano escolher um parceiro, deixá-lo fundamentar a escrita — sem nunca fabricar a parceria.
> **Base:** branch `ict-mapping` (Fase A: schema + EMBRAPII + `icts.json`, 86/90 ICTs com ponte). **Data:** 2026-06-03.
> **Pré-leitura:** [spec_ict_mapping.md](ict-mapping.md) (Fases A/B), [spec_deepresearch.md](deep-research-design.md) (gate de learning reaproveitado na peça 4).

## Decisões travadas

| # | Tópico | Decisão |
|---|--------|---------|
| 1 | Detecção de `requires_ict_partner` | **Heurística por regex** sobre o texto do edital (MVP). Patterns no schema (docs/domain/schema.md), não no código |
| 2 | Granularidade do match | **Macro-tema** (`edital.themes ∩ ict.themes`, §5.9). `areas_raw` fino fica para refino futuro |
| 3 | Exposição ao agente | Tool `find_ict_partners(edital_id)` **só no Explorador** (KGMatch). **Nunca no Redator** |
| 4 | ICT na escrita | **Só via decisão humana → ContentLibrary**, nunca via ponte temática automática (peça 4, fase posterior) |

## Princípio que atravessa a spec: sugestão ≠ compromisso

No match, "esta ICT *poderia* ser parceira" é recomendação — o humano avalia. Na
escrita, "nosso parceiro é X" é compromisso factual. Se o Redator puxasse uma ICT
por afinidade temática e a escrevesse na proposta, **fabricaria uma parceria** —
o que [spec_robustez](robustez-match-escrita.md) e o Grantable proíbem. Por
isso a ICT entra na escrita **apenas** pela porta da decisão humana (peça 4),
nunca pela porta da sugestão (peças 2/3).

---

## Peça 1 — Flag `requires_ict_partner` no edital

### Problema
O grafo não sabe quais editais **exigem** parceria com ICT. Sem isso, o match de
parceiros é ruído (sugere ICT para edital que não pede).

### Design
Campo booleano derivado no build do edital, via regex sobre o texto disponível.
Schema-first: os patterns vivem em docs/domain/schema.md (regra), o código só aplica.

```yaml
# docs/domain/schema.md §5.10 (novo)
ict_requirement_patterns:
  - "institui[çc][ãa]o de ci[êe]ncia e tecnologia"
  - "\\bICTs?\\b"
  - "parceria com (?:uma )?ICT"
  - "execu[çc][ãa]o em parceria com .{0,40}(universidade|instituto|ICT)"
  # ... refinar com falsos negativos observados
```

- **Fonte de texto** (ordem de preferência): texto integral chunkado
  (`edital_chunks`) > silver estruturado > `descricao`/`texto_cru` do bronze.
  Regex no bronze sozinho tem falso-negativo (exigência costuma estar no PDF) —
  documentar a limitação; é heurística MVP.
- Aplicado em pipeline/build_knowledge_graph.py
  (`_build_editais`/`_normalize_*`); grava `requires_ict_partner: bool` na entry
  do `index.json`. É **propriedade/tag**, não nó (§6.1.1).

### Arquivos
`docs/domain/schema.md` (§5.10 patterns + campo em §4), `core/wiki_schema.py` (helper
`ict_requirement_patterns()`), `pipeline/build_knowledge_graph.py`,
`tests/test_wiki_schema_consistency.py` (campo presente nas entries).

### Critérios de aceitação
- Toda entry do `index.json` tem `requires_ict_partner` (bool).
- Patterns vêm do doc; nenhum regex hard-coded no `.py`.
- Amostra manual: editais sabidamente de parceria marcados `true`.

---

## Peça 2 — Query de matchmaking (determinística, sem LLM)

### Problema
A ponte (`tema` compartilhado) existe (Fase A) mas ninguém a consulta.

### Design
Serviço novo `core/ict_match.py`:
```python
def find_partners(edital_id: str, k: int = 5) -> list[PartnerSuggestion]:
    # 1. carrega edital (index.json) e icts.json
    # 2. score = |edital.themes ∩ ict.themes|  (interseção de macro-tema)
    # 3. ordena desc, desempata por nº de areas_raw (proxy de abrangência)
    # 4. retorna top-k: {id, name, kind, themes_match, contact, url}
```
- `icts.json` carregado com cache por mtime (espelha `_load_index` do
  kg_match_service.py).
- Determinístico. Sem embeddings, sem LLM (MVP macro-tema).
- Se `edital.themes` vazio ou sem interseção → retorna `[]` com motivo (não força
  match ruim — coerente com o conservadorismo do normalizador da Fase A).

### Arquivos
`core/ict_match.py`, `tests/test_ict_match.py`.

### Critérios de aceitação
- `find_partners(edital_id)` retorna ranking estável por overlap de tema.
- Edital sem tema compartilhado → `[]` + motivo, nunca erro.

---

## Peça 3 — Tool `find_ict_partners` no Explorador

### Problema
O agente de match (KGMatch.explore) não consegue sugerir parceiros na tela
conversacional do grafo.

### Design
Tool em [core/agent_tools/explore_tools.py](../../core/llm/agent_tools/explore_tools.py),
espelhando `find_analogues`:
```python
@tool
def find_ict_partners(edital_id: str) -> str:
    """Sugere ICTs parceiras para um edital que exige parceria, por afinidade
    temática. Use quando o edital tem requires_ict_partner=true."""
```
- Chama `radar.core.ict_match.find_partners`, formata string com candidatas + por que
  (temas em comum) + contato.
- Exposta **só** ao Explorador (não ao Redator — peça 4 explica).
- Se o edital tem `requires_ict_partner=false`, a tool diz isso (não inventa
  necessidade).

### Arquivos
`core/agent_tools/explore_tools.py`, `core/kg_match_service.py` (registrar a tool
em `build_explore_tools`), teste de tool.

### Critérios de aceitação
- Explorador chama `find_ict_partners` e devolve candidatas com contato + temas.
- Tool ausente do tool set do Redator (grep).

---

## Peça 4 — ICT na escrita, via decisão humana → ContentLibrary (fase posterior)

### Problema
A proposta precisa nomear e descrever o parceiro escolhido. Mas o Redator não
pode escolher o parceiro — isso é compromisso, não sugestão.

### Design
Reaproveita o **gate de learning** do DeepResearch ([spec_deepresearch.md](deep-research-design.md) peça B):

```
Match/grafo (KG global)      Humano escolhe          Escrita (memória de projeto)
 find_ict_partners       →   "selecionar parceiro" →  ICT vira library_item
 (sugestão temática)         (a decisão)               search_library fundamenta a seção
```

- Na tela do grafo, ao listar parceiros, o usuário pode **importar** a ICT
  escolhida: `create_item(workspace_id, title=<ict.name>, type_='ict_partner',
  content=<about+areas_raw+contact>, source_url=<ict.url>, enrich=True)`
  ([content_library.py:166](../../core/services/content_library.py#L166)) — `enrich`/`embed`
  já existem (ADR M8).
- A partir daí, `search_library` ([writing_tools.py](../../core/llm/agent_tools/writing_tools.py))
  surfa o parceiro durante a escrita, com proveniência (`source_url`), como
  qualquer fato de projeto.
- **Guard-rail (o ponto da spec):** o Redator **não** recebe `find_ict_partners`
  nem lê `icts.json`. A ICT entra na escrita **só** se existir um `library_item`
  `ict_partner` — i.e., só se o humano decidiu. Sem decisão, não há parceiro na
  proposta.

### Arquivos
Backend: endpoint `POST /library/from-ict` (ou reuso de `POST /library` com
`type='ict_partner'`). Frontend: botão "selecionar parceiro" na tela do grafo.
`core/content_library.py` (tipo `ict_partner`, opcional half-life próprio como
`web_research`).

### Critérios de aceitação
- Importar parceiro cria `library_item` `ict_partner` enriquecido + embeddado.
- Redator usa o parceiro via `search_library`; cita `source_url`.
- Grep: Redator não acessa `icts.json` nem `find_ict_partners`.

---

## Faseamento

| Fase | Escopo | Gate |
|------|--------|------|
| **C.1** | Peças 1–3: flag regex + query + tool no Explorador | Explorador sugere parceiros para editais que exigem ICT |
| **C.2** | Peça 4: gate de decisão → library + uso na escrita | parceiro escolhido fundamenta a seção, sem o Redator escolher |

## Riscos

- **Falso-negativo do regex** (exigência só no PDF/anexo): rodar sobre o texto
  mais rico disponível (chunks/silver), não só bronze. Refinar patterns com
  observação. É heurística — não promete recall perfeito.
- **Match vazio por vocabulário forward-looking**: temas como `materiais,
  química e manufatura avançada` têm ICTs mas 0 editais hoje → 0 match até entrar
  edital do tema. Esperado e correto.
- **Tentação de dar `find_ict_partners` ao Redator** para "facilitar": é a
  regressão que a spec inteira existe para impedir. Manter o guard-rail.
