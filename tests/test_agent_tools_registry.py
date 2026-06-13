"""Trava de drift do registro público de tools do Cenário B.

`core/llm/agent_tools/__init__.py` se apresenta como o índice das factories de
tools. Este teste garante que toda factory pública `build_*_tools` definida nos
módulos do pacote está exportada em `__all__` — ou, se for intencionalmente
mantida fora (ex.: por ciclo de import), listada na allowlist explícita abaixo
com justificativa. Sem isso, factories novas voltam a "vazar" via late-import e
o registro mente sobre a superfície real (Finding A, docs/specs/00).
"""
import importlib
import inspect
import pkgutil

import core.llm.agent_tools as agent_tools

# Factories deliberadamente NÃO exportadas em __all__. Cada entrada precisa de
# uma justificativa (ciclo de import, helper interno, etc.). Hoje: vazia —
# todas as factories públicas são exportadas. Para adicionar, documente o porquê.
INTERNAL_ALLOWLIST: dict[str, str] = {}


def _iter_package_modules():
    for mod_info in pkgutil.iter_modules(agent_tools.__path__):
        if mod_info.name.startswith("_"):
            continue
        yield importlib.import_module(f"{agent_tools.__name__}.{mod_info.name}")


def _public_factories() -> dict[str, str]:
    """Mapa nome_da_factory -> módulo de origem para todo build_*_tools público."""
    found: dict[str, str] = {}
    for module in _iter_package_modules():
        for name, obj in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("build_") and name.endswith("_tools"):
                # só conta funções definidas no próprio módulo (não re-imports)
                if obj.__module__ == module.__name__:
                    found[name] = module.__name__
    return found


def test_all_public_factories_are_registered():
    factories = _public_factories()
    assert factories, "nenhuma factory build_*_tools encontrada — teste quebrado?"

    exported = set(agent_tools.__all__)
    missing = {
        name: mod
        for name, mod in factories.items()
        if name not in exported and name not in INTERNAL_ALLOWLIST
    }
    assert not missing, (
        "factories build_*_tools fora de __all__ e da allowlist: "
        f"{missing}. Exporte em core/llm/agent_tools/__init__.py ou "
        "adicione à INTERNAL_ALLOWLIST com justificativa."
    )


def test_all_entries_are_importable():
    """Todo nome em __all__ existe de fato no namespace do pacote."""
    for name in agent_tools.__all__:
        assert hasattr(agent_tools, name), f"{name} em __all__ mas ausente do pacote"


def test_allowlist_has_no_dead_entries():
    """Allowlist não pode referenciar factories que já não existem."""
    factories = _public_factories()
    dead = set(INTERNAL_ALLOWLIST) - set(factories)
    assert not dead, f"entradas mortas na INTERNAL_ALLOWLIST: {dead}"
