#!/usr/bin/env python3
"""Smoke offline da jornada sintética P0 (não usa DEMO_MODE nem dados reais)."""
from radar.core.services import eligibility

PROFILE = {"nome": "Aurora IA", "descricao_atividades": "Visão computacional para indústria", "uf": "MG", "tamanho_empresa": "epp"}
OPPORTUNITY = {"constraints": [{"tipo": "sede_uf", "op": "in", "valor": ["MG"]}]}

def main() -> int:
    result = eligibility.evaluate_opportunity(OPPORTUNITY["constraints"], PROFILE)
    assert result["status"] == "elegivel"
    print("demo smoke: ok (perfil sintético, elegibilidade determinística)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
