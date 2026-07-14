# Iniciativa — Radar como porta de entrada

**Status:** ativa · **Data:** 2026-07-13  
**Função deste documento:** mapa de produto e dependências. Não autoriza
implementação sozinho; cada entrega só começa com sua spec filha aprovada.

---

## Resultado pretendido

Fazer do Radar a entrada do Radar de Editais: uma pessoa pode explorar uma
ideia, formar um perfil mínimo, entender oportunidades compatíveis e avançar
para uma proposta ou pitch com evidência e limites claros.

```text
Explorar uma ideia/projeto
  → formar perfil ou hipótese
  → Radar pessoal e explicável
  → validar oportunidade e elegibilidade
  → proposta, pitch ou próxima ação
```

## Portfólio de entregas

| Ordem | Entrega | Spec | Estado | Dependências | Critério de conclusão |
|---:|---|---|---|---|---|
| 1 | Radar explícito e explicável | [radar-frontdoor.md](radar-frontdoor.md) | concluída — 2026-07-14 | match v3, perfil, auth opcional, cards existentes | rota `/radar`, contrato determinístico e jornadas anônima/autenticada aprovadas |
| 2 | Urgência, filtros e comparação | `radar-fase-2.md` | não iniciada | entrega 1 e contrato estável de cards | usuário filtra, prioriza por prazo e compara oportunidades sem reinterpretar ranking |
| 3 | Operação da descoberta | `discovery-operations.md` | não iniciada | staging, tasks de promoção, ingest gold e RAG | promoção observável por etapa, reprocessável e auditável |
| 4 | Explorer de projetos | `explorer-project-canvas.md` | não iniciada | decisão de modelo de dados/memória, entrega 1 | hipótese de projeto evolui para perfil/Radar por ação explícita do usuário |

## Regras de dependência

1. A entrega 1 não muda o ranking, a elegibilidade, embeddings, ingestão ou
   esquema de banco.
2. A entrega 2 consome o contrato de Radar; não duplica regras do Stage 0/1 no
   frontend.
3. A entrega 3 altera operação de dados, mas não pode expor staging ao cliente
   final nem permitir que conteúdo não aprovado entre no catálogo/RAG.
4. A entrega 4 pode preparar ou enriquecer o perfil, mas não deve acionar match
   personalizado sem perfil explícito aceito pelo usuário.
5. Toda mudança no motor de match exige spec própria e eval de matching; não é
   uma dependência implícita de nenhuma entrega desta iniciativa.

## Decisões de produto preservadas

- Afinidade não é probabilidade de aprovação.
- Evidência vem de trechos reais, e elegibilidade só elimina incompatibilidade
  comprovada; perfil incompleto é apresentado como pendência, não como reprovação.
- Descoberta é staging global com gate de operador; catálogo gold é a fonte
  operacional do Radar, e RAG é uma superfície separada.
- Explorer sem perfil ajuda a aprender e estruturar hipóteses; Radar exige um
  perfil mínimo e preserva a decisão humana sobre o que aceitar.

## Cadência spec-driven

1. Este documento mantém o backlog, as fronteiras e as dependências.
2. A spec filha define contrato, UX, testes e critérios de aceite da próxima
   entrega.
3. Só a spec filha aprovada entra em implementação.
4. Ao concluir uma entrega, este mapa é atualizado com status, decisão tomada,
   link para PR/commit e qualquer dependência nova descoberta.

## Itens deliberadamente não planejados ainda

- Novas fontes/formats de descoberta além de página web e PDF por URL.
- OCR para PDFs escaneados.
- Recomendações ou previsão de aprovação.
- Memória automática não revisada para o Explorer.
- Mudança da ontologia gold ou do funil de match v3.

## Backlog de hardening transversal

- **Documentos de entrada:** definir um limite de caracteres/tokens para o texto
  extraído de anexos antes da chamada ao LLM. O limite atual é de tamanho do
  arquivo (10 MB), não do texto extraído. A spec filha deve decidir truncamento
  ou sumarização previsível, mensagem ao usuário, telemetria de tamanho e testes
  com documentos longos. Abrange extração de perfil, biblioteca e contexto de
  escrita.
