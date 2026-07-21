# Spec — Autoridade documental

**Status:** vigente · **Data:** 2026-07-14
**Documento-pai:** [`system-coherence.md`](system-coherence.md)
**Perfis afetados:** usuário técnico e operador
**Impacto:** baixo; organização e documentação, sem mudança de runtime

## 1. Problema comprovado

O inventário anterior a esta spec encontrou 101 arquivos Markdown rastreados. A
documentação relevante para manutenção estava distribuída entre 23 arquivos em
`docs/specs/`, 7 em `docs/features/`, 22 em `docs/components/` e 27 em
`docs/historical/`, além dos documentos na raiz e de referências especializadas.

O volume não é, por si só, um problema. A ambiguidade é:

- `docs/specs/` contém contratos vigentes, propostas, entregas concluídas e
  arquiteturas explicitamente substituídas;
- `docs/features/` e `docs/components/` misturam propostas executáveis,
  referências técnicas, entrevistas, auditorias e planos superados;
- vários documentos sem status descrevem o sistema no presente mesmo quando
  citam runtimes removidos, como `index.json`, wiki pages e matchers legados;
- `AGENTS.md` e `CLAUDE.md` têm 232 linhas cada e diferem materialmente apenas no
  título, destinatário e um espaço de alinhamento;
- o `README.md` aponta o setup e a validação para `CLAUDE.md`, embora
  `AGENTS.md` já seja o runbook mantido; e
- não existe um índice atual que explique qual documento responde a cada tipo
  de pergunta.

O resultado é que o leitor precisa reconstruir a cronologia do projeto para
descobrir o que é normativo hoje.

## 2. Resultado pretendido

Uma pessoa deve conseguir partir de um único índice e localizar, sem conhecer a
história do repositório:

1. o que o produto é e como executá-lo;
2. como o runtime atual funciona;
3. onde vivem regras de domínio;
4. quais specs ainda governam decisões ou trabalho aceito;
5. quais documentos são apenas referência; e
6. quais documentos registram decisões e arquiteturas passadas.

Um fato atual terá uma fonte autoritativa. Outros documentos poderão resumir o
fato e apontar para essa fonte, mas não manter cópias concorrentes.

## 3. Fora de escopo

- alterar comportamento de produto, arquitetura, schemas, APIs ou migrations;
- reescrever a história do desenvolvimento;
- decidir se propostas antigas devem ser implementadas;
- transformar pesquisa histórica em roadmap;
- revisar regras de domínio sem evidência específica; e
- versionar os artefatos locais de avaliação protegidos.

## 4. Modelo de autoridade

### 4.1 Porta de entrada

Criar `docs/README.md` como índice de leitura. Ele não repetirá a arquitetura ou
os comandos; apenas orientará o leitor por pergunta, perfil e autoridade.

O `README.md` da raiz continua sendo a porta pública do projeto e aponta para
esse índice quando o leitor precisar de documentação aprofundada.

### 4.2 Fontes autoritativas

| Pergunta | Fonte autoritativa | Limite |
|---|---|---|
| O que é o produto e como começar? | `README.md` | visão resumida; não duplica runbook |
| Como instalar, executar, validar e operar em desenvolvimento? | `AGENTS.md` | comandos e cuidados atuais |
| Como o runtime e os fluxos atuais funcionam? | `docs/architecture.md` | estado implementado, não proposta |
| Quais são as regras e vocabulários de domínio? | `WIKI.md` e `wikis/` | conteúdo lido pelo código e regras por fonte |
| Qual é o trabalho técnico adiado e comprovado? | `docs/BACKLOG.md` | não é roadmap de produto |
| Qual mudança foi aceita ou ainda está em decisão? | `docs/specs/` | intenção até implementação; contrato vigente depois dela |
| Como operar ou compreender um subsistema atual? | `docs/reference/` | explicação derivada; não redefine arquitetura ou domínio |
| Por que uma decisão passada foi tomada? | `docs/historical/` | registro sem autoridade atual |
| Qual configuração é suportada? | `.env.example` e manifests | docs apontam; não copiam defaults extensos |
| Qual é o contrato visual implementado? | `frontend/DESIGN_SYSTEM.md` | referência colocada junto ao frontend |

### 4.3 Precedência em divergências

1. Regras de negócio e vocabulários: `WIKI.md`/`wikis/` são normativos; a
   implementação deve ser reconciliada com eles.
2. Runtime existente: código, migrations e manifests provam o comportamento;
   `docs/architecture.md` deve descrevê-lo com fidelidade.
3. Specs propõem ou registram decisões, mas não provam que algo está em produção.
4. Referências explicam o estado vigente sem criar contratos concorrentes.
5. Histórico nunca prevalece sobre documentação corrente.

Uma divergência não deve ser resolvida silenciosamente escolhendo o texto mais
novo. Primeiro se identifica o tipo de fato e, então, sua fonte autoritativa.

## 5. Ciclo de vida das specs

Toda spec mantida em `docs/specs/` deve declarar no início:

- **Status:** `proposta`, `aprovada`, `em implementação` ou `vigente`;
- **Data** da decisão ou última reconciliação;
- **Documento-pai**, quando houver; e
- spec que substitui ou é substituída, quando aplicável.

Os estados têm significado objetivo:

| Status | Significado |
|---|---|
| `proposta` | decisão ainda não aprovada; não autoriza implementação |
| `aprovada` | escopo e critérios aceitos; implementação pode começar |
| `em implementação` | entrega incompleta e ativamente executada |
| `vigente` | implementação concluída e o documento ainda expressa contrato útil |

`Concluída` descreve execução, não autoridade. Uma spec concluída:

- permanece em `docs/specs/` como `vigente` se seus contratos ainda ajudam a
  compreender ou validar o sistema; ou
- vai para `docs/historical/` se serve apenas como plano, diário de execução ou
  explicação de uma implementação já consolidada em fonte mais canônica.

Specs `substituídas`, `canceladas` ou referentes apenas a runtimes removidos não
permanecem em `docs/specs/`.

## 6. Classificação dos diretórios

### 6.1 `docs/specs/`

Conterá apenas propostas em decisão, trabalho aprovado/em execução e contratos
implementados ainda vigentes. A presença nesse diretório significa que o leitor
deve considerar o documento na tomada de decisão atual.

### 6.2 `docs/reference/`

Conterá explicações técnicas vigentes que não autorizam mudanças: segurança,
memória, operação, autoria de playbooks e outros guias comprovadamente alinhados
ao código. Cada referência deve apontar para as fontes normativas das quais
deriva.

### 6.3 `docs/historical/`

Conterá specs substituídas, auditorias datadas, pesquisas, comparativos, planos
concluídos sem autoridade residual e registros de implementação. Mover um
documento para histórico preserva seu valor explicativo; apenas retira sua
autoridade sobre o presente.

### 6.4 Categorias descontinuadas

`docs/features/` e `docs/components/` deixam de receber documentos novos. Seus
arquivos serão classificados individualmente como spec, referência ou
histórico. Os diretórios só serão removidos quando estiverem vazios e todos os
links tiverem sido atualizados.

## 7. Inventário inicial de migração

Esta tabela define lotes e critérios. Ela não presume a classificação final de
um documento cuja aderência ao código ainda não foi comprovada.

| Lote | Conteúdo atual | Destino candidato | Prova exigida |
|---|---|---|---|
| A | `README.md`, `AGENTS.md`, `docs/architecture.md`, `WIKI.md`, `wikis/`, `docs/BACKLOG.md` | permanecer | leitura cruzada e referências válidas |
| B | specs já marcadas como substituídas pela v3 | `docs/historical/` | vínculo explícito com `v3-unified.md` e ausência de contrato exclusivo vivo |
| C | specs marcadas como implementadas/concluídas | `docs/specs/` ou `docs/historical/` | contrato ainda consumido por código/teste/operação |
| D | specs antigas ainda marcadas como proposta/design review | spec atual, histórico ou remoção por duplicidade | confronto com implementação, backlog e iniciativa ativa |
| E | `docs/features/` | spec, referência ou histórico | classificação arquivo a arquivo; não inferir pelo nome |
| F | `docs/components/` | spec, referência ou histórico | classificação arquivo a arquivo; validar paths e estado descrito |
| G | auditorias e relatórios datados fora de `historical/` | `docs/historical/` | natureza temporal e ausência de autoridade operacional |
| H | `docs/security/tenant-isolation.md` e guias técnicos vigentes | `docs/reference/` | testes/implementação correspondentes ainda vivos |
| I | `docs/market/relatorio-inteligencia-mercado.md` | `docs/historical/` | relatório datado; não é contrato do sistema |

### 7.1 Casos já comprovados

- `CLAUDE.md` é uma cópia de `AGENTS.md`, não um componente do sistema nem uma
  integração necessária. O histórico Git e as autorias preservam a contribuição
  do Claude Code. Após atualizar a referência viva no `README.md`, o arquivo pode
  ser removido.
- `hypergraph-architecture.md`, `kg-redesign.md`, `kg-v2-residuos.md`,
  `match-evolution.md` e `v3-match-kg-redesign.md` já se declaram substituídos
  pela linhagem v3 e são candidatos diretos a `docs/historical/`.
- `docs/auditoria_tokens_2026_06_23.md` é uma auditoria datada e não deve competir
  com a arquitetura atual.
- `docs/security/tenant-isolation.md` tem implementação e suíte correspondentes;
  é candidata a referência vigente, não a spec de mudança.

### 7.2 Casos que exigem reconciliação antes de mover

- specs com corpo escrito no presente, mas status `proposta`, `design review`,
  `implementada` ou `concluída`;
- auditoria agêntica `docs/components/agents/00`–`09`, pois cada item pode estar
  implementado, refutado, dormente ou ainda proposto;
- documentos de conhecimento e memória que misturam arquitetura vigente com
  evolução proposta; e
- specs de UX anteriores às superfícies atuais do Radar e workspace.

Em caso de dúvida, o arquivo permanece no lugar com status explícito
`reconciliação pendente`; não será promovido a referência nem removido.

## 8. Plano de execução

### Etapa 1 — Índice e fontes primárias

1. criar `docs/README.md` com trilhas para produto, técnico e operador;
2. corrigir o link de setup do `README.md` para `AGENTS.md`;
3. remover `CLAUDE.md` depois de provar ausência de consumidor operacional; e
4. adicionar links cruzados mínimos entre as fontes primárias.

### Etapa 2 — Specs

1. aplicar o cabeçalho de ciclo de vida às specs que permanecerem atuais;
2. mover com `git mv` as specs comprovadamente substituídas;
3. reconciliar propostas antigas contra código, testes, backlog e specs-pai; e
4. atualizar links após cada lote, sem deixar redirects ou cópias.

### Etapa 3 — Features, components e referências

1. classificar cada arquivo por função real;
2. mover somente quando o destino estiver comprovado;
3. separar, quando necessário, a referência vigente do relato histórico sem
   reescrever decisões passadas; e
4. remover os diretórios descontinuados apenas quando vazios.

### Etapa 4 — Fechamento

1. verificar links Markdown locais;
2. confirmar que todo arquivo fora de `historical/` possui função atual clara;
3. confirmar que nenhuma fonte primária duplica contratos extensos; e
4. registrar a reconciliação e tornar esta spec `vigente` ou histórica.

## 9. Reversibilidade e preservação

- movimentos usam `git mv`, preservando histórico e autoria;
- nenhum documento é apagado por estar desatualizado, exceto duplicata integral
  sem função própria, como `CLAUDE.md`;
- referências históricas internas podem continuar mencionando nomes antigos;
  apenas links quebrados são corrigidos;
- não se atualiza prosa histórica para fazê-la parecer contemporânea; e
- qualquer remoção exige busca de referências e ausência de consumidor vivo.

## 10. Validação

Para cada lote:

- `git diff --check`;
- verificador de links Markdown locais;
- `rg` pelos paths antigos após movimentos ou remoções;
- revisão do índice contra todos os documentos correntes; e
- `git status --short` para confirmar que artefatos locais de avaliação não
  entraram no diff.

Não há validação Python ou frontend obrigatória enquanto as mudanças forem
exclusivamente documentais e não alterarem manifests ou arquivos consumidos em
runtime. Como `WIKI.md` é lido pelo código, qualquer mudança futura em seus
blocos YAML continua exigindo os testes de schema correspondentes.

## 11. Critérios de conclusão

O eixo de autoridade documental estará concluído quando:

1. `docs/README.md` responder onde encontrar cada tipo de informação;
2. `README.md`, `AGENTS.md`, `docs/architecture.md` e `WIKI.md` não mantiverem
   contratos concorrentes;
3. não houver documentação operacional duplicada por ferramenta de autoria;
4. toda spec corrente tiver status e autoridade claros;
5. nenhum documento substituído permanecer no caminho de specs correntes;
6. `docs/features/` e `docs/components/` estiverem vazios e removidos;
7. referências vigentes estiverem separadas de registros históricos;
8. todos os links locais estiverem válidos; e
9. nenhuma alteração de documentação tiver mudado runtime ou regra de negócio.

## 12. Resultado da execução

Concluído em 2026-07-14:

- `docs/README.md` tornou-se o índice de leitura por pergunta e perfil;
- `AGENTS.md` tornou-se o único runbook de manutenção e `CLAUDE.md` foi removido;
- 15 planos mistos ou substituídos saíram de `docs/specs/`, restando nove specs
  com status e autoridade explícitos;
- todo o conteúdo de `docs/features/` e `docs/components/` foi reconciliado;
- referências vigentes foram concentradas em `docs/reference/`;
- auditorias, pesquisas e planos superados foram preservados em
  `docs/historical/` com índice e sem autoridade atual;
- links históricos para destinos existentes foram recalculados; alvos removidos
  deixaram de ser links sem reescrever o relato; e
- nenhum contrato de runtime, schema, API, migration ou produto foi alterado.

Não permaneceram documentos em `docs/specs/`, `docs/features/` ou
`docs/components/` por incerteza. O material local de avaliação protegido
permaneceu não rastreado.

## 13. Gatilho de reconciliação ao promover uma spec

Ao concluir ou promover qualquer spec, o mesmo commit deve revisar seu ciclo de
vida antes do merge:

- confirmar no código, testes e operação se o contrato foi implementado;
- corrigir o campo `Status:` para refletir o estado verificável;
- manter em `docs/specs/` somente contratos ainda úteis e vigentes;
- mover planos e diários concluídos para `docs/historical/` com `git mv`;
- atualizar links internos e o índice documental quando aplicável;
- buscar o path antigo no repositório, incluindo migrations e comentários; e
- registrar explicitamente pendências ou escopo não promovido.

A reconciliação não reescreve o histórico nem promove uma proposta sem evidência.
