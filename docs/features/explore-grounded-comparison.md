# Explore fundamentado — comparação/detalhe ancorado em trechos

**Fase:** feature · **Validação:** teste + eval (golden de explore) · **Esforço:** baixo-médio

> Habilita o modo **explore** (chat sobre o KG) a responder **comparações e
> detalhe fino** ancorados no **texto literal** dos editais, não no resumo
> sintetizado. Hoje o explore raciocina só sobre `index.json` + wiki cards
> (extração que preenche schema) — representação correta para *navegação/triagem*,
> mas **lossy para detalhe**: o escopo decisivo (exclusões, requisitos
> específicos) só aparece nos chunks, não no resumo. A recuperação de trecho cru
> ("KG localiza → RAG recupera") já existe no **writing**; esta spec leva o mesmo
> padrão ao explore, **agnóstico a fonte**.

---

## Problema

O explore tem dois substratos hoje, ambos sintetizados:

- `index.json` no prompt — campos extraídos (`objective`, `mechanism`, `trl_range`…).
- `get_edital` → wiki card — também resumo.

Nenhuma tool do explore toca o **trecho cru**. `retrieve_chunks` (busca híbrida
sobre `edital_chunks`) vive só no caminho de escrita
(`writing_tools.search_edital`). Resultado: uma pergunta de comparação fundamentada
no chat — *"compare a contrapartida exigida nestes 3 editais"* — é respondida a
partir do resumo, que omite o detalhe que decide.

Isto é fato verificado, não hipótese: a investigação do gate de grounding
(commit `4bb468a24`) registrou que "o escopo real (ex.: um edital exclui uma
cultura; outro exige um conjunto específico de implementos) **só aparece nos
chunks**, não no resumo do wiki, grosso demais".

## Estado atual

- `build_explore_tools` (`core/llm/agent_tools/explore_tools.py`) expõe tools de
  **navegação/atributo**: `list_editais`, `get_edital`, `find_analogues`,
  `get_graph_neighbors`, `find_ict_partners`, `list_icts`, `list_investidores`,
  `oportunidades_por_tema`.
- O loop é `run_agent(system=EXPLORE_AGENT_SYSTEM, tools=build_explore_tools(self)
  + build_planning_tools(...))` em `kg_match_service.py` (método `_explore_agent`).
- `retrieve_chunks(db, edital_ids, query, k, …)` (`core/retrieval/retriever.py`):
  busca híbrida (dense + FTS via RRF + rerank), **escopável a um conjunto de
  editais** (`edital_ids = ANY`). O parâmetro `db` é **ignorado** (`# noqa
  ARG001`); a função conecta sozinha via `_get_dsn()`.
- `format_chunks_for_prompt(chunks, edital_ids=…)` (`core/retrieval`) — formatter
  compartilhado, já usado pelo writing.

## Mudança proposta

Uma tool nova no explore: `search_edital_trechos`. Diferença conceitual vs.
writing — no writing o escopo é **estado de sessão** (primário + análogos,
resolvido 1×); no explore o agente **navega livre**, então o escopo vem como
**argumento**: o agente localiza os IDs (via `list_editais` /
`oportunidades_por_tema` / `get_graph_neighbors`) e os passa à tool. É o split
"KG localiza → RAG recupera" tornado explícito.

```python
# dentro de build_explore_tools(...)

from core.retrieval.retriever import retrieve_chunks
from core.retrieval import format_chunks_for_prompt   # mesmo formatter do writing

CHUNK_CHAR_CAP = int(os.getenv("EXPLORE_CHUNK_CHAR_CAP", "800"))     # por trecho
TOOL_CHAR_CAP  = int(os.getenv("EXPLORE_TRECHOS_CHAR_CAP", "6000"))  # total da tool-result
MAX_EDITAIS    = 5   # teto de editais/chamada (orçamento de contexto)

@tool
def search_edital_trechos(edital_ids: list[str], query: str, k_por_edital: int = 3) -> str:
    """Recupera TRECHOS LITERAIS dos editais para detalhe fino ou comparação fundamentada.

    Use SÓ quando a pergunta exige o texto real — ex.: "compare a contrapartida
    exigida nestes editais", "o que o edital X exige de TRL no detalhe". Para
    panorama/triagem, use list_editais / get_edital / oportunidades_por_tema:
    o resumo basta e é mais barato.

    Localize PRIMEIRO os edital_ids (list_editais, oportunidades_por_tema,
    get_graph_neighbors) e passe-os aqui.

    Args:
        edital_ids: IDs já localizados (máx 5). Ex.: ["<id_a>", "<id_b>"]
        query: o aspecto a detalhar/comparar, PT-BR. Frases curtas funcionam melhor.
        k_por_edital: trechos por edital (default 3, máx 5).
    """
    ids = [e for e in (edital_ids or []) if e][:MAX_EDITAIS]
    if not ids:
        return "Nenhum edital_id válido. Localize IDs com list_editais / oportunidades_por_tema antes."
    k = max(1, min(int(k_por_edital), 5))

    blocos: list[str] = []
    for eid in ids:
        try:
            # 1 edital por vez → cada um garante representação. Numa união
            # ranqueada (edital_ids=[a,b,c] numa só chamada), um edital pode
            # dominar o top-k e sufocar os outros — ruim p/ comparação.
            chunks = retrieve_chunks(None, [eid], query=query, k=k)
        except Exception as e:
            blocos.append(f"### {eid}\n(erro ao recuperar: {e})"); continue
        if not chunks:
            blocos.append(f"### {eid}\n(sem trecho relevante p/ a query)"); continue
        for c in chunks:
            if c.get("text"):
                c["text"] = c["text"][:CHUNK_CHAR_CAP]
        blocos.append(f"### {eid}\n" + format_chunks_for_prompt(chunks, edital_ids=[eid]))

    return "\n\n".join(blocos)[:TOOL_CHAR_CAP]
```

Registro: adicionar `search_edital_trechos` à lista retornada por
`build_explore_tools`. **Sem mudança no loop** — `run_agent` aceita qualquer tool.

### Adição ao `EXPLORE_AGENT_SYSTEM`

```
- Para DETALHE FINO ou COMPARAÇÃO entre editais, use search_edital_trechos e
  ancore no texto literal. NÃO responda detalhe/comparação a partir de get_edital
  ou do índice — são RESUMOS e omitem o escopo decisivo (exclusões, requisitos
  específicos). Para panorama/triagem/navegação, o resumo basta (mais barato).
- Ao comparar, rotule cada trecho com seu edital_id; nunca misture fontes sem rótulo.
```

## Contrato

| Campo | Valor |
|---|---|
| Nome | `search_edital_trechos` |
| Args | `edital_ids: list[str]` (máx `MAX_EDITAIS`), `query: str`, `k_por_edital: int` (1–5) |
| Retorno | string com blocos `### <edital_id>` + trechos formatados; degradação por-edital |
| Falha | erro-como-string (padrão das demais tools); nunca levanta |
| Estado | stateless; escopo vem do argumento, não da sessão |

## Reaproveitamento

| Peça | Origem | Status |
|---|---|---|
| `retrieve_chunks` | `core.retrieval.retriever` | reusa as-is (`db=None`, ignorado) |
| `format_chunks_for_prompt` | `core.retrieval` | reusa as-is (mesmo do writing) |
| degradação erro→string | padrão explore/writing | reusa o padrão |
| escopo por **argumento** | — | novo (writing usa estado de sessão) |
| recuperação **por edital** (loop) | — | novo (lógica de comparação balanceada) |
| caps + teto de editais | inspirado no writing | novo (explore soma N editais) |

## Agnosticismo de fonte (requisito de design)

**A tool não conhece fonte e não deve conhecer.** Ela recebe `edital_ids` e chama
`retrieve_chunks`, que opera sobre `edital_chunks` sem ramificar por fonte — o
chunker já é "agnóstico à fonte" (`chunker.py:2`), com `section` vindo de
`section_path` (sempre populado, fonte-agnóstico). Regras:

- **Proibido** qualquer `if source == "finep"` / branch por fonte na tool, no
  prompt ou no formatter.
- A tool funciona sobre **qualquer** edital que tenha chunks indexados. Para um
  edital sem chunks, retorna `(sem trecho relevante p/ a query)` — degradação
  graciosa, não erro.
- Consequência: quando uma nova fonte (web/torneira ou futura) passar a ter
  chunks em `edital_chunks`, a tool **funciona sem alteração de código**.

> **Estado de runtime ≠ restrição de design.** Hoje só algumas fontes têm chunks
> indexados; isso é um fato do dado, não da tool. A spec **não** deve ser
> implementada mirando fontes específicas. A qualidade de chunk de fontes
> HTML/web (estrutura ausente, ruído de navegação) é débito separado, fora do
> escopo desta spec.

## Caveats

1. **Orçamento de contexto** — N editais × k trechos cresce rápido; daí
   `CHUNK_CHAR_CAP`, `TOOL_CHAR_CAP`, `MAX_EDITAIS`. Calibrar com o log de
   disparo (mesma disciplina da spec 02).
2. **Qualidade do chunk por fonte** — recuperação fundamentada é tão boa quanto o
   chunk. Fontes com chunk de baixa fidelidade dão comparação mais fraca. É
   débito de ingestão, não desta tool (ver acima).
3. **Roteamento de uso** — o risco é o agente usar `search_edital_trechos` para
   triagem (caro) ou `get_edital` para detalhe (lossy). Mitigado pelo prompt;
   verificar no log de disparo qual tool o agente escolhe por tipo de pergunta.

## Validação

- **Teste unitário:** tool com `edital_ids` válidos retorna blocos rotulados; com
  lista vazia retorna a mensagem de orientação; com edital sem chunk retorna a
  degradação por-edital (não levanta).
- **Eval (golden de explore):** o gate de grounding hoje só cobre o writing. Esta
  feature introduz resposta fundamentada no explore → adicionar **1–2 casos de
  comparação** ao golden, ancorados em chunks reais (mesma lição da fixture do
  writing: ancorar no chunk, não no resumo). Amarra no item de BACKLOG "domar a
  variância do gate de grounding" — sem caso de eval, o explore regride sem sinal.
- **Manual:** uma pergunta de comparação cuja resposta correta depende de detalhe
  ausente do resumo (ex.: uma exclusão/requisito específico) — confirmar que a
  resposta cita o trecho, não o card.

## Fora de escopo

- Melhorar qualidade de chunk de fontes HTML/web (débito de ingestão separado).
- Fusão profunda grafo+chunks num pipeline único — aqui o agente apenas ganha
  mais uma tool; a orquestração segue sendo do `run_agent`.
- Mudanças no caminho de writing (já faz locator→evidência).
