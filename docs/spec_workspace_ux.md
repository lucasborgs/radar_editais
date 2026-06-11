# Spec UX — Workspace de escrita (Fase 3 · 1b)

> Decisões de produto registradas em **2026-06-11** (Lucas + sessão de spec).
> Escopo: a **1b** do ROADMAP — o workspace pós-seleção onde a proposta (ou
> pitch) é elaborada. Sucede a 1a ([spec_frontdoor_ux.md](spec_frontdoor_ux.md)):
> o "Começar proposta" do front-door abre isto aqui.

## 1. Objetivo

Um **workspace estilo IDE** para trabalhar numa oportunidade selecionada:
documento da proposta no centro, navegação à esquerda, agente à direita.
Substitui o fluxo atual de escrita (`/chat?edital=`, chat linear sem visão
estrutural do documento). A máquina por baixo é a mesma — `WritingSession`
(agente com tools, `save_draft` com critic, RAG por `search_edital`),
checklist 3-passes, `mode=pitch` — o que muda é a **cara de ambiente de
trabalho**.

## 2. Decisões de produto (fechadas)

| # | Decisão | Escolha | Implicação principal |
|---|---|---|---|
| W1 | Layout | **IDE 3 painéis** — explorer (esq., **retrátil**), editor (centro), chat (dir.) | desktop-first; explorer colapsa a uma barra de ícones |
| W2 | Explorer v1 | **Seções da proposta + anexos da library** | edital navegável (Art./§) e brief ficam FORA da v1 (BACKLOG); o agente cita artigos via chat |
| W3 | Modelo de edição | **Co-edição total** — editor sempre editável; o agente aplica mudanças direto no texto (sem aceite), com desfazer | alinhado ao backend real: a tool `save_draft` JÁ persiste seções como efeito do turno |
| W4 | Escopo v1 | proposta de edital **+ pitch (mode=pitch) + checklist auto-review + export** | v1 cheia; o que ficou de fora está em §7 |
| W5 | Visibilidade da co-edição | **Highlight temporário + desfazer**: seções tocadas pelo agente ficam marcadas até o usuário interagir; cada edição do agente gera snapshot com "↶ desfazer" no chat | consciência sem fricção de aceite (≠ diff card da 1a — aqui a confirmação é a posteriori) |
| W6 | Checklist | **Ancorado no editor**: findings dos 3 passes viram marcações por seção (ícone na margem, contador no explorer); ação "corrigir com IA" vira turno com `section_hint` | reusa `_infer_section` do checklist_service, que já atribui seção a cada issue |
| W7 | Rotas | **Substitui já**: rota nova `/workspace/{session_id}`; `/chat?edital=` redireciona (resolve/cria a sessão); `/sessions` vira lista que abre workspaces | um fluxo único de escrita; nada de manutenção dupla |
| W8 | Export | **PDF + DOCX + Markdown/copiar**; template por agência → BACKLOG (pós-v1) | geração client-side a partir do markdown (sem dependência nova de backend) |

### Decisões técnicas (delegadas ao modelo)

- **Editor v1 = blocos por seção em markdown**: cada seção renderizada
  (ReactMarkdown) por padrão; clique entra em edição inline (textarea
  auto-grow); blur/Cmd-S salva via `PUT /writing/{id}/section` (endpoint já
  existe). Rich editor (TipTap) é upgrade pós-v1 — não pagar a dependência
  antes de validar o fluxo. Highlight e desfazer operam no nível da seção
  (diff fino intra-seção fica fora da v1).
- **Undo client-side**: antes de aplicar a edição do agente na UI, guarda-se o
  conteúdo anterior da seção (snapshot em memória, pilha por seção);
  "desfazer" = `PUT section` com o conteúdo antigo. Sem versionamento novo no
  backend.
- **Export client-side**: markdown vem de `GET /writing/{id}/export`; PDF via
  print stylesheet (janela de impressão com CSS dedicado), DOCX via lib `docx`
  (npm), .md/copiar direto. Cabeçalho: empresa + edital + data.
- **Desktop-first**: em viewport estreita o workspace vira 2 abas empilhadas
  (Documento | Chat) com explorer em drawer — leitura e turnos funcionam,
  edição confortável é desktop.

## 3. Anatomia da tela

```
┌──┬─────────────────┬──────────────────────────────────┬────────────────────┐
│☰ │ EXPLORER (retrátil)│ EDITOR                         │ CHAT               │
│  │                 │                                  │                    │
│§ │ ▾ Proposta      │  ## 2. Metodologia          ⚠︎1   │ 🤖 Reescrevi a     │
│  │   1. Resumo   ✓ │  ┌─ tocada pelo agente ─────────┐│    metodologia     │
│📎│   2. Metodol. ⚠︎1│  │ O projeto aplicará técnicas  ││    citando o       │
│  │   3. Cronogr. ⚠︎2│  │ de ML para triagem clínica,  ││    Art. 5º.        │
│  │   4. Orçamento  │  │ conforme exige o Art. 5º…    ││    [↶ desfazer]    │
│  │ ▾ Anexos (@)    │  └──────────────────────────────┘│                    │
│  │   deck.pdf      │                                  │ 👤 detalha o       │
│  │   equipe.docx   │  ## 3. Cronograma           ⚠︎2   │    cronograma…     │
│  │                 │  Fase 1 — validação…             │                    │
│  │ [▶ Revisar]     │  (clique numa seção → edita)     │ [✎ …]    [@] [⏎]   │
│  │ [⬇ Exportar]    │                                  │                    │
└──┴─────────────────┴──────────────────────────────────┴────────────────────┘
   ↑ colapsado: barra de ícones (§ seções · 📎 anexos · ▶ revisar · ⬇ exportar)
```

- **Header fino** do workspace: nome do edital/alvo + badge do mode
  (proposta/pitch) + completude do documento (n de seções com conteúdo) +
  voltar ao radar (`/`).
- **Explorer**: árvore de seções (do outline da sessão — `section_titles`),
  com ✓ (tem conteúdo), ⚠︎n (findings do review); clicar rola o editor até a
  seção. Anexos = `library_items` do workspace; clicar insere @ mention no
  composer do chat. Botões Revisar e Exportar vivem no rodapé do explorer (e
  na barra colapsada).
- **Editor**: documento contínuo, uma âncora por seção do outline. Seções
  vazias aparecem como placeholder ("— rascunhe aqui ou peça ao agente").
- **Chat**: o mesmo padrão visual da 1a (transcript + composer), com:
  `pending_user_input` renderizado como prompt destacado (o backend já emite),
  `compliance_flags` como aviso âmbar no turno, e a narração de cada edição do
  agente com link pra seção + "↶ desfazer".

## 4. Fluxos

### F1 — Entrar no workspace
1. Do front-door (1a): "Começar proposta" → `POST /writing/start`
   (edital_id + profile) → navega `/workspace/{session_id}`.
2. De `/sessions`: lista de sessões (já existe) → clicar abre o workspace.
3. Deep-link antigo `/chat?edital=X`: resolve a sessão mais recente desse
   edital no workspace do usuário (ou cria) e **redireciona** pro workspace.
4. Primeira visita de uma sessão nova: editor com outline vazio + mensagem
   inicial do agente no chat (reusa `/writing/section-start` ou welcome local).

### F2 — Turno com co-edição (o coração)
1. Usuário manda mensagem (com @ mentions de anexos resolvidos como hoje).
2. `POST /writing/{id}/turn` → o agente trabalha; `save_draft` persiste
   seções no DB como efeito colateral.
3. Resposta chega: front extrai do `tool_trace` quais seções foram salvas,
   recarrega o documento (`GET /writing/{id}/document`), aplica:
   - snapshot do conteúdo anterior na pilha de undo da seção;
   - **highlight** na(s) seção(ões) tocada(s) (fundo suave, esmaece quando o
     usuário rola até lá/clica);
   - no chat, a resposta do agente + chip por seção editada ("§ Metodologia
     atualizada · ↶ desfazer").
4. `pending_user_input` → prompt destacado; `compliance_flags` → aviso âmbar.

### F3 — Edição manual
Clique na seção → textarea inline → blur/Cmd-S → `PUT /writing/{id}/section`.
O agente vê o conteúdo novo no turno seguinte (tools `read_section`/
`read_full_proposal` leem do DB). Sem lock: último que salva ganha (single
user por workspace na prática).

### F4 — Revisar (checklist ancorado)
1. "▶ Revisar" → `POST /writing/{id}/checklist/auto-review` (3 passes
   paralelos; mostrar progresso).
2. Findings agrupados por seção (o serviço já infere a seção de cada issue):
   ícone ⚠︎ na margem da seção + contador no explorer; clique abre o finding
   num popover com o texto do issue e **"corrigir com IA"** → vira turno
   pré-preenchido com `section_hint` da seção.
3. Issues sem seção inferível ("Geral") aparecem num bloco no topo do editor.

### F5 — Exportar
"⬇ Exportar" → modal com 3 opções: PDF (print stylesheet), DOCX (lib client),
copiar/baixar .md (de `GET /writing/{id}/export`). "Salvar na nuvem" mantém o
`POST /writing/{id}/save-to-storage` existente como quarta ação.

### F6 — Pitch (mode=pitch)
Mesmo workspace, outline de pitch (já existe `_default_pitch_outline`) e badge
"Pitch — {investidor}" no header. Entrada: card de investidor no radar da 1a
ganha "Escrever pitch" (follow-up do BACKLOG multi-quadrante, resolvido aqui).

## 5. Contratos com o backend

### Já existe (reuso direto — backend/routers/writing.py)
| Peça | Endpoint |
|---|---|
| Criar sessão | `POST /writing/start` (e variante pitch — conferir parâmetro de mode) |
| Turno do agente (save_draft side-effect, compliance paralelo) | `POST /writing/{id}/turn` → `{assistant_message, tool_trace, pending_user_input, compliance_flags, …}` |
| Documento por seções | `GET /writing/{id}/document` · `GET /writing/sessions/{id}/document` |
| Edição manual de seção | `PUT /writing/{id}/section` |
| Outline/info da sessão | `get_info` → `section_titles` (exposto via start/list) |
| Checklist + auto-review 3 passes (issue→seção via `_infer_section`) | `GET/PUT /writing/{id}/checklist*` · `POST /writing/{id}/checklist/auto-review` |
| Export markdown + storage | `GET /writing/{id}/export` · `POST /writing/{id}/save-to-storage` |
| Lista/delete de sessões | `GET /writing/sessions` · `DELETE /writing/sessions/{id}` |

### Deltas (pequenos)
- **W-D1 — seção no `tool_trace`**: garantir que cada entrada `save_draft` do
  trace exponha o título da seção salva (se hoje o trace não carrega os args,
  enriquecer `_extract_tool_trace` — mudança local em writing_session.py).
- **W-D2 — resolver sessão por edital** (para o redirect de `/chat?edital=`):
  se `GET /writing/sessions` já devolve `edital_id` por sessão, resolve no
  front; senão, expor o campo na listagem.
- **W-D3 — start de pitch**: conferir como `mode=pitch` é selecionado no
  `/writing/start` e expor se necessário.
- Export PDF/DOCX: **sem delta** (client-side, §2).

## 6. Rotas

| Rota | Destino |
|---|---|
| `/workspace/{session_id}` | **novo** — o workspace |
| `/chat?edital=X` | redirect → workspace (resolve/cria sessão); `/chat` sem param já redireciona pra `/` (1a) |
| `/sessions` | mantém como lista; abrir → workspace |
| "Começar proposta"/"Escrever pitch" (1a) | criam sessão e abrem o workspace |

## 7. Não-escopo da v1 (BACKLOG)

- Edital navegável (Art./§) no explorer — o agente cita via chat por enquanto.
- Diff fino intra-seção e changelog/versões por seção (undo de 1 passo basta).
- Rich editor (TipTap) — upgrade quando o fluxo validar.
- Template de export por agência (FINEP/FAPESP).
- Colaboração multi-usuário / presença.
- Brief no explorer (continua acessível pela 1a/pipeline).

## 8. Plano de entrega (marcos)

1. **N1 — esqueleto**: rota `/workspace/{id}`, 3 painéis (explorer retrátil),
   documento read-only por seções + chat com turnos reais (sem co-edição
   visual ainda). `/sessions` abre o workspace.
2. **N2 — co-edição**: edição manual inline (PUT section), highlight +
   undo das edições do agente (W-D1), @ mentions do explorer.
3. **N3 — revisar**: auto-review ancorado por seção + "corrigir com IA".
4. **N4 — export + chegada**: PDF/DOCX/md, redirect `/chat?edital=`,
   "Começar proposta"/"Escrever pitch" na 1a apontando pra cá, mobile 2-abas.

## 9. Riscos e pontos de atenção

- **Co-edição sem aceite** é a maior aposta de UX (tensão consciente com
  "humans decide" — Lucas decidiu que aqui a confirmação é a posteriori):
  o highlight+undo precisa ser MUITO visível, senão mudança do agente passa
  batida. Medir uso do desfazer.
- **Consistência editor↔DB**: edição manual e save_draft do agente tocam o
  mesmo JSONB; o front deve recarregar o documento após cada turno (não
  confiar no estado local).
- **Latência do turno** (agente com tools): o chat precisa de estado "agente
  trabalhando…" com os passos do tool_trace aparecendo conforme rodam, se
  viável; senão indicador simples.
- **`/chat?edital=` redirect**: testar os deep-links existentes (sessions,
  editais/[id], PipelineCard) — eles foram o motivo de NÃO aposentar a rota
  na 1a.
