# Whitepaper Técnico — CerrAI: Modelo de Predição de Estresse Hídrico e Recomendação de Irrigação

> tipo sugerido na Biblioteca: technical_doc

**Autoria:** Equipe de IA da TerraSense Agritech (Dr. Pedro Almeida, Head de IA)
**Versão do modelo:** CerrAI v3.2 (produção desde fev/2026)

## Visão geral
O **CerrAI** é o núcleo preditivo da plataforma AgriPulse. Ele cumpre duas tarefas: (1) prever o risco de estresse hídrico de uma zona de manejo em uma janela de 7 dias e (2) recomendar a lâmina de irrigação ótima (mm/dia) que minimiza consumo de água sem perda de produtividade.

## Dados e datasets
O modelo é treinado sobre o dataset proprietário **TerraSet-2026**, composto por:
- 4,2 bilhões de leituras de sensores HydroNode (umidade em 3 profundidades, condutividade, temperatura) coletadas em 1.240 fazendas desde 2021;
- 38 meses de imagens Sentinel-2 (bandas NDVI, NDRE, NDWI) sobre 380.000 ha;
- 14.700 amostras de solo com referência laboratorial (potencial matricial, textura, SOC);
- variáveis meteorológicas (estações locais + reanálise ERA5).

O conjunto é particionado por fazenda (não por linha) para evitar vazamento espacial: 70% treino, 15% validação, 15% teste, com 92 fazendas integralmente retidas (hold-out) para avaliação fora de distribuição.

## Arquitetura
- **Recomendação de lâmina:** ensemble LightGBM com 480 features de engenharia (médias móveis, déficit de pressão de vapor acumulado, índice de aridez por zona).
- **Predição de estresse:** rede convolucional temporal (TCN) de 6 blocos dilatados, entrada multivariada de 21 dias, saída de probabilidade diária para 7 dias à frente.

## Acurácia e validação
| Tarefa | Métrica | CerrAI v3.2 | Baseline |
|---|---|---|---|
| Predição de estresse (7 dias) | F1 | **0,91** | 0,68 (limiar de umidade fixo) |
| Predição de estresse | AUC-ROC | **0,95** | 0,74 |
| Recomendação de lâmina | MAE (mm/dia) | **0,8** | 2,3 (FAO-56 padrão) |
| Estimativa de SOC | R² | **0,87** | 0,61 (regressão por NDVI) |

A validação de campo foi conduzida em 18 fazendas independentes (safra 2024/25, cana e soja), comparando talhões manejados pelo CerrAI contra talhões-controle com manejo convencional. Resultado: **28% menos água** e **22% menos N**, sem diferença estatisticamente significativa de produtividade (teste t pareado, p = 0,41).

## Diferencial vs. baseline
O baseline de mercado mais comum (balanço hídrico FAO-56 com coeficiente de cultura fixo) ignora a heterogeneidade intra-talhão. O CerrAI opera por zonas de manejo derivadas de condutividade elétrica e NDRE, reduzindo o erro de recomendação em 65% e habilitando a economia de água documentada. O método de calibração de SOC por fusão de sensores é objeto da patente BR 10 2024 015882-3.
