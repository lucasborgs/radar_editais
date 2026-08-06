# Spec filha — Caminhos de inovação e matching por domínio

**Status:** aprovada para implementação · **Data:** 2026-08-04  
**Documento-pai:** [`product-strategy-ecosystem-pathways.md`](product-strategy-ecosystem-pathways.md)

## 1. Objetivo

Representar caminhos de inovação sem reduzir financiamento, crédito, desafios,
programas de desenvolvimento e capacidades tecnológicas a um score universal.

## 2. Comportamento pretendido

Um caminho possui tipo, objetivo, atores, requisitos, canal de acesso, vigência,
próximo passo, evidências e incertezas.

O matching usa critérios próprios:

- financiamento: elegibilidade, instrumento, projeto, prazo e contrapartida;
- crédito: finalidade, maturidade financeira, garantias e pagamento;
- desafio: problema, solução, estágio e formato de participação;
- aceleradora/incubadora: estágio, suporte, contrapartida e programa;
- ICT/laboratório: competência, equipamento, projeto, localização e acesso.

O resultado deve explicar compatibilidades, lacunas e desconhecidos. “Unknown”
não elimina automaticamente uma empresa e afinidade não é promessa de aprovação.

## 3. Jornadas

O sistema suporta tanto:

```text
perfil → projeto → caminhos → lacunas → parceiros → plano de ação
```

quanto:

```text
perfil/intenção → possibilidades → hipótese de projeto → caminhos → brief
```

## 4. Fora de escopo

- um ranking único entre domínios;
- recomendação de investidores;
- submissão ou contato automático;
- inferência de elegibilidade a partir de similaridade textual isolada.

## 5. Critérios de aceite

1. Dois caminhos de tipos diferentes podem ser retornados para a mesma intenção,
   mas com explicações e critérios distintos.
2. A resposta separa fatos confirmados, inferências e informações pendentes.
3. Uma intenção sem projeto pode produzir um brief revisável, sem declarar
   elegibilidade inexistente.
4. A UI apresenta o próximo passo por caminho, e não apenas uma pontuação.
