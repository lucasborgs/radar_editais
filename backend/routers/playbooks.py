"""
Auditoria de playbooks — playbook resolvido CAMADA POR CAMADA (Item 3).

A competência de escrita é composta em camadas (core/skills.py):
    git base (mechanism/<mech>.md)
      + git source (source/<src>/global.md + source/<src>/<mech>.md)
      + learned overlays do banco (playbook_overlays)  ← reader dormente, sem writer

Este router expõe essas camadas sem mergear para auditoria. No runtime atual não
há produtor de overlays; sem linhas, somente as camadas git são retornadas.
"""
from fastapi import APIRouter

from core.auth import CurrentUserId
from core.skills import resolve_playbook_layers

router = APIRouter(prefix="/playbooks", tags=["playbooks"])


@router.get(
    "/{mechanism}/layers",
    summary="Playbook resolvido camada por camada (git base, git source, learned overlays)",
)
def playbook_layers(
    mechanism: str,
    user_id: CurrentUserId,
    source: str | None = None,
):
    """Auditoria visual: devolve cada camada do playbook separada, na ordem de
    aplicação, sem mergear. Camadas possíveis: `git_base`, `git_source`,
    `learned_overlay`. A leitura de overlays é guardada — sem DB/overlays, só as
    camadas git aparecem.
    """
    mech, src, layers = resolve_playbook_layers(mechanism, source)
    return {
        "mechanism": mech,
        "source": src,
        "layers": [
            {
                "layer": layer.layer,
                "lente": layer.lente,
                "sections": layer.sections,
            }
            for layer in layers
        ],
    }
