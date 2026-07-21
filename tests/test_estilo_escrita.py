"""Testes do campo `estilo_escrita` (docs/specs/playbook-overlays-plan.md).

Craft de escrita da empresa, preenchido à mão pelo dono no Perfil e injetado
SÓ no prompt do Redator. Restrição dura da fase: ZERO token — todos os testes
aqui são de montagem de prompt / round-trip de dados, nunca chamam LLM.

Cobre:
  • T1 — round-trip do campo pelo schema/allowlist do backend; grep-check que
    ele NÃO entra em CompanyProfile.to_context() (matching).
  • T2 — montagem do prompt do Redator injeta o bloco quando preenchido, fica
    vazio quando não; checagem de não-vazamento para o Monitor/Critic.
  • T4 — captura best-effort do par rascunho-IA → edição do usuário em
    set_section_content (severável; log não é lido por nada nesta fase).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.common import (  # noqa: E402
    CompanyProfileSchema,
    profile_from_workspace,
    to_py_profile,
)
from core.services.writing_session import WritingSession  # noqa: E402
from domain.user_profile import CompanyProfile  # noqa: E402

# ============================================================================
# T1 — campo no backend (schema, allowlist, ausência em to_context)
# ============================================================================

def test_schema_round_trip_estilo_escrita():
    schema = CompanyProfileSchema(estilo_escrita="usa analogias de futebol")
    profile = to_py_profile(schema)
    assert profile.estilo_escrita == "usa analogias de futebol"


def test_schema_default_estilo_escrita_vazio():
    schema = CompanyProfileSchema()
    profile = to_py_profile(schema)
    assert profile.estilo_escrita == ""


def _mock_db_with_profile(raw_profile: dict) -> MagicMock:
    db = MagicMock()
    execute_result = MagicMock()
    execute_result.data = {"profile": raw_profile}
    (
        db.table.return_value.select.return_value.eq.return_value
        .maybe_single.return_value.execute.return_value
    ) = execute_result
    return db


def test_profile_from_workspace_com_estilo_escrita():
    db = _mock_db_with_profile({"nome": "ACME", "estilo_escrita": "direto, sem jargão"})
    profile = profile_from_workspace(db, "ws_1")
    assert profile.estilo_escrita == "direto, sem jargão"


def test_profile_from_workspace_sem_estilo_escrita_usa_default():
    db = _mock_db_with_profile({"nome": "ACME"})
    profile = profile_from_workspace(db, "ws_1")
    assert profile.estilo_escrita == ""


def test_estilo_escrita_nao_entra_em_to_context():
    """Grep-check em runtime: matching (to_context) não deve ver o estilo."""
    marker = "MARCADOR_ESTILO_UNICO_9f3a"
    profile = CompanyProfile(nome="ACME", estilo_escrita=marker)
    assert marker not in profile.to_context()

    # Grep-check estático também — o campo não deve nem ser mencionado dentro
    # do corpo do método to_context().
    src = inspect.getsource(CompanyProfile.to_context)
    assert "estilo_escrita" not in src


# ============================================================================
# T2 — injeção no prompt do Redator (+ não-vazamento p/ Monitor/Critic)
# ============================================================================

def _make_session(estilo: str = "") -> WritingSession:
    """WritingSession sem __init__ (que exige DB real) — mesmo padrão de
    tests/test_writing_session_agent.py / tests/test_prompt_caching.py."""
    s = WritingSession.__new__(WritingSession)
    s.session_id = "sess_estilo"
    s.workspace_id = "ws_estilo"
    s.edital_id = "ed_estilo"
    s._db = MagicMock()
    s._scope_edital_ids = ["ed_estilo"]
    s._doc_sections = {}
    s._proposal_outline = ["1. Identificação", "2. Objeto"]
    s._library_item_ids = set()
    s._history = []
    s._history_summary = ""
    s._turn_count = 0
    s._profile_context = "Empresa: ACME Bio."
    s._library_context = ""
    s._reflection_insights_context = ""
    s._temporal_block = ""
    s.mode = "proposal"
    s._source_card_context = ""
    s._programa_context = ""
    s._project_description = None
    s._pending_user_input = None
    s._plan = None
    s._plan_pending_confirmation = False
    s._tool_results = []
    s._critic_fail_open_count = 0
    s._playbook_writer_block = ""
    s._playbook_monitor_block = ""
    s._estilo_empresa_block = (
        f"ESTILO DA EMPRESA (como esta empresa gosta de contar sua história):\n{estilo}"
        if estilo else ""
    )
    s.backend = "anthropic"
    s.model = "claude-sonnet-4-6"
    return s


def _joined(msgs: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in msgs)


def test_generation_messages_contem_estilo_quando_preenchido():
    s = _make_session(estilo="usa analogias de futebol, tom direto")
    msgs = s._build_generation_section_messages("2. Objeto")
    joined = _joined(msgs)
    assert "ESTILO DA EMPRESA" in joined
    assert "usa analogias de futebol, tom direto" in joined


def test_generation_messages_sem_bloco_quando_estilo_vazio():
    s = _make_session(estilo="")
    msgs = s._build_generation_section_messages("2. Objeto")
    joined = _joined(msgs)
    assert "ESTILO DA EMPRESA" not in joined


def test_agent_initial_messages_contem_estilo_quando_preenchido():
    s = _make_session(estilo="prefere números concretos a adjetivos")
    msgs = s._build_agent_initial_messages("escreva a seção", None, "")
    joined = _joined(msgs)
    assert "ESTILO DA EMPRESA" in joined
    assert "prefere números concretos a adjetivos" in joined


def test_agent_initial_messages_sem_bloco_quando_estilo_vazio():
    s = _make_session(estilo="")
    msgs = s._build_agent_initial_messages("escreva a seção", None, "")
    joined = _joined(msgs)
    assert "ESTILO DA EMPRESA" not in joined


def test_estilo_nao_referenciado_em_paths_de_monitor_ou_critic():
    """Checagem arquitetural: estilo_escrita/_estilo_empresa_block não podem
    aparecer no código que alimenta ComplianceMonitor/Critic — nem por
    coincidência de refactor futuro."""
    from core.llm.agent_tools import critic_agent
    from core.services import checklist_service

    for mod in (critic_agent, checklist_service):
        src = inspect.getsource(mod)
        assert "estilo_escrita" not in src
        assert "_estilo_empresa_block" not in src

    # _resolve_playbook (fonte do bloco for_monitor) também não referencia estilo.
    resolve_src = inspect.getsource(WritingSession._resolve_playbook)
    assert "estilo" not in resolve_src.lower()


def test_playbook_monitor_block_independente_do_estilo():
    s = _make_session(estilo="texto de estilo que NUNCA deve vazar pro monitor")
    assert "texto de estilo" not in s._playbook_monitor_block


# ============================================================================
# T4 — captura par rascunho-IA → edição do usuário (severável)
# ============================================================================

def _make_session_for_save() -> WritingSession:
    s = WritingSession.__new__(WritingSession)
    s.session_id = "sess_style_log"
    s._db = MagicMock()
    s._db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
    s._doc_sections = {}
    s._generation_critic_annotations = {}
    s._style_edit_log = []
    return s


def test_style_edit_log_registra_par_quando_edicao_difere():
    s = _make_session_for_save()
    s.set_section_content("1. Identificação", "rascunho gerado pela IA")
    assert s._style_edit_log == []  # primeira gravação: sem "anterior" p/ comparar

    s.set_section_content("1. Identificação", "texto reescrito pelo usuário")
    assert len(s._style_edit_log) == 1
    entry = s._style_edit_log[0]
    assert entry["section"] == "1. Identificação"
    assert entry["ai_draft"] == "rascunho gerado pela IA"
    assert entry["user_edited"] == "texto reescrito pelo usuário"
    assert "ts" in entry


def test_style_edit_log_nao_cresce_quando_conteudo_identico():
    s = _make_session_for_save()
    s.set_section_content("1. Identificação", "rascunho gerado pela IA")
    s.set_section_content("1. Identificação", "texto reescrito pelo usuário")
    assert len(s._style_edit_log) == 1

    s.set_section_content("1. Identificação", "texto reescrito pelo usuário")  # idêntico
    assert len(s._style_edit_log) == 1


def test_style_edit_log_persistido_no_jsonb_section_drafts():
    s = _make_session_for_save()
    s.set_section_content("1. Identificação", "rascunho gerado pela IA")
    s.set_section_content("1. Identificação", "texto reescrito pelo usuário")

    update_calls = s._db.table.return_value.update.call_args_list
    assert update_calls, "esperava ao menos uma chamada .update(...) no mock"
    last_payload = update_calls[-1].args[0]
    assert "_style_edit_log" in last_payload["section_drafts"]
    assert len(last_payload["section_drafts"]["_style_edit_log"]) == 1


def test_style_edit_log_falha_de_persistencia_nao_quebra_save():
    """Best-effort: se o update no DB falhar, set_section_content não propaga
    a exceção (mesmo padrão do try/except já existente no método)."""
    s = _make_session_for_save()
    s._db.table.return_value.update.return_value.eq.return_value.execute.side_effect = (
        RuntimeError("db indisponível")
    )
    s.set_section_content("1. Identificação", "rascunho gerado pela IA")
    s.set_section_content("1. Identificação", "texto reescrito pelo usuário")  # não deve levantar
    assert s._doc_sections["1. Identificação"] == "texto reescrito pelo usuário"
