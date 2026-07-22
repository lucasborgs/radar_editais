# RT01-T02 — Baseline de equivalência

**Objetivo:** congelar a projeção funcional atual antes do dual-write.

## Entrega

- comparador por chaves naturais, ignorando timestamps/IDs físicos;
- uma fixture disponível para FINEP, FAPESP, FAPESC, Web, EMBRAPII e catálogos
  de investidores/programas;
- snapshot de campos, relações e chunks atuais;
- lacunas de fixture registradas, não mascaradas.

## Validação

- teste hermético do comparador;
- execução local sem alterar banco remoto ou artefatos do dono.

## Pare

Pare se uma origem não puder ser reproduzida sem tocar dados protegidos ou se a
projeção revelar duas autoridades concorrentes.
