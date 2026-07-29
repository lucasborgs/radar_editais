# RT05-T03 — Detector temporal em shadow

## Objetivo

Ligar o contrato T01 e o repositório T02 ao fluxo de oportunidade, abrindo ou
observando exceções temporais idempotentes. Mede/persiste a fila, mas não muda
match nem produto.

## Dependências

RT05-T01 e RT05-T02. A chamada deve ocorrer depois da composição/proveniência
da Spec 04, no ponto de publicação já existente.

## Arquivos prováveis

- `src/radar/core/services/temporal_quality.py` (novo);
- `src/radar/core/kg/gold.py`, `provenance_writer.py` ou ponto equivalente;
- `tests/unit/test_temporal_quality_detector.py` e testes de gold/proveniência
  afetados.

## Passos

1. Adaptar facts temporais, `FactProvenance` e bundle disponíveis para a função
   T01. Evidência ausente é condição, não autorização para fabricar citação.
2. Rodar o detector após a versão corrente ser conhecida e abrir/observar a
   exceção com fingerprint material e referências existentes.
3. Manter a gravação best-effort para o ETL: falha operacional só registra
   tipo/código e não interrompe bronze/silver/gold. Enquanto o rollout estiver
   em shadow, os consumidores preservam o comportamento legado; somente T07
   pode deixar de tratar o item como ativo.
4. Cobrir Finep/Eureka, futuro, fechado, contínuo comprovado, conflito e rerun
   idêntico. Não executar coleta ou reprocessamento produtivo.

## Invariantes

- Não altera documento, bundle, bronze, status editorial, promoção ou saída
  original do produtor.
- Não chama rede/LLM e não resolve a exceção automaticamente.
- Falha de storage é erro operacional do detector, não evidência factual nem
  decisão de validade.

## Testes mínimos

- Finep/Eureka abre uma exceção e rerun não duplica;
- fingerprint novo supersede; ausência de evidência mantém `needs_review`;
- storage falhando não derruba ingestão, não vaza erro bruto e não altera o
  comportamento legado enquanto shadow;
- testes unitários relevantes de gold/proveniência, `ruff check` e
  `git diff --check`.

## Critérios de aceite

- sinais temporais chegam à fila com subject/fingerprint e referências
  recuperáveis quando existirem;
- detector está em shadow: consumidores e estado ativo legado permanecem
  intocados até T07.

## Proibições

Sem enforcement em `stage0_alive`, API, frontend, revisão automática,
backfill integral, cron novo, OCR/visão, LLM, rede ou mudança editorial.

## Pare se

Não houver ponto posterior à proveniência/bundle, se Finep/Eureka só puder ser
registrado inventando evidência, ou se a integração exigir backfill integral.
