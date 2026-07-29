# RT05-T02 — Persistência mínima e repositório da fila

## Objetivo

Criar as duas tabelas administrativas e o repositório de exceções/revisões,
sem detector, rota ou alteração da projeção de oportunidade.

## Dependências

RT05-T01. Confirmar a próxima numeração de migration na execução.

## Arquivos prováveis

- `supabase/migrations/NNN_data_quality_exceptions.sql` (novo);
- `src/radar/core/services/data_quality_exceptions.py` (novo);
- `tests/unit/test_data_quality_exceptions.py` e teste estrutural de migration
  local/fake (novos).

## Passos

1. Criar apenas `data_quality_exceptions` e `data_quality_reviews`, com RLS
   sem leitura de usuário final. A primeira guarda chave lógica, estado,
   referências/hashes, fingerprint, timestamps e status; a segunda guarda
   decisão append-only e vínculo à exceção.
2. Garantir idempotência por `(subject_kind, subject_id, field_path,
   issue_code, input_fingerprint)`. Recoleta igual atualiza só observação;
   fingerprint novo supersede a anterior, sem apagar revisões.
3. Implementar métodos pequenos: abrir/observar, listar/detalhar, registrar
   revisão e ler projeção. Ausência é `None`; falha real é erro de domínio
   sanitizável, não `False` ambíguo.
4. Validar tamanho curto de justificativa, IDs/códigos e referências
   serializáveis. Não gravar documento, URL arbitrária ou erro bruto.

## Invariantes

- Só service role escreve; não há backfill de legados.
- Revisão nunca é atualizada/removida. `actor_id` virá da camada autenticada,
  nunca de payload público.
- A fila não substitui `discovered_opportunities` nem altera seu RLS,
  promoção ou rejeição.

## Testes mínimos

- schema/RLS/colunas e unicidade; reexecução;
- mesma entrada, nova fingerprint, revisão append-only e erro sanitizado;
- `ENVIRONMENT=test pytest -q tests/unit/test_data_quality_exceptions.py`,
  migration local/fake, `ruff check` e `git diff --check`.

## Critérios de aceite

- há exatamente duas tabelas novas;
- histórico e revisão sobrevivem a versão material nova;
- dados legados seguem legíveis sem exceção fabricada.

## Proibições

Sem detector, `gold`, match, API, frontend, worker, migration remota, rede,
LLM, alerta, cache ou índice especulativo.

## Pare se

A chave idempotente não couber no schema, RLS existente precisar mudar ou a
persistência exigir documento/payload bruto.
