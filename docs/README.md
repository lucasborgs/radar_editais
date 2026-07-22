# Documentação — Radar de Editais

Este é o índice de leitura do sistema. Ele aponta para a fonte autoritativa de
cada assunto; não substitui os documentos indicados.

## Comece pela sua pergunta

| Quero entender… | Leia | Autoridade |
|---|---|---|
| o produto e como iniciá-lo | [`README.md`](../README.md) | proposta e início rápido |
| setup, comandos, validação e cuidados de manutenção | [`AGENTS.md`](../AGENTS.md) | runbook técnico |
| runtime, dados, agentes, deploy e avaliação | [`architecture.md`](architecture.md) | arquitetura implementada |
| vocabulários, ingestão e regras por fonte | [`domain/schema.md`](domain/schema.md) e [`domain/sources/`](domain/sources/) | domínio lido pelo código |
| trabalho técnico adiado com evidência | [`BACKLOG.md`](BACKLOG.md) | backlog técnico atual |
| mudanças propostas ou contratos vigentes | [`specs/`](specs/) | specs correntes |
| planos e relatórios de implementação ativos | [`execution/`](execution/) | execução das specs, sem redefinir seus contratos |
| operação e subsistemas atuais | [`reference/`](reference/) | referência derivada |
| decisões e arquiteturas anteriores | [`historical/`](historical/) | registro sem autoridade atual |
| contrato visual do frontend | [`DESIGN_SYSTEM.md`](../frontend/DESIGN_SYSTEM.md) | design system implementado |

## Trilhas de leitura

### Produto

1. [`README.md`](../README.md)
2. [`system-coherence.md`](specs/system-coherence.md)
3. specs correntes da capacidade de interesse em [`specs/`](specs/)

### Técnico

1. [`AGENTS.md`](../AGENTS.md)
2. [`architecture.md`](architecture.md)
3. [`domain/schema.md`](domain/schema.md)
4. referências e specs do subsistema afetado

### Operador

1. comandos e configuração em [`AGENTS.md`](../AGENTS.md)
2. deploy e jobs em [`architecture.md`](architecture.md)
3. operação da Descoberta em
   [`discovery-operations.md`](specs/discovery-operations.md)
4. gates externos adiados em [`BACKLOG.md`](BACKLOG.md)

## Specs correntes

| Spec | Status | Função |
|---|---|---|
| [`system-coherence.md`](specs/system-coherence.md) | vigente | propósito, capacidades e invariantes globais |
| [`radar-data-trust.md`](specs/radar-data-trust.md) | proposta para aprovação | programa de cobertura, proveniência e qualidade do plano de dados |
| [`radar-data-trust-00-relevance-contract.md`](specs/radar-data-trust-00-relevance-contract.md) | proposta para aprovação | relevância de oportunidades e atores para startups e PMEs tecnológicas |
| [`radar-data-trust-01-provenance.md`](specs/radar-data-trust-01-provenance.md) | proposta para aprovação | evidência rastreável do documento ao gold e às superfícies do produto |
| [`document-authority.md`](specs/document-authority.md) | vigente | autoridade e ciclo de vida documental |
| [`user-mental-model.md`](specs/user-mental-model.md) | vigente | Explorar, Radar e Projetos como modelo mental do produto |
| [`evaluation-operations.md`](specs/evaluation-operations.md) | aprovada; matching candidato | runs reproduzíveis e gates operacionais explícitos |
| [`data-plane-convergence.md`](specs/data-plane-convergence.md) | vigente | caminho canônico de dados e remoção de resíduos sem runtime |
| [`dormant-capabilities.md`](specs/dormant-capabilities.md) | vigente | estado, gates e custo de capacidades experimentais ou congeladas |
| [`v3-unified.md`](specs/v3-unified.md) | vigente | contrato da arquitetura gold v3 |
| [`radar-frontdoor.md`](specs/radar-frontdoor.md) | vigente | entrada e contrato da superfície Radar |
| [`radar-fase-2.md`](specs/radar-fase-2.md) | vigente | filtros, urgência e comparação |
| [`discovery-operations.md`](specs/discovery-operations.md) | vigente | promoção, retry e observabilidade da Descoberta |
| [`crawl4ai-discovery-integration.md`](specs/crawl4ai-discovery-integration.md) | vigente | coletor opcional e seus limites |
| [`durable-source-docs.md`](specs/durable-source-docs.md) | vigente | persistência dos documentos canônicos |
| [`explore-factual-rag.md`](specs/explore-factual-rag.md) | implementada em pré-produção local; promoção pendente | autoridade de versões, RAG factual e síntese enumerativa no Explorar |
| [`environment-parity-isolation.md`](specs/environment-parity-isolation.md) | pré-produção local implementada; staging Cloud adiado | paridade, credenciais e isolamento de local/test/staging/produção |
| [`environment-promotion.md`](runbooks/environment-promotion.md) | vigente | bootstrap, staging e promoção segura entre ambientes |

## Regras de autoridade

- regras de negócio começam em `docs/domain/schema.md`/`docs/domain/sources/`;
- código, migrations e manifests provam o runtime existente, que deve estar
  fielmente descrito em `architecture.md`;
- uma spec expressa intenção até ser implementada e reconciliada;
- referências explicam contratos atuais sem redefini-los; e
- documentos históricos preservam contexto, mas não governam o presente.

O ciclo de vida, os critérios de classificação e o plano de reconciliação estão
na [`spec de autoridade documental`](specs/document-authority.md).

Estados de capacidades opcionais, experimentais e dormentes estão em
[`reference/capability-lifecycle.md`](reference/capability-lifecycle.md).
