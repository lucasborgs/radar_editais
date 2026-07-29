# RT05-T06 — Interface administrativa de exceções

## Objetivo

Adicionar a aba **Exceções de dados** à área administrativa da Descoberta,
consumindo exclusivamente a API de T05. Não altera regra temporal, revisão,
promoção ou consumidores de produto.

## Dependências

RT05-T05.

## Arquivos prováveis

- `frontend/src/lib/api.ts`;
- `frontend/src/app/discovered/page.tsx`;
- teste frontend próximo ao padrão existente, se houver infraestrutura;
- relatório da task.

## Passos

1. Adicionar cliente tipado para lista, detalhe e criação de revisão, refletindo
   os payloads sanitizados de T05, sem tipos paralelos.
2. Criar aba/seção recolhível com filtros de abertas/resolvidas, código e fonte;
   exibir sujeito, campo, motivo, valor seguro, evidência/versionamento e
   impacto em linguagem clara.
3. Oferecer somente as quatro decisões da spec e campos requeridos. A UI não
   aceita `actor_id`, URL livre ou evidência textual avulsa.
4. Tratar carregamento, 403 e falha discretamente, sem bloquear promover ou
   rejeitar na fila existente.

## Invariantes

- A UI não calcula validade nem abre/resolve exceção localmente.
- Sem bulk action, comentários, notificação, atribuição, SLA ou edição.
- Informação interna só aparece após o gate administrativo existente.

## Testes mínimos

- Finep/Eureka, filtros e formulário de decisão válida;
- 403/erro não bloqueia Descoberta e `actor_id` não é enviado;
- `cd frontend && npx tsc --noEmit`, `cd frontend && npm run lint` e
  `git diff --check`.

## Critérios de aceite

- operador entende o motivo da revisão e só envia decisão válida;
- tela não acrescenta operação além da API T05.

## Proibições

Sem backend, migration, regra temporal, mudança de `promote/reject`, produto
público, LLM, rede ou workflow adicional.

## Pare se

O payload T05 não bastar sem expor conteúdo bruto, se o componente tiver de
reimplementar regra temporal ou alterar estado editorial.
