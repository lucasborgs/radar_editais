# Grantable — Análise de Funcionalidades

> Fonte: https://grantable.co/docs/ai-automation  
> Data da análise: 2026-05-11

---

## 1. Visão Geral e Posicionamento

Grantable é uma plataforma de gestão de grants construída especificamente para o contexto de captação de recursos — não é uma ferramenta genérica de IA com grants como feature adicional. A inteligência artificial é integrada ao workspace como um componente nativo que lê arquivos, cria documentos, pesquisa bancos de dados e gerencia pipelines.

**Filosofia central:** _"AI drafts, humans decide."_  
Todos os outputs são revisáveis, com raciocínio transparente exibido ao usuário. O sistema não fabrica estatísticas nem decisões estratégicas — requer input humano para dados financeiros e posicionamento.

**Efeito composto:** quanto mais a organização usa o sistema (uploads de propostas, relatórios, perfis), mais contextualizado ficam os outputs. No décimo grant, a IA já conhece o voice e o histórico da organização.

---

## 2. Arquitetura

```
┌─────────────────────────────────────────────────────┐
│                    Workspace                        │
│                                                     │
│  File Tree          Chat Interface      Doc Viewer  │
│  ├── /Skills/       ├── Slash cmds     └── Edição   │
│  ├── /Boilerplate/  ├── @ mentions         inline   │
│  ├── /Prospects/    └── Model selector              │
│  └── Proposals/                                     │
│                                                     │
│         ┌──────────────────────────────┐            │
│         │     Organizational Memory    │            │
│         │  - Perfil da org             │            │
│         │  - Documentos enviados       │            │
│         │  - Histórico de conversas    │            │
│         │  - Boilerplate library       │            │
│         └──────────────────────────────┘            │
│                                                     │
│         ┌──────────────────────────────┐            │
│         │   External Data Sources      │            │
│         │  - 800k+ funders database    │            │
│         │  - IRS 990 filings           │            │
│         │  - Web research              │            │
│         └──────────────────────────────┘            │
└─────────────────────────────────────────────────────┘
```

### Componentes principais

| Componente | Descrição |
|---|---|
| **Organizational Memory** | Contexto persistente acumulado via documentos, perfis e conversas. Fica disponível para todos os skills. |
| **File Tree** | Workspace com pastas estruturadas. A IA cria documentos diretamente no file tree — não há copy-paste. |
| **Skills Engine** | Workflows estruturados ativados por slash commands (`/`). Cada skill é um playbook metodológico para um tipo de tarefa. |
| **Funder Database** | Base com 800k+ organizações financiadoras, enriquecida com dados de 990 filings e pesquisa web. |
| **Model Router** | Três tiers de modelo (Auto/Pro/Fast) selecionáveis por mensagem, sem perda de contexto. |

---

## 3. Skills (Slash Commands)

Skills são workflows estruturados de IA ativados pelo caractere `/`. Cada skill fornece um playbook metodológico específico — diferente do chat livre, que produz respostas oportunistas.

### Como invocar
1. Digitar `/` no chat → picker de skills aparece
2. Selecionar o skill desejado
3. Descrever a necessidade
4. Referenciar arquivos com `@mention` quando necessário

### Skills padrão

#### `/grant-writing`
Drafting completo de propostas em 5 fases:
1. Intake do RFP e extração de requisitos
2. Confirmação de elegibilidade
3. Planejamento da aplicação e estrutura de pastas
4. Redação de seções com rastreamento de requisitos
5. Revisão de compliance

#### `/prospecting`
Pesquisa e avaliação de financiadores na base de 800k+ organizações.  
Output: tabela interativa com classificações de fit (Strong / Good / Moderate / Low).

#### `/profile`
Constrói perfis detalhados da organização, financiadores ou parceiros combinando:
- Dados da base interna
- IRS 990 filings
- Pesquisa web

#### `/boilerplate`
Mantém biblioteca de conteúdo reutilizável:
- Declarações de missão
- Descrições de programas
- Dados de outcomes
- Bios de staff

Extrai automaticamente de documentos e propostas antigas.

#### `/review`
Revisão em 3 passes:
1. Compliance (requisitos do RFP)
2. Avaliação de qualidade
3. Checagem de completude

Output: citações de localização específica e recomendações acionáveis.

#### `/archive`
Organização do workspace — identifica e sugere arquivamento de:
- Aplicações submetidas
- Arquivos antigos
- Prospects descartados
- Drafts desatualizados

### Customização de Skills
- Skills padrão ficam em `/Skills/<nome>/SKILL.md` — editáveis diretamente
- Custom skills: criar pasta em `/Skills/` com `SKILL.md` contendo metadados + workflow
- Arquivos de referência (`.md`) podem acompanhar o skill (templates, guias de estilo)
- Mudanças têm efeito imediato após salvar

---

## 4. Grant Opportunity Brief

Feature específica para responder: _"Vale a pena investir semanas neste grant?"_

### Inputs
- Documento do grant (RFP ou descrição da oportunidade)
- Perfil organizacional
- Materiais de suporte (propostas anteriores, relatórios anuais, dados de avaliação)

### Output: Documento no workspace contendo
- Background do financiador e histórico de doações
- Métricas-chave (tamanho típico do award, geografia, grantees anteriores)
- **Decision Matrix** com score em 15 critérios
- Avaliação narrativa de alinhamento
- Recomendações de posicionamento para a aplicação

### Decision Matrix — 3 Dimensões

| Dimensão | Pontuação máxima | Critérios avaliados |
|---|---|---|
| **Funder Priorities** | 12 pts | Alinhamento geográfico, áreas de interesse, população-alvo, tipo de funding |
| **Credibility & Readiness** | 15 pts | Credibilidade com o financiador, expertise, gestão fiscal, plano de projeto, governança |
| **Effort & Timing** | 18 pts | Clareza do RFP, tempo disponível, viabilidade de auditoria |

### Sistema de Scoring (0-100)

| Faixa | Classificação | Decisão sugerida |
|---|---|---|
| 70–100 | **Strong** | Vale a pena |
| 50–69 | **Good** | Promissor com posicionamento estratégico |
| 30–49 | **Moderate** | Requer framing cuidadoso |
| 0–29 | **Low** | Provavelmente não vale o esforço |

Cada score inclui **nível de confiança** (high/medium/low) indicando qualidade da evidência.

### Critérios Mandatórios (pass/fail antes do scoring)
- Status 501(c)(3)
- Elegibilidade geográfica
- Tipo de organização
- Faixa de budget

---

## 5. AI Writing

### Workflow de Escrita (5 passos)

```
1. Request  →  2. File Creation  →  3. Review  →  4. Revision  →  5. Iterate
   (brief)       (doc no file tree)  (doc viewer)   (chat feedback)  (update direto)
```

Não há copy-paste. O arquivo é criado diretamente no workspace e atualizado in-place a cada revisão.

### Boas práticas
- Briefs específicos: incluir _o que_, _quais materiais referenciar_ e _formato esperado_
- Pedir outline antes de drafts completos para seções complexas
- Anexar arquivos de referência diretamente à mensagem (via `@mention`)
- Usar revisão iterativa como fluxo padrão, não como exceção

### Integração com `/grant-writing`
Quando ativado, o skill de escrita:
- Revisa requisitos do RFP antes de escrever qualquer seção
- Cruza com materiais enviados
- Endereça critérios do grant ponto a ponto
- Cita dados organizacionais com referência à fonte

---

## 6. Model Tiers

| Tier | Modelo base | Uso recomendado | Consumo de budget |
|---|---|---|---|
| **Auto** (padrão) | Claude Sonnet | Drafting diário, pesquisa de financiadores, análise de RFP, revisões | Moderado |
| **Pro** | Claude Sonnet (higher quality settings) | Narrativas finais, análise estratégica complexa, avaliações críticas de financiadores | Alto (plano Pro/Pro+) |
| **Fast** | Claude Haiku | Perguntas rápidas, brainstorming, consultas simples | Baixo |

**Estratégia recomendada:** _"use Auto como baseline, Fast para coisas pequenas, e reserve Pro para os momentos em que qualidade realmente importa."_

O tier pode ser trocado mid-conversation sem perda de contexto.

---

## 7. Princípios de Prompting

**Fórmula base:** `o que preciso` + `quais materiais referenciar` + `formato esperado`

**Método de briefing interativo:**
- Pedir que a IA te entreviste com perguntas-alvo
- Responder diretamente no chat cria contexto mais rico do que tentar escrever prompts perfeitos
- Especialmente eficaz para novas aplicações ou financiadores desconhecidos

**Interpretação de outputs:**
- Verificar citações para identificar gaps nos materiais enviados
- Hedges e "informação faltante" = avaliação honesta, não falha
- Budget e decisões estratégicas sempre passam pelo usuário

---

## 8. Workflows Típicos

### Nova Aplicação
```
Upload RFP + materiais
  → Análise de requisitos (chat ou /grant-writing)
    → Outline da proposta
      → Drafting por seção (/grant-writing)
        → Revisão de alinhamento (/review)
```

### Avaliação de Oportunidade
```
RFP ou URL do grant
  → Grant Opportunity Brief
    → Decision Matrix (score 0-100)
      → Go / No-go decision
```

### Melhoria de Draft Existente
```
Upload draft existente
  → /review (compliance + qualidade)
    → Revisões específicas no chat
      → Atualização in-place no file
```

### Pesquisa de Financiadores
```
/prospecting + contexto do programa
  → Tabela interativa (Strong/Good/Moderate/Low)
    → Grant Opportunity Brief nos candidatos Strong
      → Funder Profile (/profile) antes de aplicar
```

---

## 9. Limitações Explícitas

- Não fabrica estatísticas ou dados organizacionais
- Não substitui decisões estratégicas (requer input do usuário)
- Qualidade do output é proporcional à qualidade dos materiais enviados
- Pro tier disponível apenas em planos pagos (Pro/Pro+)
- Foco exclusivo no contexto norte-americano (base de dados 501(c)(3), IRS 990)
