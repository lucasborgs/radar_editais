# RT00-T01 — Contrato de domínio

**Objetivo:** criar tipos versionados para decisão, reason codes e evidência de
relevância, sem alterar produção.

## Escopo

- `in_scope | out_of_scope | needs_review`;
- contratos separados por `kind`;
- oportunidade ≠ investidor/ICT/programa/agência;
- compatibilidade com a saída atual de triagem.

## Entrega

- tipos puros no domínio;
- serialização estável e versão do classificador;
- testes unitários de estados inválidos e round-trip.

## Validação

- testes do novo módulo e testes atuais de triagem;
- ruff nos arquivos alterados.

## Pare

Pergunte ao proprietário se um caso exigir nova inclusão/exclusão de produto ou
se `needs_review` não resolver a ambiguidade.
