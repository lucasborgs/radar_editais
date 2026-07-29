from datetime import date

from radar.domain.provenance import EvidenceRef


def finep_eureka_2024() -> dict:
    """Fixture sanitizada do caso Finep/Eureka (Chamada pública conjunta
    Finep e Rede Eureka 2024).

    Dados derivados do caso real:
      - publicação em 31/01/2024
      - status ABERTA (no portal)
      - prazo ausente (null)
      - sem evidência de continuidade

    HTML integral não é copiado. Nenhuma rede é acessada.
    Resultado esperado: unknown/needs_review/temporal_status_without_basis.
    """
    return {
        "publication_date": date(2024, 1, 31),
        "status": "ABERTA",
        "deadline": None,
        "continuous_evidence": None,
        "closed_status_values": {"encerrada", "resultado_divulgado",
                                 "fechada", "closed", "finished"},
    }
