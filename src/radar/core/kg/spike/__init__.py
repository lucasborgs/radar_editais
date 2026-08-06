"""core/kg/spike/__init__.py — spike KG estrutura-consciente (SPEC.md).

Módulo ISOLADO: escreve apenas no schema `kg_spike`; não altera `public`,
nenhum router/tool/task existente. Habilite a integração Explore com
`KG_SPIKE_ENABLED=1`.
"""
from radar.core.kg.spike import graph_store

__all__ = ["graph_store"]
