# RT06-T07-A — validação local, artifacts e preparação dos goldens

**Status:** Aguardando revisão humana dos goldens

**Escopo executado:** T07-A somente. Não houve promoção de família, remoção do
legado, acesso a produção, backfill nacional, commit, push ou deploy.

## Decisão reconciliada

As famílias são avaliadas e promovidas independentemente:

| Família | Campos | Resultado T07-A |
|---|---|---|
| `eligibility` | `eligibility_constraints`, `requirements`, `exclusions`, `eligible_entities`, `publico_alvo` | shadow; revisão pendente |
| `temporal` | `deadline`, `submission_window`, `continuous_flow` | shadow; revisão pendente |
| `financial` | `funding_amount`, `funding_limits`, `counterpart` | shadow; revisão pendente |
| `table_evidence` | `table_references` | shadow; revisão pendente |

O read model temporal continua calculando `active|closed|needs_review`; a
extração fornece fatos temporais, mas não decide validade. Referências de tabela
foram tratadas como evidência estruturada para Knowledge/Writing, sem pressupor
novas colunas gold. OCR/layout/visão continuam fora por não haver perda medida;
`channel` continua pendente.

## Persistência local

O Supabase foi confirmado em loopback (`127.0.0.1`, portas locais) antes da
escrita. As migrations 055 e 056 foram aplicadas localmente. A verificação de
schema encontrou:

- `public.document_extractions` presente, RLS habilitado e zero policies
  públicas;
- trigger append-only ativo para update/delete;
- `attempt_id` obrigatório;
- constraint unique `(fingerprint, attempt_id)`;
- índice parcial healthy por `fingerprint` para `complete|partial`, com
  `indisvalid=true` e `indisready=true`;
- artifacts de retry ficam como novas linhas; não há sobrescrita.

O [persistence-check.json](artifacts/t07-a-v1/persistence-check.json) demonstra
`complete`, `partial`, `failed` e `unavailable`, cache por fingerprint, retry
com attempts distintos, concorrência com duas chamadas e fingerprints distintos
após mudança de schema, produtor e targets. A execução foi somente no banco
local.

## Corpus e produtor

O corpus contém 4 sujeitos e 8 documentos:

| Sujeito | Documentos | Cobertura selecionada |
|---|---:|---|
| `finep:602` | 1 | PDF textual, status legado `aberta`, prazo e contrapartida |
| `finep:769` | 5 | PDFs múltiplos, duas rerratificações, valores, contrapartida e tabela |
| `fapesp:16466` | 1 | página HTML adquirida; “a qualquer momento” sem prazo FAPESP seguro |
| `web:3b554a9fcafc` | 1 | HTML adquirido com declaração literal de fluxo contínuo |

O corpus deste relatório foi executado com `adaptive_textual_extractor/text-v3`, em shadow, somente
com o silver local. Não foram executados DeepResearch, web discovery, OCR ou
visão. RT04 e RT05 não tiveram autoridade alterada; a projeção local devolveu
`needs_review` e “SourceBundle corrente indisponível; claims não publicados”
para os quatro sujeitos, portanto nenhum claim foi publicado.

Também foi executada uma tentativa de composição RT04 e projeção RT05 em
memória para `finep:769`, com bundle derivado do silver e override RT05
`mark_unknown`. O read model recusou a seleção porque os cinco artifacts estão
`partial` e não são artifacts promovidos/saudáveis; o resultado foi
`promoted=false`, `needs_review=true`, sem persistência de promoção. O registro
está em `artifacts/t07-a-v1/rt04-rt05-projection.json`.

Artifacts por documento, manifestos e fila humana estão no [pacote
T07-A](artifacts/t07-a-v1/README.md). O pacote não contém prompts nem respostas
brutas.

## Diagnóstico do shadow

Há 96 linhas de revisão (12 campos × 8 documentos), todas com decisão humana
`pending` e comentário vazio.

| Família | Linhas | `unknown` | `inferred` | Evidência resolvida |
|---|---:|---:|---:|---:|
| `eligibility` | 40 | 29 | 1 | 19 |
| `temporal` | 24 | 12 | 1 | 11 |
| `financial` | 24 | 21 | 0 | 4 |
| `table_evidence` | 8 | 7 | 0 | 1 |
| **Total** | **96** | **69** | **2** | **35** |

O produtor fez 8 chamadas na execução corrigida, registrando 177.609 tokens de
entrada e 9.086 de saída. A resposta do modelo continha 51 claims `stated`, 43
`absent` e 2 `inferred`; após validação, 35 claims ficaram com valor e
evidência resolvida. O custo monetário não foi calculado porque não há tabela
de preços no harness; chamadas e tokens estão em `summary.json`.

Não houve divergência nova resolvida entre documentos: a composição RT04 ficou
pendente e não foi usada para escolher autoridade. A comparação agora contém
21 valores legados recuperados por `source + native_id`; os candidatos novos e
as divergências entre rerratificações permanecem pendentes.

A primeira versão do pacote havia apagado candidatos durante a normalização e
também não regenerava o Markdown humano. O produtor foi corrigido para
preservar candidatos tipados em `unknown`/`inferred` sem torná-los publicáveis,
o prompt passou a declarar os formatos tipados e a exigir quote de bloco, e o
documento Markdown foi regenerado a partir do mesmo `review_rows.jsonl`.

As referências compreensíveis a tabela aparecem como exemplos pendentes. Não
houve `table_structure_lost` no corpus selecionado; isso é uma lacuna registrada,
não um motivo para ativar OCR/layout/visão.

## Harness e diagnósticos

Foi reutilizada a suíte existente `extraction` em 4 casos locais:

- presença: `0.8747`, abaixo do baseline `0.95` (`presence_regression=True`);
- correção de valor: `0.6375`;
- fidelidade de evidência: `1.0000`.

O resultado é diagnóstico/provisório e não foi transformado em aprovação. A
suíte `provenance` registrou locator exact/document-only e fidelidade verbatim;
`e2e_health` local passou com citações/coordenadas e sem erro operacional. Os
JSONs completos estão em `eval_results/` e não alteram autoridade.

## Revisão humana necessária

O usuário precisa revisar, por documento e campo, as 96 linhas de
`T07-A-human-review.md` (ou `review_rows.jsonl`), especialmente:

1. confirmar ou corrigir o valor de cada claim `unknown`/`inferred`;
2. confirmar os 35 candidatos com `stated` e evidência resolvida;
3. marcar `absent` somente quando o documento completo sustentar ausência
   legítima;
4. rejeitar candidatos `unknown`/`inferred` sem suporte suficiente como
   possível fabricação;
5. comparar as rerratificações de `finep:769` no RT04 e registrar conflitos;
6. confirmar prazo, janela e fluxo contínuo sem converter “a qualquer momento”
   em validade temporal;
7. validar valores/contrapartida e a referência de tabela para uso em
   Knowledge/Writing;
8. confirmar que nenhum caso selecionado exige OCR/layout/visão e manter
   `channel` pendente.

Os exemplos de valor correto, ausência legítima, unknown conservador, possível
fabricação, divergência, retificação e tabela estão em
`artifacts/t07-a-v1/review_examples.json`; todos permanecem não aprovados.

**T07-B não foi iniciada.**
