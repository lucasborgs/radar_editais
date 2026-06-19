"""Camada web canônica do Radar — fetch HTTP + busca, com cache TTL.

Toda leitura de página web (perfil, deep_research, Descoberta) deve passar por
`core.web.fetch`. Centraliza limpeza de HTML (texto que o LLM vê), truncamento
parametrizado por chamador e cache TTL (evita pagar a mesma URL/busca de novo).
"""
