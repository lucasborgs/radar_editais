# RT04 — Relatório consolidado: pacotes documentais versionados

**Spec:** [`radar-data-trust-04-source-bundles.md`](../../../../specs/radar-data-trust-04-source-bundles.md)
**Status:** vigente · fechado em 2026-07-27
**Escopo:** histórico documental recuperável, composição conservadora e
linhagem factual; sem crawler universal, backfill, revisão humana, OCR ou API.

## Resultado

O Radar agora preserva versões materiais de documentos em uma única tabela
append-only, sem deslocar o Documento Canônico nem o pipeline gold. Cada
bundle completo pode ligar uma evidência ao pacote e ao documento que a
sustentou. Informação insuficiente continua explícita: bundles `partial` não
substituem a visão corrente, conflito não comprovadamente resolvível permanece
`conflicting` e registros legados continuam válidos sem linhagem nova.

## Entregas

| Task | Entrega | Commits funcionais relevantes |
|---|---|---|
| T01 | Contrato, hashes e fixtures Web/FAPESC/ator incompleto | `d883515d3`, `25e6168d7` |
| T02 | `source_bundles` append-only e idempotente (migration 044) | `fad984a09`, `88d0219f2` |
| T03 | Portal Web contextual + desafio promovido | `2157efd7e`, `a0b1f85fc` |
| T04 | Documentos normativos FAPESC | `0057c59b5`, `d9b55735e` |
| T05 | Bundles de ICT, investidores e programas já consumidos | `022162b59` |
| T06-A | Projeção corrente e precedência document-scoped | `e5da20abc` |
| T06-B | Linhagem `bundle_hash` + `content_hash` em evidências recuperáveis | `9cf4d428c`, `fccf8fc2c`, `22fdea2ff` |
| T07 | Métricas locais, reconciliação e fechamento | ver [`RT04-T07-report.md`](RT04-T07-report.md) |

## Contrato operacional consolidado

- `source_bundles` é a única persistência nova e é service-role-only,
  append-only e idempotente por `(subject_kind, subject_id, bundle_hash)`.
- A leitura corrente usa somente o último bundle `complete`; `partial` fica no
  histórico como diagnóstico e não substitui `edital_source_docs`.
- O bundle preserva páginas específicas, contexto, edital-base, anexos e
  retificações sem apagar versões anteriores.
- A composição só usa claims explícitos do mesmo campo. Documento superado sai
  da visão corrente; precedência não inferida deixa o valor `conflicting`.
- `EvidenceRef` carrega `bundle_hash` e `content_hash` juntos apenas quando o
  vínculo documental é recuperável e inequívoco. Payloads legados continuam
  válidos.

## Baseline de fixtures RT04

O baseline abaixo não mede cobertura produtiva; ele mede somente os cenários
versionados da spec: portal Web + desafio, FAPESC com retificação, ator
incompleto e uma versão `partial` posterior.

| Métrica | Resultado |
|---|---:|
| sujeitos com bundle | 3 |
| sujeitos com bundle corrente `complete` | 2 |
| versões do sujeito FAPESC | 2 |
| ator sem bundle completo | 1 |
| fatos críticos / composição no baseline | não observados (`null`) |

Métricas de linhagem factual contam `FactProvenance`, não referências: um fato
fica ligado quando ao menos uma de suas `EvidenceRef` contém o par recuperável
de hashes. Conflitos e precedência retornam `null` sem denominador fornecido;
nenhuma métrica converte ausência em zero. Não há threshold, gate, alerta ou
automação de decisão.

## Limitações e decisões futuras

- `match_chunks` permanece sem `bundle_hash`/`content_hash`, pois o schema não
  possui as colunas e RT04 não autoriza migration adicional.
- Chunks de escrita recebem linhagem somente quando a identidade do documento
  já chega inequívoca; não há heurística por nome, URL, data ou similaridade.
- Não existe marcador explícito de documento consolidado; RT04 não o infere.
- Investidores e programas curados não se tornam fontes oficiais por terem
  bundle; ator com conteúdo insuficiente continua visível como gap.
- Revisão humana de conflitos pertence à RT05; OCR, visão e extração adaptativa
  pertencem à RT06 e foram deliberadamente adiados.

## Validação

Os relatórios por task registram suas baterias herméticas. O fechamento T07
roda a suíte completa, a suíte diagnóstica `provenance`, Ruff e diff-check,
sempre com `ENVIRONMENT=test`, sem `.env`, rede, produção, LLM ou publicação.
