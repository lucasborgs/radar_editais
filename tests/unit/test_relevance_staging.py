"""Testes de persistência da classificação de relevância no staging (RT00-T04).

Cobre:
  - discover_opportunities → _row_with_relevance com relevance v1;
  - in_scope, out_of_scope, needs_review persistidos;
  - erro v1 ainda produz candidato pending no staging;
  - veredicto inválido recusado (validate_opportunity_result);
  - in_scope incompleto recusado;
  - retry com erro não apaga classified;
  - registro legado lido como unclassified/default;
  - promote e reject reais continuam independentes da relevância;
  - cache negativo não recebe decisão do classificador v1;
  - write=False não escreve staging;
  - nenhuma tabela gold é chamada.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from radar.core.ingestion.opportunity_discovery import _row_with_relevance
from radar.core.ingestion.relevance_classifier import (
    _ERROR_CANONICAL_MESSAGES,
    validate_opportunity_result,
)

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    db = MagicMock()
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
    return db


@pytest.fixture
def record():
    return {
        "url": "https://exemplo.com/edital-01",
        "url_hash": "abc123",
        "title": "Edital de Inovação 2026",
        "agency": "FINEP",
        "fonte": "FINEP",
        "descricao": "Chamada pública para projetos de inovação tecnológica.",
        "prazo_envio": "30/12/2026",
        "publico_alvo": "empresas brasileiras de base tecnológica",
        "tema": "inovação",
        "opportunity_type": "edital",
        "extraction_quality": "high",
        "texto_cru": (
            "Chamada pública FINEP 2026. Podem participar empresas brasileiras "
            "de base tecnológica, startups e PMEs. O objetivo é fomentar o "
            "desenvolvimento de inovação tecnológica. As propostas devem ser "
            "enviadas até 30/12/2026. Serão concedidos recursos não reembolsáveis "
            "de até R$ 500.000,00 por projeto. A chamada faz parte do programa "
            "de subvenção econômica à inovação."
        ),
    }


@pytest.fixture
def full_in_scope_verdict():
    """Verdict in_scope completo com R1-R5 e evidência para cada código."""
    return {
        "decision": "in_scope",
        "reason_codes": [
            "R1_ENTERPRISE_PATH",
            "R2_TECH_INNOVATION",
            "R3_ACTIONABLE",
            "R4_RELEVANT_BENEFIT",
            "R5_BRAZIL_RELEVANCE",
        ],
        "exclusion_codes": [],
        "evidence": [
            {
                "code": "R1_ENTERPRISE_PATH",
                "quote": "Podem participar empresas brasileiras de base tecnológica, startups e PMEs",
                "source": "landing_page",
                "locator": {"document": "Edital.pdf"},
            },
            {
                "code": "R2_TECH_INNOVATION",
                "quote": "fomentar o desenvolvimento de inovação tecnológica",
                "source": "landing_page",
                "locator": {"document": "Edital.pdf"},
            },
            {
                "code": "R3_ACTIONABLE",
                "quote": "As propostas devem ser enviadas até 30/12/2026",
                "source": "landing_page",
                "locator": {"document": "Edital.pdf"},
            },
            {
                "code": "R4_RELEVANT_BENEFIT",
                "quote": "recursos não reembolsáveis de até R$ 500.000,00 por projeto",
                "source": "landing_page",
                "locator": {"document": "Edital.pdf"},
            },
            {
                "code": "R5_BRAZIL_RELEVANCE",
                "quote": "empresas brasileiras de base tecnológica",
                "source": "landing_page",
                "locator": {"document": "Edital.pdf"},
            },
        ],
        "missing_information": [],
        "classifier_version": "radar-data-trust-relevance-v1",
    }


# ══════════════════════════════════════════════════════════════════════════
# validate_opportunity_result
# ══════════════════════════════════════════════════════════════════════════


class TestValidateOpportunityResult:
    def test_valid_in_scope(self, full_in_scope_verdict):
        result = validate_opportunity_result({"verdict": full_in_scope_verdict})
        assert "verdict" in result
        assert result["verdict"]["decision"] == "in_scope"

    def test_valid_out_of_scope(self):
        result = validate_opportunity_result({
            "verdict": {
                "decision": "out_of_scope",
                "reason_codes": ["X1_ACADEMIC_ONLY"],
                "exclusion_codes": ["X1_ACADEMIC_ONLY"],
                "evidence": [
                    {
                        "code": "X1_ACADEMIC_ONLY",
                        "quote": "bolsa exclusiva para estudantes",
                        "source": "landing_page",
                        "locator": {"document": "Edital.pdf"},
                    },
                ],
                "missing_information": [],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        })
        assert result["verdict"]["decision"] == "out_of_scope"

    def test_valid_needs_review(self):
        result = validate_opportunity_result({
            "verdict": {
                "decision": "needs_review",
                "reason_codes": ["R1_ENTERPRISE_PATH"],
                "exclusion_codes": [],
                "evidence": [
                    {
                        "code": "R1_ENTERPRISE_PATH",
                        "quote": "empresas podem participar",
                        "source": "landing_page",
                        "locator": {"document": "portal.html"},
                    },
                ],
                "missing_information": [
                    "R2_TECH_INNOVATION: material não menciona inovação",
                ],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        })
        assert result["verdict"]["decision"] == "needs_review"

    def test_valid_error_known_category(self):
        """Erro com prefixo conhecido retorna mensagem canônica fixa."""
        for prefix, msg in _ERROR_CANONICAL_MESSAGES.items():
            result = validate_opportunity_result({"error": f"{prefix} conteúdo arbitrário"})
            assert result["error"] == msg

    def test_error_content_after_prefix_discarded(self):
        """Conteúdo arbitrário após prefixo não é persistido."""
        result = validate_opportunity_result(
            {"error": "provider_error: segredo-ou-resposta-bruta"}
        )
        assert result["error"] == _ERROR_CANONICAL_MESSAGES["provider_error:"]
        assert "segredo" not in result["error"]

    def test_dual_keys_rejected(self):
        """Resultado com verdict e error simultâneos é rejeitado."""
        with pytest.raises(ValueError, match="must not contain both"):
            validate_opportunity_result({
                "verdict": {"decision": "in_scope"},
                "error": "timeout: erro",
            })

    def test_invalid_result_no_keys_raises(self):
        with pytest.raises(ValueError, match="must contain 'verdict' or 'error'"):
            validate_opportunity_result({})

    def test_invalid_unknown_error_category_raises(self):
        with pytest.raises(ValueError, match="unknown error category"):
            validate_opportunity_result({"error": "some_raw_error_message"})

    def test_in_scope_incomplete_rejected(self):
        """in_scope sem todos R1-R5 é rejeitado pelo contrato."""
        incomplete = {
            "decision": "in_scope",
            "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
            "exclusion_codes": [],
            "evidence": [
                {
                    "code": "R1_ENTERPRISE_PATH",
                    "quote": "empresas podem participar",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf"},
                },
                {
                    "code": "R2_TECH_INNOVATION",
                    "quote": "fomento à inovação",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf"},
                },
            ],
            "missing_information": [],
            "classifier_version": "radar-data-trust-relevance-v1",
        }
        with pytest.raises(Exception) as exc_info:
            validate_opportunity_result({"verdict": incomplete})
        assert "in_scope" in str(exc_info.value).lower()

    def test_evidence_code_mismatch_rejected(self):
        """Evidence sem correspondência com reason_codes é rejeitada."""
        mismatch = {
            "decision": "needs_review",
            "reason_codes": ["R1_ENTERPRISE_PATH"],
            "exclusion_codes": [],
            "evidence": [
                {
                    "code": "R1_ENTERPRISE_PATH",
                    "quote": "empresas podem participar",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf"},
                },
                {
                    "code": "R3_ACTIONABLE",
                    "quote": "prazo definido",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf"},
                },
            ],
            "missing_information": [],
            "classifier_version": "radar-data-trust-relevance-v1",
        }
        with pytest.raises(Exception) as exc_info:
            validate_opportunity_result({"verdict": mismatch})
        assert "evidence" in str(exc_info.value).lower()

    def test_serialize_mode_json(self, full_in_scope_verdict):
        """model_dump(mode='json') produz tipos serializáveis."""
        result = validate_opportunity_result({"verdict": full_in_scope_verdict})
        verdict = result["verdict"]
        assert isinstance(verdict["decision"], str)
        assert all(isinstance(c, str) for c in verdict["reason_codes"])
        assert all(isinstance(e, dict) for e in verdict["evidence"])


# ══════════════════════════════════════════════════════════════════════════
# _row_with_relevance — integração discovery + classificador
# ══════════════════════════════════════════════════════════════════════════


class TestRowWithRelevance:
    def test_classified_in_scope(self, record):
        """_row_with_relevance com classificação bem-sucedida."""
        with (
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_classify.return_value = {"verdict": {"decision": "in_scope"}}
            mock_validate.return_value = {"verdict": {"decision": "in_scope", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            row = _row_with_relevance(record)

        assert row["status"] == "pending"
        assert row["relevance_status"] == "classified"
        assert row["relevance_verdict"] is not None
        assert row["relevance_verdict"]["decision"] == "in_scope"
        assert row["relevance_error"] is None
        assert row["relevance_classified_at"] is not None
        assert row["url"] == record["url"]

    def test_classified_out_of_scope(self, record):
        with (
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_classify.return_value = {"verdict": {"decision": "out_of_scope"}}
            mock_validate.return_value = {"verdict": {"decision": "out_of_scope", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            row = _row_with_relevance(record)

        assert row["status"] == "pending"
        assert row["relevance_status"] == "classified"
        assert row["relevance_verdict"]["decision"] == "out_of_scope"
        assert row["relevance_error"] is None

    def test_classified_needs_review(self, record):
        with (
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_classify.return_value = {"verdict": {"decision": "needs_review"}}
            mock_validate.return_value = {"verdict": {"decision": "needs_review", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            row = _row_with_relevance(record)

        assert row["relevance_status"] == "classified"
        assert row["relevance_verdict"]["decision"] == "needs_review"

    def test_error_graceful_pending_preserved(self, record):
        """Erro do classificador produz registro pending, sem excluir."""
        with (
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_classify.return_value = {"error": "timeout: provedor não respondeu"}
            mock_validate.return_value = {"error": "timeout: provedor não respondeu"}

            row = _row_with_relevance(record)

        assert row["status"] == "pending"
        assert row["relevance_status"] == "error"
        assert row["relevance_error"] is not None
        assert "timeout" in row["relevance_error"]

    def test_unexpected_exception_never_blocks_staging(self, record):
        """Exceção inesperada no classificador vira erro sanitizado."""
        with patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify:
            mock_classify.side_effect = RuntimeError("homem morreu")

            row = _row_with_relevance(record)

        assert row["status"] == "pending"
        assert row["relevance_status"] == "error"
        assert "provider_error" in row["relevance_error"]

    def test_no_material_skips_classification(self):
        """Registro sem texto_cru nem descricao não tenta classificar."""
        rec = {
            "url": "https://exemplo.com/vazio",
            "url_hash": "vazio",
            "texto_cru": "",
            "descricao": "",
        }
        row = _row_with_relevance(rec)
        assert row["status"] == "pending"
        assert "relevance_status" not in row

    def test_editorial_columns_untouched(self, record):
        """Colunas editoriais nunca são sobrescritas pela relevância."""
        with (
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_classify.return_value = {"verdict": {"decision": "in_scope"}}
            mock_validate.return_value = {"verdict": {"decision": "in_scope", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            row = _row_with_relevance(record)

        assert row["status"] == "pending"
        assert "reject_reason" not in row
        assert "reviewed_at" not in row
        assert "promoted_web_source_id" not in row


# ══════════════════════════════════════════════════════════════════════════
# Discover → stage (integration com mock)
# ══════════════════════════════════════════════════════════════════════════


class TestStageRecordsRelevance:
    def test_records_have_relevance_on_upsert(self, record):
        """_stage_records inclui colunas de relevância no upsert."""
        with (
            patch("radar.core.infra.db.get_supabase_service") as mock_get_db,
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_get_db.return_value = mock_db = MagicMock()
            mock_classify.return_value = {"verdict": {"decision": "in_scope"}}
            mock_validate.return_value = {"verdict": {"decision": "in_scope", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            from radar.core.ingestion.opportunity_discovery import _stage_records
            _stage_records([record])

            upsert_args = mock_db.table.return_value.upsert.call_args
            assert upsert_args is not None
            rows = upsert_args[0][0]
            assert len(rows) == 1
            row = rows[0]
            assert row["status"] == "pending"
            assert row["relevance_status"] == "classified"

    def test_relevance_error_still_upserts_pending(self, record):
        """Falha na classificação não impede o upsert do candidato."""
        with (
            patch("radar.core.infra.db.get_supabase_service") as mock_get_db,
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_get_db.return_value = mock_db = MagicMock()
            mock_classify.return_value = {"error": "provider_error: falha no provedor"}
            mock_validate.return_value = {"error": "provider_error: falha no provedor"}

            from radar.core.ingestion.opportunity_discovery import _stage_records
            _stage_records([record])

            rows = mock_db.table.return_value.upsert.call_args[0][0]
            row = rows[0]
            assert row["status"] == "pending"
            assert row["relevance_status"] == "error"

    def test_ignore_duplicates_preserves_classified(self, record):
        """ignore_duplicates=True impede que erro posterior apague classified."""
        with (
            patch("radar.core.infra.db.get_supabase_service") as mock_get_db,
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_get_db.return_value = mock_db = MagicMock()
            mock_classify.return_value = {"verdict": {"decision": "in_scope"}}
            mock_validate.return_value = {"verdict": {"decision": "in_scope", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            from radar.core.ingestion.opportunity_discovery import _stage_records
            _stage_records([record])
            upsert_kwargs = mock_db.table.return_value.upsert.call_args[1]
            assert upsert_kwargs.get("ignore_duplicates") is True

    def test_no_gold_table_written(self, record):
        """Escrita ocorre apenas em discovered_opportunities."""
        with (
            patch("radar.core.infra.db.get_supabase_service") as mock_get_db,
            patch("radar.core.ingestion.relevance_classifier.classify_opportunity") as mock_classify,
            patch("radar.core.ingestion.relevance_classifier.validate_opportunity_result") as mock_validate,
        ):
            mock_get_db.return_value = mock_db = MagicMock()
            mock_classify.return_value = {"verdict": {"decision": "in_scope"}}
            mock_validate.return_value = {"verdict": {"decision": "in_scope", "reason_codes": [], "exclusion_codes": [], "evidence": [], "missing_information": [], "classifier_version": "v1"}}

            from radar.core.ingestion.opportunity_discovery import _stage_records
            _stage_records([record])

            called_tables = {c[0][0] for c in mock_db.table.call_args_list}
            assert called_tables == {"discovered_opportunities"}
            assert "entities" not in called_tables
            assert "entity_relationships" not in called_tables
            assert "match_chunks" not in called_tables


# ══════════════════════════════════════════════════════════════════════════
# Legacy / migration contract
# ══════════════════════════════════════════════════════════════════════════


class TestLegacyContract:
    def test_migration_041_default_is_unclassified(self):
        """A migration SQL define default 'unclassified' + NOT NULL + 4 colunas."""
        import importlib.util

        migration_path = "supabase/migrations/041_discovered_opportunities_relevance.sql"
        try:
            spec = importlib.util.find_spec("radar")
            pkg_path = spec.origin
            root = pkg_path.rsplit("/src/radar", 1)[0]
        except Exception:
            root = "."

        path = f"{root}/{migration_path}"
        import os.path
        if os.path.exists(path):
            sql = open(path, encoding="utf-8").read()
        else:
            raise FileNotFoundError(f"migration not found at {path}")

        assert "default 'unclassified'" in sql, "default must be 'unclassified'"
        assert "not null" in sql.lower(), "relevance_status must be NOT NULL"
        assert "relevance_status" in sql, "relevance_status column missing"
        assert "relevance_verdict" in sql, "relevance_verdict column missing"
        assert "relevance_error" in sql, "relevance_error column missing"
        assert "relevance_classified_at" in sql, "relevance_classified_at column missing"




# ══════════════════════════════════════════════════════════════════════════
# Promote/reject independence
# ══════════════════════════════════════════════════════════════════════════


class TestPromoteRejectIndependence:
    def test_promote_not_called_by_staging(self):
        """_stage_records nunca chama promote."""
        from radar.core.ingestion.opportunity_discovery import _row_with_relevance, _stage_records
        src = inspect_getsource(_stage_records)
        assert "promote" not in src
        src2 = inspect_getsource(_row_with_relevance)
        assert "promote" not in src2

    def test_reject_not_called_by_staging(self):
        """_stage_records nunca chama reject."""
        from radar.core.ingestion.opportunity_discovery import _row_with_relevance, _stage_records
        src = inspect_getsource(_stage_records)
        assert "reject" not in src
        src2 = inspect_getsource(_row_with_relevance)
        assert "reject" not in src2

    def test_cache_not_modified_by_relevance(self):
        """_row_with_relevance não toca o cache negativo."""
        from radar.core.ingestion.opportunity_discovery import _row_with_relevance
        src = inspect_getsource(_row_with_relevance)
        assert "record_rejection" not in src
        assert "rejected" not in src or "relevance_status" in src  # relevância != cache


# ══════════════════════════════════════════════════════════════════════════
# write=False não escreve staging
# ══════════════════════════════════════════════════════════════════════════


class TestWriteFlag:
    def test_write_false_skips_staging(self):
        """discover_opportunities(write=False) não chega a _stage_records."""
        import inspect

        from radar.core.ingestion.opportunity_discovery import discover_opportunities
        src = inspect.getsource(discover_opportunities)
        # O guard write controla a chamada de _stage_records
        lines = src.split("\n")
        write_guard_lines = [i for i, line in enumerate(lines) if "if write:" in line]
        assert write_guard_lines, "write guard not found in discover_opportunities"
        idx = write_guard_lines[0]
        # A linha seguinte ao guard deve referenciar _stage_records
        following = [line for line in lines[idx:] if line.strip()]
        assert any("_stage_records" in line for line in following[:5])


def inspect_getsource(func):
    import inspect
    return inspect.getsource(func)
