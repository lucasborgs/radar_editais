# KG-P1D — Resposta estratégica aterrada

## Incidente

Com `KG_PHASE1_EXPLORE_ENABLED=true`, uma saudação ou uma pergunta ampla podia
entrar no ReAct. Quando o grafo retornava vazio ou indisponível, o fluxo ainda
permitia uma resposta textual sem um contrato fechado de evidência. O resultado
observado foi a fabricação de nomes, relações, investidores e recomendações
temporais que não estavam no snapshot atual.

## Causa-raiz

O modo profile-first controlava as ferramentas disponíveis, mas não controlava
deterministicamente a decisão de executar a estratégia nem a fronteira entre o
payload autoritativo e a síntese final. Histórico e saída livre do agente podiam
ser tratados como contexto factual.

## Contrato de aterramento

O roteamento agora é uma função pura: saudações e perguntas conceituais não
consultam o KG; pedidos estratégicos só entram na rota profile-first com perfil
presente; sem perfil, a resposta é uma orientação honesta. Na rota ativa, o
backend executa exatamente uma `graph_strategy`, sem catálogo, Match, memória ou
pesquisa como fallback.

O payload corrente é validado por um contrato Pydantic fechado. Cada seleção
precisa ter ID, kind, ação e referências de fatos existentes no payload; relações
derivadas não podem ser usadas como fatos. A resposta é renderizada por templates
determinísticos, usando apenas nomes e sinais atuais, e ausência é descrita como
limitação do recorte, nunca como inexistência no mercado. O histórico não é fonte
de nomes, IDs ou relações.

## Temporalidade

Para editais candidatos, os campos atuais são carregados em lote e passam por
`resolve_temporal_read_models()`, com o dia civil de `America/Sao_Paulo`. Apenas
`ACTIVE` pode receber ação de avaliação; `CLOSED` não é recomendado; falha ou
ausência de base temporal resulta em `NEEDS_REVIEW` e na indicação separada de
“validade a confirmar”. Ausência de deadline não é interpretada como fluxo
contínuo.

## Resultados reais

- Testes focados KG-P1D, KG-P1C, Explore, roteamento, golden e temporal:
  `69 passed, 2 skipped`.
- Suíte unitária completa: `2197 passed, 2 skipped`.
- Ruff nos arquivos alterados: `All checks passed!`.
- O golden `iforestal-profile-strategy` passou a proibir os nomes inventados do
  incidente (UFSC, INCT, IPMet, Embrapa, INPE, BNDES, ANP, CNPq e aceleradora
  2025), além da afirmação de inexistência de mercado.
- Sync e streaming usam o mesmo resultado validado; o streaming só emite o
  bloco final seguro na rota estratégica.
- Frontend: `npm run lint` não iniciou porque `next` não está instalado nesta
  worktree; `npx tsc --noEmit` não concluiu sem resolução de dependências. Não
  houve instalação nem acesso de rede.

## Limites

O KG-P1D não cria status ou deadline no snapshot da Fase 1; ele apenas consulta
os campos temporais gold e reutiliza o read model canônico. A resposta continua
limitada à geração corrente e às evidências efetivamente presentes nela.

Auditoria Codex: pendente

Commit documental: 21592b094
