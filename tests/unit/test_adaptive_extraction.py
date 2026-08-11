from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from radar.core.ingestion.adaptive_extraction import (
    AdaptiveDocumentExtraction,
    _consolidate_text_claims,
    document_assets_from_blocks,
    extract_initial_family,
)
from radar.core.kg.gold import _run_adaptive_shadow
from radar.core.services.document_extractions import ExtractionStorageError
from radar.domain.adaptive_extraction import (
    DocumentAsset,
    ExtractionRoute,
    ExtractionStatus,
    ExtractionTarget,
    TextUnit,
    extraction_fingerprint,
)
from radar.domain.provenance import FactState, LocatorQuality

pytestmark = pytest.mark.unit


class MemoryRepository:
    def __init__(self):
        self.items = {}
        self.loads = 0
        self.saves = 0

    def load(self, fingerprint):
        self.loads += 1
        attempts = self.items.get(fingerprint, [])
        healthy = next((item for item in reversed(attempts) if item.status.value in {"complete", "partial"}), None)
        return healthy or (attempts[-1] if attempts else None)

    def load_attempt(self, fingerprint, attempt_id):
        attempts = self.items.get(fingerprint, [])
        return next((item for item in attempts if item.attempt_id == attempt_id), None)

    def save(self, artifact):
        self.saves += 1
        self.items.setdefault(artifact.fingerprint, []).append(artifact)
        return True


class CanonicalHealthyRepository(MemoryRepository):
    def save(self, artifact):
        attempts = self.items.setdefault(artifact.fingerprint, [])
        if artifact.status.value in {"complete", "partial"} and any(
            item.status.value in {"complete", "partial"} for item in attempts
        ):
            return True
        attempts.append(artifact)
        return True


def _document(text: str = "Podem participar empresas brasileiras.") -> DocumentAsset:
    return DocumentAsset(
        subject_id="finep:1",
        source="finep",
        doc_name="edital.pdf",
        text_units=[TextUnit(text=text, document="edital.pdf", page=2, block_idx=0)],
    )


def _targets():
    return [ExtractionTarget(
        field_path="eligible_entities", value_type="list[str]",
        required_for="eligibility", criticality="decision",
    )]


def _constraint_target():
    return [ExtractionTarget(
        field_path="eligibility_constraints", value_type="list[constraint]",
        required_for="eligibility", criticality="decision",
    )]


def _family_targets(*fields):
    return [
        ExtractionTarget(field_path=field, value_type="list[str]", required_for="eligibility")
        for field in fields
    ]


def test_document_asset_hash_e_fingerprint_sao_deterministic():
    document = _document()
    assert document.asset_hash and document.asset_hash.startswith("sha256:")
    first = extraction_fingerprint(document, _targets(), producer_versions={"text": "v1"})
    second = extraction_fingerprint(document, _targets(), producer_versions={"text": "v1"})
    assert first == second and first.startswith("sha256:")
    assert extraction_fingerprint(document, _targets(), producer_versions={"text": "v2"}) != first


def test_fingerprint_dos_targets_ignora_ordem_e_preserva_contrato_completo():
    first_target = ExtractionTarget(
        field_path="deadline", value_type="date", required_for="eligibility",
    )
    second_target = ExtractionTarget(
        field_path="deadline", value_type="date", required_for="writing",
        criticality="decision",
    )

    first = extraction_fingerprint(_document(), [first_target, second_target])
    second = extraction_fingerprint(_document(), [second_target, first_target])

    assert first == second


def test_produtor_textual_respeita_backend_e_modelo_configurados(monkeypatch):
    calls = []

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": {
                            "deadline": {
                                "value": "2026-12-31", "state": "stated",
                                "evidence": "Prazo final: 2026-12-31.",
                            },
                        }}))
                    )])

    client_kwargs = []

    def make_client(**kwargs):
        client_kwargs.append(kwargs)
        return FakeClient()

    monkeypatch.setenv("LLM_BACKEND", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-test")
    monkeypatch.setattr("radar.core.llm.llm_client.make_client", make_client)

    target = ExtractionTarget(
        field_path="deadline", value_type="date", required_for="eligibility",
    )
    output = extract_initial_family(
        _document("Prazo final: 2026-12-31."), [target],
    )

    assert output["deadline"]["state"] == "stated"
    assert client_kwargs == [{
        "api_key": "test-key",
        "max_retries": 6,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
    }]
    assert calls[0]["model"] == "gemini-test"


def test_textual_route_resolves_quote_and_persists_without_raw_text():
    repository = MemoryRepository()
    calls = []

    def extractor(document, targets):
        calls.append((document.subject_id, tuple(target.field_path for target in targets)))
        return {
            "eligible_entities": {
                "value": ["empresas"],
                "state": "stated",
                "evidence": "Podem participar empresas brasileiras.",
            }
        }

    artifact = AdaptiveDocumentExtraction(
        repository=repository, text_extractor=extractor,
    ).extract(_document(), _targets())

    assert artifact.status is ExtractionStatus.COMPLETE
    claim = artifact.claims[0]
    assert claim.provenance.state is FactState.STATED
    assert claim.provenance.evidence_refs[0].locator_quality is LocatorQuality.EXACT
    assert "text" not in artifact.structured_blocks[0]
    assert calls == [("finep:1", ("eligible_entities",))]


def test_produtor_textual_canonico_usa_cliente_na_fronteira_e_retorna_payload_tipado():
    calls = []

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    content = json.dumps({"claims": {
                            "eligibility_constraints": {
                                "value": [{"tipo": "porte", "op": "in", "valor": ["me"]}],
                                "state": "stated",
                                "evidence": "Podem participar empresas brasileiras.",
                            },
                            "requirements": {
                                "value": ["empresas"], "state": "stated",
                                "evidence": "Podem participar empresas brasileiras.",
                            },
                            "exclusions": {"value": None, "state": "absent", "evidence": None},
                            "eligible_entities": {
                                "value": ["empresas"], "state": "stated",
                                "evidence": "Podem participar empresas brasileiras.",
                            },
                            "publico_alvo": {"value": None, "state": "absent", "evidence": None},
                        }}, ensure_ascii=False)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=content)
                    )])

    targets = [
        ExtractionTarget(field_path=field, value_type="list[str]", required_for="eligibility")
        for field in [
            "eligibility_constraints", "requirements", "exclusions",
            "eligible_entities", "publico_alvo",
        ]
    ]
    output = extract_initial_family(_document(), targets, client=FakeClient())
    assert calls and calls[0]["response_format"] == {"type": "json_object"}
    assert "Podem participar empresas brasileiras." in calls[0]["messages"][1]["content"]
    assert output["eligibility_constraints"]["value"] == [
        {"tipo": "porte", "op": "in", "valor": ["me"]}
    ]
    assert output["requirements"]["state"] == "stated"
    assert output["exclusions"] == {"value": None, "state": "absent", "evidence": None}


def test_produtor_textual_omite_target_e_retorna_unknown():
    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": {
                            "eligible_entities": {
                                "value": ["empresas"],
                                "state": "stated",
                                "evidence": "Podem participar empresas brasileiras.",
                            },
                        }}))
                    )])

    output = extract_initial_family(
        _document(),
        _family_targets("eligible_entities", "exclusions"),
        client=FakeClient(),
    )

    assert output["exclusions"] == {
        "value": None, "state": "unknown", "evidence": None,
    }


def test_consolidacao_preserva_candidato_inferred_para_revisao():
    output = _consolidate_text_claims([
        {
            "eligible_entities": {
                "value": ["empresas"],
                "state": "inferred",
                "evidence": None,
            },
        },
    ], ["eligible_entities"])

    assert output["eligible_entities"] == {
        "value": ["empresas"],
        "state": "inferred",
        "evidence": None,
    }


def test_produtor_unificado_retorna_todas_as_familias_em_uma_resposta():
    calls = []
    evidence = "O edital recebe propostas em fluxo contínuo até 31/12/2026."
    fields = [
        "eligibility_constraints", "requirements", "exclusions",
        "eligible_entities", "publico_alvo", "deadline",
        "submission_window", "continuous_flow", "funding_amount",
        "funding_limits", "counterpart", "table_references",
    ]

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    claims = {
                        "eligibility_constraints": {
                            "value": [{"tipo": "porte", "op": "in", "valor": ["me"]}],
                            "state": "stated", "evidence": evidence,
                        },
                        "requirements": {
                            "value": ["CNPJ regular"], "state": "stated", "evidence": evidence,
                        },
                        "exclusions": {
                            "value": ["pessoa física"], "state": "stated", "evidence": evidence,
                        },
                        "eligible_entities": {
                            "value": ["empresa"], "state": "stated", "evidence": evidence,
                        },
                        "publico_alvo": {
                            "value": ["empresa brasileira"], "state": "stated", "evidence": evidence,
                        },
                        "deadline": {
                            "value": "2026-12-31", "state": "stated", "evidence": evidence,
                        },
                        "submission_window": {
                            "value": {"start": "2026-09-01", "end": "2026-12-31"},
                            "state": "stated", "evidence": evidence,
                        },
                        "continuous_flow": {
                            "value": True, "state": "stated", "evidence": evidence,
                        },
                        "funding_amount": {
                            "value": {"currency": "BRL", "min": 1000, "max": 5000},
                            "state": "stated", "evidence": evidence,
                        },
                        "funding_limits": {
                            "value": {"currency": "BRL", "per_project": 3000},
                            "state": "stated", "evidence": evidence,
                        },
                        "counterpart": {
                            "value": {
                                "required": True, "percentage": 20, "base": "valor total",
                            },
                            "state": "stated", "evidence": evidence,
                        },
                        "table_references": {
                            "value": [{
                                "document": "edital.pdf", "title": "Tabela 1",
                                "page": 4, "purpose": "limites de apoio",
                            }],
                            "state": "stated", "evidence": evidence,
                        },
                    }
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": claims}))
                    )])

    document = _document(evidence)
    target_types = {
        "eligibility_constraints": "list[constraint]",
        "requirements": "list[str]",
        "exclusions": "list[str]",
        "eligible_entities": "list[str]",
        "publico_alvo": "list[str]",
        "deadline": "date",
        "submission_window": "submission_window",
        "continuous_flow": "bool",
        "funding_amount": "monetary_range",
        "funding_limits": "funding_limits",
        "counterpart": "counterpart",
        "table_references": "list[table_reference]",
    }
    targets = [ExtractionTarget(
        field_path=field, value_type=target_types[field], required_for="eligibility",
    ) for field in fields]
    output = extract_initial_family(document, targets, client=FakeClient())

    assert len(calls) == 1
    assert json.loads(calls[0]["messages"][1]["content"])["targets"] == fields
    assert output["deadline"]["value"] == "2026-12-31"
    assert output["funding_limits"]["value"]["per_project"] == 3000
    assert output["counterpart"]["value"]["percentage"] == 20
    assert output["table_references"]["value"][0]["title"] == "Tabela 1"
    system_prompt = calls[0]["messages"][0]["content"]
    assert "a qualquer momento" in system_prompt
    assert "Nunca combine" in system_prompt
    assert "proposição factual" in system_prompt


def test_produtor_mantem_prazo_unknown_e_nao_inventa_fluxo_continuo():
    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": {
                            "deadline": {"value": None, "state": "unknown", "evidence": None},
                            "continuous_flow": {"value": True, "state": "stated", "evidence": None},
                        }}))
                    )])

    targets = [
        ExtractionTarget(field_path="deadline", value_type="date", required_for="eligibility"),
        ExtractionTarget(field_path="continuous_flow", value_type="bool", required_for="eligibility"),
    ]
    output = extract_initial_family(
        _document("O edital recebe propostas."), targets, client=FakeClient(),
    )

    assert output["deadline"] == {"value": None, "state": "unknown", "evidence": None}
    assert output["continuous_flow"] == {"value": None, "state": "unknown", "evidence": None}


def test_tabela_com_estrutura_perdida_vira_unknown():
    document = DocumentAsset(
        subject_id="finep:1", source="finep", doc_name="edital.pdf",
        text_units=[TextUnit(
            text="Tabela 1: valores por projeto", document="edital.pdf", page=4,
            block_idx=0, table_structure_lost=True,
        )],
    )
    target = ExtractionTarget(
        field_path="table_references", value_type="list[table_reference]",
        required_for="eligibility", criticality="decision",
    )
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"table_references": {
            "value": [{
                "document": "edital.pdf", "title": "Tabela 1", "page": 4,
                "purpose": "valores por projeto",
            }],
            "state": "stated", "evidence": "Tabela 1: valores por projeto",
        }},
    ).extract(document, [target])

    assert artifact.status is ExtractionStatus.PARTIAL
    assert artifact.claims[0].provenance.state is FactState.UNKNOWN


def test_quote_igual_a_titulo_de_secao_vira_unknown():
    document = DocumentAsset(
        subject_id="finep:1", source="finep", doc_name="edital.pdf",
        text_units=[TextUnit(
            text="Critérios de elegibilidade", document="edital.pdf", page=1,
            block_idx=0, section_path=["Critérios de elegibilidade"],
        )],
    )
    target = ExtractionTarget(
        field_path="eligible_entities", value_type="list[str]",
        required_for="eligibility",
    )
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"eligible_entities": {
            "value": ["empresas"], "state": "stated",
            "evidence": "Critérios de elegibilidade",
        }},
    ).extract(document, [target])

    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert any(
        validation.name == "evidence_substantive"
        and validation.status == "failed"
        for validation in artifact.claims[0].provenance.validations
    )


def test_lista_aceita_multiplas_quotes_e_cria_multiplas_evidencias():
    document = DocumentAsset(
        subject_id="finep:1", source="finep", doc_name="edital.pdf",
        text_units=[
            TextUnit(
                text="Podem participar empresas brasileiras.",
                document="edital.pdf", page=1, block_idx=0,
            ),
            TextUnit(
                text="Também são elegíveis startups de base tecnológica.",
                document="edital.pdf", page=2, block_idx=1,
            ),
        ],
    )
    target = ExtractionTarget(
        field_path="eligible_entities", value_type="list[str]",
        required_for="eligibility",
    )
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"eligible_entities": {
            "value": ["empresas brasileiras", "startups de base tecnológica"],
            "state": "stated",
            "evidence": [
                "Podem participar empresas brasileiras.",
                "Também são elegíveis startups de base tecnológica.",
            ],
        }},
    ).extract(document, [target])

    claim = artifact.claims[0]
    assert claim.provenance.state is FactState.STATED
    assert len(claim.provenance.evidence_refs) == 2


def test_lista_com_item_sem_quote_fica_unknown():
    target = ExtractionTarget(
        field_path="publico_alvo", value_type="list[str]",
        required_for="eligibility",
    )
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"publico_alvo": {
            "value": ["empresas", "universidades"],
            "state": "stated",
            "evidence": ["O público-alvo inclui empresas."],
        }},
    ).extract(_document("O público-alvo inclui empresas."), [target])

    claim = artifact.claims[0]
    assert claim.provenance.state is FactState.UNKNOWN
    assert claim.value is None


def test_documento_longo_e_processado_em_uma_execucao_do_produtor():
    calls = []
    quote = "Prazo final: 2026-12-31."
    document = DocumentAsset(
        subject_id="finep:1", source="finep", doc_name="edital.pdf",
        text_units=[
            TextUnit(
                text=quote + (" conteúdo" * 6000), document="edital.pdf", page=1, block_idx=0,
                section_path=["1"],
            ),
            TextUnit(
                text="Informações administrativas.", document="edital.pdf", page=2,
                block_idx=1, section_path=["2"],
            ),
        ],
    )

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    calls.append(json.loads(kwargs["messages"][1]["content"])["text"])
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": {
                            "deadline": {
                                "value": "2026-12-31", "state": "stated", "evidence": quote,
                            },
                        }}))
                    )])

    target = ExtractionTarget(field_path="deadline", value_type="date", required_for="eligibility")
    output = extract_initial_family(document, [target], client=FakeClient())

    assert len(calls) == 1
    assert output["deadline"]["state"] == "stated"
    assert output["deadline"]["value"] == "2026-12-31"


def test_expansao_textual_nao_aciona_rotas_multimodais():
    calls = []
    quote = "Prazo final: 2026-12-31."

    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    calls.append(kwargs)
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": {
                            "deadline": {
                                "value": "2026-12-31", "state": "stated", "evidence": quote,
                            },
                        }}))
                    )])

    target = ExtractionTarget(field_path="deadline", value_type="date", required_for="eligibility")
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(), llm_client=FakeClient(),
    ).extract(_document(quote), [target])

    assert len(calls) == 1
    assert [trace.route for trace in artifact.route_trace] == [ExtractionRoute.TEXT]


def test_selecao_parcial_de_documento_nao_produz_absent():
    class FakeClient:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    prompt = json.loads(kwargs["messages"][1]["content"])
                    state = "unknown" if "Prazo ainda" in prompt["text"] else "absent"
                    return SimpleNamespace(choices=[SimpleNamespace(
                        message=SimpleNamespace(content=json.dumps({"claims": {
                            "deadline": {"value": None, "state": state, "evidence": None},
                        }}))
                    )])

    document = DocumentAsset(
        subject_id="finep:1", source="finep", doc_name="edital.pdf",
        text_units=[
            TextUnit(
                text="Seção de elegibilidade.", document="edital.pdf", page=1,
                block_idx=0, section_path=["1"],
            ),
            TextUnit(
                text="Prazo ainda não analisado.", document="edital.pdf", page=2,
                block_idx=1, section_path=["2"],
            ),
        ],
    )
    target = ExtractionTarget(field_path="deadline", value_type="date", required_for="eligibility")
    output = extract_initial_family(document, [target], client=FakeClient())

    assert output["deadline"]["state"] == "unknown"


def test_cache_hit_nao_chama_extrator_novamente():
    repository = MemoryRepository()
    calls = 0

    def extractor(document, targets):
        nonlocal calls
        calls += 1
        return {"eligible_entities": {"value": ["empresas"], "state": "absent"}}

    service = AdaptiveDocumentExtraction(repository=repository, text_extractor=extractor)
    first = service.extract(_document(), _targets())
    second = service.extract(_document(), _targets())
    assert first.fingerprint == second.fingerprint
    assert calls == 1
    assert repository.saves == 1


def test_quote_ausente_vira_unknown_e_observa_rt05():
    repository = MemoryRepository()
    exceptions = []

    def extractor(document, targets):
        return {"eligible_entities": {
            "value": ["empresas"], "state": "stated", "evidence": "não existe"
        }}

    artifact = AdaptiveDocumentExtraction(
        repository=repository, text_extractor=extractor,
        exception_sink=exceptions.append,
    ).extract(_document(), _targets())

    assert artifact.status is ExtractionStatus.PARTIAL
    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert exceptions and exceptions[0].field_path == "eligible_entities"


def test_stated_sem_evidencia_vira_unknown_sem_falhar_artifact():
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"eligible_entities": {
            "value": ["empresas"], "state": "stated", "evidence": None,
        }},
    ).extract(_document(), _targets())

    assert artifact.status is ExtractionStatus.PARTIAL
    assert artifact.claims[0].value is None
    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert any(
        validation.name == "list_item_evidence"
        and validation.status == "failed"
        for validation in artifact.claims[0].provenance.validations
    )


def test_claim_valido_nao_e_bloqueado_por_irmao_sem_evidencia():
    targets = [
        ExtractionTarget(
            field_path="eligible_entities", value_type="list[str]",
            required_for="eligibility",
        ),
        ExtractionTarget(
            field_path="requirements", value_type="list[str]",
            required_for="eligibility",
        ),
    ]
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {
            "eligible_entities": {
                "value": ["empresas"], "state": "stated", "evidence": None,
            },
            "requirements": {
                "value": ["empresas brasileiras"], "state": "stated",
                "evidence": "Podem participar empresas brasileiras.",
            },
        },
    ).extract(_document(), targets)

    claims = {claim.field_path: claim for claim in artifact.claims}
    assert artifact.status is ExtractionStatus.PARTIAL
    assert claims["eligible_entities"].provenance.state is FactState.UNKNOWN
    assert claims["requirements"].provenance.state is FactState.STATED
    assert claims["requirements"].value == ["empresas brasileiras"]


def test_estado_malformado_vira_unknown_sem_bloquear_outro_claim():
    targets = [
        ExtractionTarget(
            field_path="eligible_entities", value_type="list[str]",
            required_for="eligibility",
        ),
        ExtractionTarget(
            field_path="requirements", value_type="list[str]",
            required_for="eligibility",
        ),
    ]
    exceptions = []
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        exception_sink=exceptions.append,
        text_extractor=lambda document, targets: {
            "eligible_entities": {
                "value": ["empresas"], "state": "not-a-state",
                "evidence": "Podem participar empresas brasileiras.",
            },
            "requirements": {
                "value": ["empresas brasileiras"], "state": "stated",
                "evidence": "Podem participar empresas brasileiras.",
            },
        },
    ).extract(_document(), targets)

    claims = {claim.field_path: claim for claim in artifact.claims}
    assert artifact.status is ExtractionStatus.PARTIAL
    assert claims["eligible_entities"].provenance.state is FactState.UNKNOWN
    assert claims["eligible_entities"].value is None
    assert claims["requirements"].provenance.state is FactState.STATED
    assert exceptions and exceptions[0].issue_code.value == "validation_failed"


def test_falha_de_extracao_e_status_observavel():
    repository = MemoryRepository()

    def extractor(document, targets):
        raise RuntimeError("simulated")

    artifact = AdaptiveDocumentExtraction(
        repository=repository, text_extractor=extractor,
    ).extract(_document(), _targets())

    assert artifact.status is ExtractionStatus.FAILED
    assert artifact.unresolved_targets == ["eligible_entities"]


def test_constraints_legacy_nao_vazam_e_formato_canonico_e_validado():
    legacy = {"type": "region", "description": "SC", "state": "stated", "evidence": "SC"}
    legacy_artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"constraints": {"value": [legacy], "state": "stated", "evidence": "Podem participar empresas brasileiras."}},
    ).extract(_document("Podem participar empresas brasileiras."), _constraint_target())
    assert legacy_artifact.claims[0].value is None
    assert legacy_artifact.claims[0].provenance.state is FactState.UNKNOWN

    valid_artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"constraints": {
            "value": [{"tipo": "porte", "op": "in", "valor": ["me"]}],
            "state": "stated", "evidence": "Podem participar empresas brasileiras.",
        }},
    ).extract(_document("Podem participar empresas brasileiras."), _constraint_target())
    assert valid_artifact.claims[0].value == [{"tipo": "porte", "op": "in", "valor": ["me"]}]
    assert valid_artifact.claims[0].provenance.state is FactState.STATED


def test_constraints_falham_fechado_se_schema_indisponivel(monkeypatch):
    from radar.core.kg import schema

    monkeypatch.setattr(schema, "constraint_tipos", lambda: (_ for _ in ()).throw(RuntimeError("schema")))
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"constraints": {
            "value": [{"tipo": "porte", "op": "in", "valor": ["me"]}],
            "state": "stated", "evidence": "Podem participar empresas brasileiras.",
        }},
    ).extract(_document("Podem participar empresas brasileiras."), _constraint_target())
    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert artifact.status is ExtractionStatus.PARTIAL


def test_constraints_nao_coercionam_numero_em_string():
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"constraints": {
            "value": [{"tipo": "idade_empresa_meses", "op": "gte", "valor": "12"}],
            "state": "stated", "evidence": "A empresa deve ter 12 meses.",
        }},
    ).extract(_document("A empresa deve ter 12 meses."), _constraint_target())

    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert artifact.claims[0].value is None


def test_tipo_de_alvo_invalido_nao_bloqueia_alvo_irmao():
    targets = [
        ExtractionTarget(
            field_path="eligible_entities", value_type="date",
            required_for="eligibility",
        ),
        ExtractionTarget(
            field_path="requirements", value_type="list[str]",
            required_for="eligibility",
        ),
    ]
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {
            "eligible_entities": {
                "value": ["empresas"], "state": "stated",
                "evidence": "Podem participar empresas brasileiras.",
            },
            "requirements": {
                "value": ["empresas brasileiras"], "state": "stated",
                "evidence": "Podem participar empresas brasileiras.",
            },
        },
    ).extract(_document(), targets)

    claims = {claim.field_path: claim for claim in artifact.claims}
    assert claims["eligible_entities"].provenance.state is FactState.UNKNOWN
    assert claims["eligible_entities"].value is None
    assert claims["requirements"].provenance.state is FactState.STATED


@pytest.mark.parametrize("state", ["inferred", "unknown", "conflicting"])
def test_estados_nao_stated_nunca_resolvem_alvo(state):
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"eligible_entities": {
            "value": ["empresas"] if state != "absent" else None,
            "state": state,
            "evidence": "Podem participar empresas brasileiras." if state != "absent" else None,
        }},
    ).extract(_document(), _targets())
    assert artifact.claims[0].provenance.state.value == state
    if state in {"unknown", "conflicting"}:
        assert artifact.claims[0].value is None
    else:
        assert artifact.claims[0].value == ["empresas"]
    assert artifact.status is ExtractionStatus.PARTIAL


def test_absent_explicito_resolve_alvo():
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"eligible_entities": {
            "value": None, "state": "absent", "evidence": None,
        }},
    ).extract(_document(), _targets())
    assert artifact.claims[0].provenance.state is FactState.ABSENT
    assert artifact.claims[0].value is None
    assert artifact.unresolved_targets == []
    assert artifact.status is ExtractionStatus.COMPLETE


def test_todos_targets_explicitamente_absent_produzem_complete():
    targets = _family_targets("requirements", "exclusions", "publico_alvo")
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {
            field: {"value": None, "state": "absent", "evidence": None}
            for field in ("requirements", "exclusions", "publico_alvo")
        },
    ).extract(_document(), targets)
    assert all(claim.provenance.state is FactState.ABSENT for claim in artifact.claims)
    assert artifact.unresolved_targets == []
    assert artifact.status is ExtractionStatus.COMPLETE


def test_stated_e_absent_produzem_complete():
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {
            "eligible_entities": {
                "value": ["empresas"], "state": "stated",
                "evidence": "Podem participar empresas brasileiras.",
            },
            "exclusions": {"value": None, "state": "absent", "evidence": None},
        },
    ).extract(_document(), _family_targets("eligible_entities", "exclusions"))
    assert artifact.status is ExtractionStatus.COMPLETE
    assert artifact.unresolved_targets == []
    assert [claim.provenance.state for claim in artifact.claims] == [
        FactState.STATED, FactState.ABSENT,
    ]


def test_target_omitido_vira_unknown_e_partial():
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {},
    ).extract(_document(), _targets())
    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert artifact.claims[0].value is None
    assert artifact.unresolved_targets == ["eligible_entities"]
    assert artifact.status is ExtractionStatus.PARTIAL


def test_chave_de_contexto_legada_nao_fabrica_requisitos():
    artifact = AdaptiveDocumentExtraction(
        repository=MemoryRepository(),
        text_extractor=lambda document, targets: {"key_requirements": ["CNPJ regular"]},
    ).extract(_document(), [ExtractionTarget(
        field_path="requirements", value_type="list[str]",
        required_for="eligibility", criticality="decision",
    )])
    assert artifact.claims[0].provenance.state is FactState.UNKNOWN
    assert artifact.claims[0].value is None


def test_persistencia_false_nao_expoe_artifact_como_duravel():
    class NoPersistence:
        def save(self, artifact):
            return False

        def load(self, fingerprint):
            return None

    with pytest.raises(ExtractionStorageError, match="durably persisted"):
        AdaptiveDocumentExtraction(
            repository=NoPersistence(),
            text_extractor=lambda document, targets: {
                "eligible_entities": {
                    "value": ["empresas"], "state": "stated",
                    "evidence": "Podem participar empresas brasileiras.",
                }
            },
        ).extract(_document(), _targets())


def test_sem_persistencia_duravel_nao_chama_llm(monkeypatch):
    monkeypatch.setattr("radar.core.services.document_extractions.is_configured", lambda: False)
    monkeypatch.setattr(
        "radar.core.ingestion.adaptive_extraction.extract_initial_family",
        lambda *args, **kwargs: pytest.fail("LLM route should not run without durable persistence"),
    )
    with pytest.raises(ExtractionStorageError, match="durable persistence"):
        AdaptiveDocumentExtraction().extract(_document(), _targets())


def test_retry_de_failed_nao_fica_preso_no_fingerprint():
    repository = MemoryRepository()
    attempts = 0

    def extractor(document, targets):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("temporário")
        return {"eligible_entities": {
            "value": ["empresas"], "state": "stated",
            "evidence": "Podem participar empresas brasileiras.",
        }}

    service = AdaptiveDocumentExtraction(repository=repository, text_extractor=extractor)
    assert service.extract(_document(), _targets()).status is ExtractionStatus.FAILED
    retried = service.extract(_document(), _targets())
    assert retried.status is ExtractionStatus.COMPLETE
    assert attempts == 2
    stored = repository.items[retried.fingerprint]
    assert [artifact.status for artifact in stored] == [
        ExtractionStatus.FAILED, ExtractionStatus.COMPLETE,
    ]
    assert stored[0].attempt_id != stored[1].attempt_id


def test_duas_falhas_do_mesmo_fingerprint_sao_append_only():
    repository = MemoryRepository()
    service = AdaptiveDocumentExtraction(
        repository=repository,
        text_extractor=lambda document, targets: (_ for _ in ()).throw(RuntimeError("temporário")),
    )

    first = service.extract(_document(), _targets())
    second = service.extract(_document(), _targets())

    stored = repository.items[first.fingerprint]
    assert first.status is ExtractionStatus.FAILED
    assert second.status is ExtractionStatus.FAILED
    assert len(stored) == 2
    assert stored[0].attempt_id != stored[1].attempt_id


def test_attempt_saudavel_perdedor_retorna_vencedor_canonico():
    repository = CanonicalHealthyRepository()
    service = AdaptiveDocumentExtraction(repository=repository)
    first = service._artifact(
        _document(),
        _targets(),
        extraction_fingerprint(_document(), _targets()),
        claims=[],
        unresolved=[],
        traces=[],
        status=ExtractionStatus.COMPLETE,
    )
    second = first.model_copy(update={"attempt_id": "loser"})

    winner = service._persist(first)
    result = service._persist(second)

    stored = repository.items[first.fingerprint]
    assert len([item for item in stored if item.status is ExtractionStatus.COMPLETE]) == 1
    assert winner.attempt_id == first.attempt_id
    assert result.attempt_id == winner.attempt_id


def test_attempt_falho_concorrente_continua_diagnosticavel():
    repository = CanonicalHealthyRepository()
    service = AdaptiveDocumentExtraction(repository=repository)
    healthy = service._artifact(
        _document(),
        _targets(),
        extraction_fingerprint(_document(), _targets()),
        claims=[],
        unresolved=[],
        traces=[],
        status=ExtractionStatus.COMPLETE,
    )
    failed = healthy.model_copy(update={
        "attempt_id": "failed-attempt",
        "status": ExtractionStatus.FAILED,
    })

    service._persist(healthy)
    result = service._persist(failed)

    assert result.status is ExtractionStatus.FAILED
    assert result.attempt_id == "failed-attempt"
    assert len(repository.items[healthy.fingerprint]) == 2


def test_schema_produtor_e_targets_mudam_fingerprint_material():
    document = _document()
    first = extraction_fingerprint(
        document, _targets(), schema_version=1, producer_versions={"text": "v1"},
    )
    changed_schema = extraction_fingerprint(
        document, _targets(), schema_version=2, producer_versions={"text": "v1"},
    )
    changed_producer = extraction_fingerprint(
        document, _targets(), schema_version=1, producer_versions={"text": "v2"},
    )
    changed_targets = extraction_fingerprint(
        document, _family_targets("eligible_entities", "exclusions"),
        schema_version=1, producer_versions={"text": "v1"},
    )

    assert len({first, changed_schema, changed_producer, changed_targets}) == 4


def test_retry_de_unavailable_preserva_diagnostico_e_pode_ficar_saudavel():
    repository = MemoryRepository()
    document = DocumentAsset(
        subject_id="finep:1", source="finep", doc_name="edital.pdf", payload=b"",
    )
    service = AdaptiveDocumentExtraction(
        repository=repository,
        text_extractor=lambda document, targets: {"eligible_entities": {
            "value": ["empresas"], "state": "stated",
            "evidence": "Podem participar empresas brasileiras.",
        }},
    )
    unavailable = service.extract(document, _targets())
    assert unavailable.status is ExtractionStatus.UNAVAILABLE
    document.text_units.append(TextUnit(
        text="Podem participar empresas brasileiras.", document="edital.pdf", page=1, block_idx=0,
    ))
    healthy = service.extract(document, _targets())
    assert healthy.status is ExtractionStatus.COMPLETE
    assert len(repository.items[healthy.fingerprint]) == 2


def test_assets_e_artifacts_preservam_identidade_por_documento():
    bundle_hash = "sha256:" + "d" * 64
    blocks = [
        {
            "doc": "base.pdf", "idx": 0, "page": 1, "text": "Base permite empresas.",
            "document_metadata": {
                "content_hash": "sha256:" + "a" * 64,
                "bundle_hash": bundle_hash, "role": "base_notice",
            },
        },
        {
            "doc": "retificacao.pdf", "idx": 0, "page": 2, "text": "Retificação permite startups.",
            "document_metadata": {
                "content_hash": "sha256:" + "b" * 64,
                "bundle_hash": bundle_hash, "role": "amendment",
            },
        },
    ]
    assets = document_assets_from_blocks(
        subject_id="finep:1", source="finep", blocks=blocks,
    )
    assert [asset.doc_name for asset in assets] == ["base.pdf", "retificacao.pdf"]
    assert [asset.asset_hash for asset in assets] == ["sha256:" + "a" * 64, "sha256:" + "b" * 64]
    assert all(asset.bundle_hash == bundle_hash for asset in assets)

    repository = MemoryRepository()
    artifacts = [AdaptiveDocumentExtraction(
        repository=repository,
        text_extractor=lambda document, targets: {
            "eligible_entities": {
                "value": [document.text_units[0].text], "state": "stated", "evidence": document.text_units[0].text,
            }
        },
    ).extract(asset, _targets()) for asset in assets]
    assert [artifact.document for artifact in artifacts] == ["base.pdf", "retificacao.pdf"]
    assert [artifact.bundle_hash for artifact in artifacts] == [bundle_hash, bundle_hash]
    assert [artifact.claims[0].provenance.evidence_refs[0].document for artifact in artifacts] == [
        "base.pdf", "retificacao.pdf",
    ]


def test_wiring_shadow_exige_flag_e_seam_injetavel(monkeypatch):
    calls = []

    class Service:
        def extract(self, document, targets):
            calls.append((document.subject_id, len(targets)))

    document = _document()
    monkeypatch.delenv("RADAR_ADAPTIVE_EXTRACTION_SHADOW", raising=False)
    assert not _run_adaptive_shadow(
        documents=[document], targets=_targets(), extractor_factory=lambda: Service(),
    )
    assert calls == []

    monkeypatch.setenv("RADAR_ADAPTIVE_EXTRACTION_SHADOW", "1")
    assert _run_adaptive_shadow(
        documents=[document], targets=_targets(), extractor_factory=lambda: Service(),
    )
    assert calls == [("finep:1", 1)]
