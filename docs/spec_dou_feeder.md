# spec_dou_feeder.md — Feeder DOU/INLABS (Descoberta, Fase A)

> **Status:** módulo IMPLEMENTADO e validado ao vivo (2026-06-09) —
> [core/dou_feeder.py](../core/dou_feeder.py). Wiring no `discover_opportunities()`
> **pendente** (aditivo; §6). Credenciais em `.env` (`INLABS_EMAIL`/`INLABS_PASSWORD`),
> carregadas por `load_dotenv()` (já em `backend/api.py` e `core/tasks.py`).
> Persona: deep-tech early-stage. Ref. arquitetural: [spec_multi_quadrante.md](spec_multi_quadrante.md) §3.3.

---

## 1. O que é (e o que NÃO é)

O DOU é **torneira de descoberta**, não fonte/adapter. O feeder gera candidatos de
alta precisão a partir do Diário Oficial e os entrega como `SearchHit` ao
**mesmo** `discover_opportunities()` — paralelo ao Tavily. Daí pra frente é o
pipeline existente: triagem LLM → extração → bronze `web` → adapter `web` chunka.
**Nenhum adapter novo, nenhum bronze novo** (invariante da spec).

## 2. Fluxo real do INLABS (validado ao vivo)

```
POST https://inlabs.in.gov.br/logar.php   {email, password}  → cookie inlabs_session_cookie
   (handler dá 502 de manutenção transitório → retry; resto do site fica no ar)
GET  https://inlabs.in.gov.br/index.php?p=YYYY-MM-DD&dl=YYYY-MM-DD-DO3.zip   (com cookie)
   → zip com 1 XML por matéria (DO3 2026-06-09 = 2895 XMLs, ~5 MB)
```

**Schema do XML** (`<xml><article …><body>…</body></article>`):
- `article@artType` — tipo do ato ("Aviso de Chamamento Público", "Edital",
  "Extrato de Contrato"…). **Filtro primário.**
- `article@artCategory` — hierarquia do **órgão emissor** ("Ministério da Ciência,
  Tecnologia e Inovação/…"). **Agência de graça** + alavanca de precisão.
- `article@pubDate`, `@pdfPage` (link da página PDF = identidade), `@idMateria`.
- `body`: `Identifica` (título), `Ementa` (resumo), `Texto` (conteúdo completo,
  com HTML embutido).

## 3. Seções

`DEFAULT_SECTIONS = (DO3, DO1)`. **DO3** = avisos, chamamentos, editais (onde caem
a maioria das chamadas). **DO1** = atos normativos (algumas chamadas FINEP/MCTI).
**DO2** (pessoal) fora. DOU ordinário não sai sáb/dom (feeder retorna `[]`).

## 4. Filtro — recall-first determinístico + precisão na triagem

**Achado empírico (DO3 2026-06-09):** dos 2895 artigos, o filtro de fomento rende
~63 candidatos. Mas o DO3 é **dominado por ruído**: dos brutos pré-filtro, 55 eram
editais do **MEC** (acadêmicos/processo seletivo), 22 de **Prefeituras** (social/
saúde/merenda), e só **5 do MCTI** — o órgão que importa pra deep-tech. **Filtro
genérico de "fomento" no DOU é dominado por edital acadêmico/municipal irrelevante.**

Divisão de trabalho (mesma filosofia do resto do sistema):

| Camada | Faz | Onde |
|---|---|---|
| **Pré-filtro determinístico** (recall-first) | dropa licitação/resultado por `artType`+título; exige sinal de fomento; dropa órgão de compras | `dou_feeder._is_candidate` |
| **Triagem LLM** (precisão de persona) | "é fomento à INOVAÇÃO?" → mata merenda escolar, processo seletivo do MEC, etc. | `opportunity_discovery._triage` (já existe) |

Regras determinísticas (validadas):
- **DROP por `artType`:** contrato, licita, pregão, aditivo, homologa, adjudica,
  distrato, diploma, resultado, julgamento, processo seletivo, convocação,
  credenciamento, termo de fomento…
- **DROP por título** (`_IDENT_DROP_RE`): `extrato de (termo|contrato|fomento|…)`
  e `resultado de julgamento` — mata *resultados*; **preserva** "extrato de edital"
  (anúncio de chamada). Pega os que têm `artType` genérico "Extrato".
- **DROP por órgão:** topo de `artCategory` com "licita"/"compras".
- **KEEP:** `chamada|seleção pública|chamamento|subvenção|fomento|inovação|edital
  n|pesquisa e desenvolvimento|fundo|bolsa` em Identifica+Ementa+artType.

**Alavanca de custo/persona — `org_allowlist`:** parâmetro opcional que restringe
por órgão-topo (ex.: `("Ministério da Ciência", "FINEP", "Defesa")`). Default
`None` = **recall-first** (não perde cauda longa de FAPs, que aparecem sob governos
estaduais). Ligá-lo corta candidatos→triagem (custo de LLM) à custa de recall.

## 5. Mapeamento matéria → `SearchHit`

```
title   = Identifica
url      = pdfPage  (ou https://www.in.gov.br/web/dou/-/<idMateria>)   ← identidade
snippet = Ementa (ou início do Texto)
content = Texto (HTML stripado)   ← JÁ é o corpo completo; dispensa full-fetch
```

## 6. Wiring no `discover_opportunities()` (PENDENTE — aditivo)

Mudança mínima em [core/opportunity_discovery.py](../core/opportunity_discovery.py):

1. **Segundo gerador de candidatos**, ao lado do loop Tavily, atrás de flag:
   ```python
   if os.getenv("DISCOVERY_DOU_ENABLED", "0") == "1":
       from core.dou_feeder import dou_candidates
       for h in dou_candidates():
           nu = _norm_url(h.url)
           if nu and nu not in known and nu not in seen_now:
               seen_now.add(nu); candidates.append(h)
   ```
   Dedup reusa o **ledger** (`_known_urls`) + `_norm_url` — `pdfPage`/`idMateria`
   são URLs estáveis, então o mesmo edital não re-entra.

2. **`_page_text` já funciona sem branch:** ele devolve `hit.content` quando o
   full-fetch não traz mais texto. Como `pdfPage` é um visualizador JSP (full-fetch
   raso), cai no `hit.content` = o `Texto` completo do DOU. Opcional: marcar hits
   DOU pra pular o full-fetch e economizar 1 request/candidato.

**Por que atrás de flag:** liga em prod sem mexer no caminho Tavily; permite rodar
A/B (cobertura DOU vs Tavily) e desligar se o INLABS cair.

## 6.1 Papel das fontes: DOU é espinha, Tavily é gap-filler

Os dois geradores **não competem por "base"** — cobrem zonas diferentes. A
decisão de design:

| | DOU (INLABS) | Tavily (busca web) |
|---|---|---|
| Papel | **espinha de alta precisão** do fomento publicado federalmente (Q1 + Q2 regulatório) | **rede larga** pro que o DOU NÃO vê |
| Cobre | só o publicado no DOU **federal** | DOEs estaduais (FAPs!), Q3 (VC), Q4 (aceleradoras), anúncio só-no-site |
| Confiabilidade | canônica, estruturada, com órgão/data | cega, fuzzy, alto falso-positivo |

**Consequência 1 — o Tavily deve ENCOLHER de escopo** quando o DOU entra: parar
de varrer o federal (que o DOU entrega limpo) e mirar as zonas não-DOU. Manter os
dois no mesmo federal é desperdício + gera overlap.

**Consequência 2 — DOU vence o dedup.** Quando a MESMA oportunidade chega pelas
duas (edital FINEP no DOU *e* achado pelo Tavily na página da FINEP), prefere-se o
registro do DOU (canônico, estruturado). DOU-sourced pode nascer com
`verificacao` mais alta que `provisorio`.

**Furo conhecido (ver BACKLOG):** o dedup atual é por URL (`_norm_url`), mas a
mesma oportunidade tem URLs diferentes por fonte (`pdfPage` do DOU ≠ HTML da
agência) → dedup por URL **não pega o duplicado cross-fonte**. Mitigação imediata:
encolher o Tavily (consequência 1) faz o overlap quase sumir. Solução durável:
dedup semântico + prioridade de fonte (item de backlog).

## 6.2 DOU como stream de ciclo de vida (não só descoberta)

**Achado empírico (DO1+DO3 2026-06-09): 826 atos de ciclo de vida num dia.** O DOU
não anuncia só a abertura — anuncia o ciclo inteiro: **Retificação** (69, errata),
**Prorrogação** (9, prazo estendido), **Alteração** (~78), **Suspensão** (38),
**Revogação** (7, cancelada), **Republicação** (5), **Resultado/Homologação**
(70, encerramento). (Maioria é ciclo de licitação; o subconjunto de fomento é
pequeno mas existe.)

Logo o DOU é **stream de ciclo de vida por oportunidade**, não torneira de
descoberta. Um edital nasce → é retificado → prorrogado → encerrado, tudo na fonte
canônica. Três implicações:

| # | Implicação |
|---|---|
| ① | **`core/temporal.py` ganha fonte autoritativa.** Hoje status = "prazo < hoje → ENCERRADA". Mas **prorrogação muda o prazo** e **suspensão/revogação mudam o status independente do prazo** → o DOU é o SSOT real dessas transições, melhor que inferir do prazo |
| ② | **Exige IDENTIDADE estável de oportunidade** — e ela é NÃO-TRIVIAL (ver nota abaixo). Uma retificação tem que colar no nó do edital original; o `web:<url_hash>` muda por publicação e não cola. Mesmo problema de identidade do dedup cross-fonte (§6.1) |
| ③ | **"Alteração/prorrogação" NÃO é ruído absoluto.** É ruído só p/ oportunidade que você NÃO rastreia; p/ rastreada, "prazo prorrogado" é ouro. Handling condicional ao tracking |

**Ganho estratégico:** o DOU mantém os editais do radar **VIVOS** (prazos corretos,
encerrados marcados). Um radar com prazo errado é inútil → multiplicador de
robustez; casa com a ambição de memória longitudinal das agências.

**Identidade — Option B (`nº + órgão`) TESTADA E REPROVADA (dry-run 2026-06-09).**
A hipótese era nascer com id estável `<órgão>-<nº>-<ano>` em vez de `url_hash`. O
dry-run sobre o DOU real **reprovou**: o "Nº N/ANO" do DOU é escopado por
**unidade (UASG) + tipo de ato + ano**, NÃO por ministério — então
`<órgão-topo>-<nº>-<ano>` colide em massa (`ministerio-da-educacao-1-2026` fundiu
50+ atos distintos: termos aditivos de UASGs diferentes, editais, processo
seletivo). Pior que duplicar: **funde oportunidades não-relacionadas.** E a
ligação retificação→original no DOU mora no **texto do corpo** ("retifica-se a
publicação de DD/MM, pág. X"), não no metadado do título.

**Conclusão revertida:**
- **Descoberta/dedup:** `web:<url_hash>` está CORRETO (único por aviso, sem
  colisão) — o schema §3 estava certo. Mantém.
- **Identidade de ciclo de vida:** é um sub-problema REAL (identidade unit-level +
  parse de cross-referência no corpo), **não** uma decisão de `id_format` a cravar
  agora. Vai junto do sync de ciclo de vida (BACKLOG), não antes.

## 7. Custo e cadência

- 1 login + N downloads de zip/dia (DO3 ~5 MB). Parse local, barato.
- Triagem LLM roda nos ~dezenas de candidatos/dia (não nos 2895). Ligar
  `org_allowlist` deep-tech derruba pra ~handful.
- Cadência: 1×/dia (cron/procrastinate), após a publicação do DOU (manhã).

## 8. Próximos passos (tuning, pós-merge do wiring)

- Fechar `org_allowlist` de deep-tech (MCTI, FINEP, MDIC, Defesa, Comunicações…)
  como default de produção — decisão de produto/recall.
- Agendar o feeder (cron diário) junto da Descoberta.
- Medir precisão pós-triagem (quantos candidatos DOU viram oportunidade real) p/
  calibrar o pré-filtro vs custo.
- Avaliar varrer dias retroativos no primeiro run (backfill da janela vigente).
