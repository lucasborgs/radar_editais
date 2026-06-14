<!-- SEED / DRAFT (2026-06-14) — playbook do mecanismo `subvencao`.
     COMPETÊNCIA (craft), não conhecimento. Delta sobre a persona-base (system
     prompt do Redator). Fato do edital → RAG; fato de praxe → rationale curto.
     NUNCA número/prazo/rubrica/elegibilidade (isso muda por edital → RAG).
     Cada `##` é um TIPO e roteia para um consumidor. Inerte até o loader existir.
     Destilado de entrevista a especialistas + 2 LLMs (2026-06-14); pendente
     validação por outcome real (learning loop, BACKLOG). -->

# Playbook — Subvenção econômica (não-reembolsável)

A lente: o avaliador **não compra ideia nem plano de vendas — compra um projeto de
mitigação de risco TECNOLÓGICO com retorno econômico (spillover)**. A subvenção
existe para o "vale da morte" (entre prova de conceito e produto). Escreva como
quem sabe disso.

## Padrões de escrita e tom  <!-- → Redator (geração) -->

**O arco da justificativa** (não é "problema→pesquisa→conhecimento"):
> oportunidade/dor real → estado da arte e por que falha → **gargalo tecnológico
> (o core, onde a subvenção entra)** → hipótese de solução → plano de redução de
> risco → resultado validado → apropriação econômica/impacto.

**Calibre o risco no ponto ótimo** *(é a presença de risco que justifica o
não-reembolsável; sem risco, "por que não um empréstimo?"; risco indefinido lê
como não-entregável)*:
- Exponha o risco, não o esconda: nomeie a incerteza técnica concreta e **como o
  projeto a testa**. Evite tanto "plataforma revolucionária de IA" (sem risco
  definido) quanto "ainda investigaremos qual abordagem usar" (risco demais).

**Craft por seção** (o que cada uma precisa FAZER; o erro que reprova):

| Seção | O trabalho real | Erro que mata |
|---|---|---|
| Justificativa | provar que vale correr o risco + por que recurso público | virar artigo; só tamanho de mercado |
| Objetivos | definir o que é SUCESSO (meta quantificada) | listar atividades ("desenvolver X") |
| Metodologia | provar que a equipe sabe REDUZIR incerteza | descrever Scrum/sprints no lugar de método técnico-experimental |
| Resultados | consequência econômica + KPI técnico quantificado | só entregável técnico; promessa irreal |
| Equipe | reduzir risco percebido: quem resolve qual risco | currículo; pôr o comercial como responsável técnico |
| Orçamento | coerência com os riscos (horas da equipe técnica = o coração) | lista de compras; concentrar em execução/marketing |
| Cronograma | sequência lógica de **aprendizado** | Gantt administrativo |

**Triângulo de ferro** — amarre numa cadeia só: `risco → experimento → entregável
→ prazo → recurso`. A incoerência que mais reprova: narrativa centrada em P&D mas
orçamento/cronograma concentrados em execução/marketing (ou vice-versa).

**Língua** — propositiva, quantificada, ciente do estado da arte. Aplique o padrão
**vago → específico**:
- "tecnologia inovadora" → "arquitetura que reduz 40% o tempo de processamento vs
  o estado da prática"
- "existe grande demanda" → "12 clientes potenciais com processo compatível; 3 LOIs"
- "será avaliada a viabilidade" → "será avaliada a estabilidade sob variação de
  temperatura entre X e Y, meta Recall > 85%"
- "profissional experiente" → "responsável pela validação industrial já em ambiente
  produtivo"

**Viabilidade comercial sem virar pitch deck:** evidência > TAM. Cliente
identificado, dor específica, LOIs — não "mercado de bilhões / seremos líderes".

## Heurísticas de aprovação  <!-- → ComplianceMonitor (avaliação) -->

- **Equipe e metodologia são MULTIPLICADORES de risco (fator ~0–1).** Inovação
  nota 10 com metodologia vaga → nota final baixa ("a ideia é ótima, mas não vão
  entregar"). Coerência e execução pesam mais que brilho da ideia.
- **Inovação crível > inovação extraordinária.** E domínio do **estado da arte**
  dá a nota alta — pareceristas (viés acadêmico) punem "inovação" que já existe em
  papers de anos atrás.
- Pesos reais ≠ edital: risco tecnológico + capacidade de execução + clareza da
  lógica = altíssimo; impacto + grau de inovação = alto; mercado = médio;
  sofisticação acadêmica = baixo.
- **"Sim com convicção"** = o projeto **antecipa o próprio risco e propõe plano B**
  ("se não atingir X no mês 4, fallback Y"); o leitor responde
  problema/risco/experimento/resultado/quem-executa **sem reler**.

## Anti-padrões / red flags  <!-- → ComplianceMonitor (avaliação) -->

Instant kills (sinalize forte):
- Tecnologia **sem incerteza** (lê como prestação de serviço/engenharia de rotina).
- Pesquisa **sem aplicação** (lê como projeto acadêmico → instrumento errado).
- Mercado **sem evidência**; objetivos impossíveis/promessa excessiva.
- **Buzzword stacking** (IA+blockchain+IoT…) sem mecanismo causal.
- Confundir **atividade com resultado**; inovação só estética (nova UI/dashboard).
- "Desenvolvimento de app/plataforma/ERP/e-commerce" tradicional; "único no mundo".
- Verba para **capital de giro / expansão comercial** (subvenção é P&D).

Erros que **boas empresas** cometem por desconhecer a praxe:
- Esconder a **"caixa preta"** (descrevem o botão/relatório; o avaliador avalia o
  algoritmo, não a interface).
- **Solução antes do problema**; subestimar explicação ("isso é óbvio" — não é).
- Tentar parecer perfeita (vencedores **expõem** fragilidades + mitigação).
- Não citar papers/patentes/benchmarks → falta de maturidade em P&D.

Termos que deslocam para o enquadramento errado: "pesquisa básica", "investigação
exploratória", "estudo preliminar", "revisão de literatura", "geração de
conhecimento"; e amadores: "criar um app", "disrupção", "Uber do X", "certeza de
sucesso".

---
**Fato (NÃO entra aqui — vem do edital via RAG):** TRL exigido, pesos/critérios
oficiais, elegibilidade/ROB, contrapartida %, duração, rubricas/itens financiáveis,
composição de equipe exigida, documentos, temas prioritários, indicadores
obrigatórios. *Regra de bolso: se muda ao trocar "FINEP 2024 → PIPE 2026", é fato
(RAG); se sobrevive à troca, é craft (fica aqui).*
