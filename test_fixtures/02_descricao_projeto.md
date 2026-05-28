# Projeto em Andamento — AgriPulse Edge: Inferência On-Device para Estresse Hídrico em Tempo Real

> tipo sugerido na Biblioteca: project_description

**Empresa:** TerraSense Agritech Ltda., São Carlos/SP
**Período:** jan/2025 – dez/2026 (em execução)
**Orçamento total:** R$ 3.150.000 (recursos próprios + parceria ICT)

## Objetivo
Levar a inferência do modelo **CerrAI** do servidor para a borda (edge), permitindo recomendação de irrigação e detecção precoce de estresse hídrico sem dependência de conectividade contínua — requisito crítico em 34% das fazendas atendidas que ficam em zonas com cobertura celular intermitente.

## Arquitetura da solução
1. **Camada de campo:** sensores **HydroNode S3** + nova versão **HydroNode S4** (adiciona sensor de NDVI de baixo custo e microfone acústico para detecção de bombas com falha). Comunicação LoRaWAN 915 MHz.
2. **Edge gateway:** módulo computacional com NPU (4 TOPS) rodando o **CerrAI-Lite**, versão quantizada (INT8) do modelo principal, com footprint de 11 MB.
3. **Camada de nuvem:** AWS (EKS + S3 + Timestream), pipeline de MLOps com retreino mensal e versionamento de modelo via MLflow.
4. **Aplicação:** painel web e app mobile **AgriPulse**, já em uso por 1.240 fazendas.

## Tecnologias
- Modelos: gradient boosting (LightGBM) para recomendação de lâmina + rede temporal (TCN) para previsão de estresse hídrico em janela de 7 dias.
- Dados: séries temporais de umidade de solo, evapotranspiração (estação meteorológica + reanálise ERA5), imagens Sentinel-2 (10 m), e histórico de manejo.
- Stack: Python, PyTorch, ONNX Runtime (edge), Terraform para infraestrutura.

## KPIs do projeto
- Latência de inferência on-device < 200 ms (meta) — atual: 140 ms.
- Operação offline por até 14 dias sem perda de recomendação.
- Acurácia de detecção de estresse hídrico ≥ 90% (F1).
- Reduzir falsos alarmes de irrigação em 30% vs. versão em nuvem.
- Evolução de **TRL 6 para TRL 8**.

## Parcerias com ICTs
- **Universidade Estadual do Vale do Tietê (UEVT)** — Laboratório de Física do Solo: validação agronômica em 3 estações experimentais (cana, soja, café).
- **Instituto Federal do Centro-Oeste Paulista (IFCOP)** — embarcados e otimização de modelos para NPU.
- Convênio prevê 4 bolsas de mestrado e 2 de iniciação científica.

## Cronograma
- T1 (2025 S1): especificação HydroNode S4 e dataset rotulado de estresse.
- T2 (2025 S2): quantização do CerrAI-Lite e protótipo de edge gateway.
- T3 (2026 S1): piloto em 40 fazendas, 18.000 ha.
- T4 (2026 S2): validação estatística, documentação e pleito de novo depósito de patente.
