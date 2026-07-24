"""RT01-T12 — backfill amostral de proveniência (hermético, sem banco real).

Um teste por caso pedido no plano
(docs/execution/radar-data-trust/plans/01-provenance/RT01-T12-sample-backfill.md):
requisito resolve/não resolve, status igual/diferente, path já preenchido
(no-op), chunk igual/diferente, producer sempre backfill, dry-run não
escreve. As funções `decide_*` são puras (sem I/O); a orquestração
(`run_backfill`) é exercitada com um `conn`/`cursor` fake gravando chamadas
e com `_fetch_rows`/`_fetch_chunks`/`_load_*_catalog`/`gold.*` monkeypatched
— nenhuma conexão de rede ou banco real em nenhum teste.
"""
from __future__ import annotations

from radar.core.kg import gold
from radar.core.kg import provenance_backfill as pb

# ===========================================================================
# requisitos_texto — resolve / não resolve
# ===========================================================================


def test_requisito_resolves_to_stated_backfill():
    blocks = [{
        "idx": 0, "doc": "edital.pdf", "page": 2, "section_path": ["Requisitos"],
        "kind": "paragraph", "text": "O proponente deve comprovar CNPJ ativo há 2 anos.",
    }]
    fp = pb.decide_requisito(
        "comprovar CNPJ ativo há 2 anos", blocks=blocks, source="finep", stem="589",
        edital_id="finep:589", silver_source_hash="md5:abc123", source_url=None,
        already_present=False,
    )
    assert fp is not None
    assert fp["state"] == "stated"
    assert fp["evidence_refs"][0]["locator_quality"] == "exact"
    assert fp["evidence_refs"][0]["page"] == 2


def test_requisito_unresolved_returns_none():
    blocks = [{
        "idx": 0, "doc": "edital.pdf", "page": 2, "section_path": ["Requisitos"],
        "kind": "paragraph", "text": "O proponente deve comprovar CNPJ ativo há 2 anos.",
    }]
    fp = pb.decide_requisito(
        "texto que não existe em nenhum bloco silver", blocks=blocks, source="finep", stem="589",
        edital_id="finep:589", silver_source_hash="md5:abc123", source_url=None,
        already_present=False,
    )
    assert fp is None


# ===========================================================================
# status / mecanismo — igual / diferente
# ===========================================================================


def test_status_matching_rederivation_returns_inferred_backfill():
    fp = pb.decide_status(rederived="aberta", stored="aberta", already_present=False)
    assert fp is not None
    assert fp["state"] == "inferred"
    assert fp["evidence_refs"] == []
    assert fp["derivation"]["rule"] == "_normalize_status:v1"


def test_status_mismatch_returns_none():
    fp = pb.decide_status(rederived="encerrada", stored="aberta", already_present=False)
    assert fp is None


def test_mecanismo_matching_rederivation_returns_inferred_backfill():
    fp = pb.decide_mecanismo(rederived="subvencao", stored="subvencao", already_present=False)
    assert fp is not None
    assert fp["state"] == "inferred"
    assert fp["derivation"]["rule"] == "_infer_mecanismo_from_text:v1"


def test_mecanismo_mismatch_returns_none():
    fp = pb.decide_mecanismo(rederived="bolsa", stored="subvencao", already_present=False)
    assert fp is None


# ===========================================================================
# path já preenchido → intocado (no-op), mesmo quando resolvível
# ===========================================================================


def test_path_already_present_is_noop():
    blocks = [{
        "idx": 0, "doc": "edital.pdf", "page": 1, "section_path": [],
        "kind": "paragraph", "text": "Requisito X aqui.",
    }]
    assert pb.decide_requisito(
        "Requisito X", blocks=blocks, source="finep", stem="1", edital_id="finep:1",
        silver_source_hash="md5:abc", source_url=None, already_present=True,
    ) is None
    assert pb.decide_status(rederived="aberta", stored="aberta", already_present=True) is None
    assert pb.decide_mecanismo(rederived="bolsa", stored="bolsa", already_present=True) is None


# ===========================================================================
# match_chunks — texto igual / diferente / ambíguo
# ===========================================================================


def _repacked_chunks():
    return [
        {"section_path": [], "kind": "paragraph", "text": "Texto do chunk um.",
         "src_doc": "edital.pdf", "src_page": 3, "src_idx": 5},
        {"section_path": [], "kind": "paragraph", "text": "Texto do chunk dois.",
         "src_doc": "edital.pdf", "src_page": 4, "src_idx": 9},
    ]


def test_chunk_exact_text_match_returns_coords():
    coords = pb.decide_chunk_coords(
        stored_text="Texto do chunk um.", repacked_chunks=_repacked_chunks(),
        silver_source_hash="md5:abc123", already_filled=False,
    )
    assert coords == {"document": "edital.pdf", "page": 3, "silver_block_idx": 5, "source_hash": "md5:abc123"}


def test_chunk_no_match_or_ambiguous_returns_none():
    assert pb.decide_chunk_coords(
        stored_text="Texto que não bate com nada.", repacked_chunks=_repacked_chunks(),
        silver_source_hash="md5:abc123", already_filled=False,
    ) is None
    ambiguous = [
        {"section_path": [], "kind": "paragraph", "text": "Duplicado.", "src_doc": "a", "src_page": 1, "src_idx": 0},
        {"section_path": [], "kind": "paragraph", "text": "Duplicado.", "src_doc": "a", "src_page": 2, "src_idx": 1},
    ]
    assert pb.decide_chunk_coords(
        stored_text="Duplicado.", repacked_chunks=ambiguous, silver_source_hash="md5:abc", already_filled=False,
    ) is None


# ===========================================================================
# producer sempre kind=backfill
# ===========================================================================


def test_producer_always_backfill_kind():
    blocks = [{"idx": 0, "doc": "d.pdf", "page": 1, "section_path": [], "kind": "paragraph", "text": "Alfa."}]
    requisito_fp = pb.decide_requisito(
        "Alfa", blocks=blocks, source="finep", stem="1", edital_id="finep:1",
        silver_source_hash="md5:x", source_url=None, already_present=False,
    )
    status_fp = pb.decide_status(rederived="aberta", stored="aberta", already_present=False)
    mecanismo_fp = pb.decide_mecanismo(rederived="bolsa", stored="bolsa", already_present=False)
    ict_paths = pb.decide_catalog_anchor_paths(
        kind="ict", record={"name": "ICT Um", "url": "https://ict.example"}, document="embrapii_2026.json",
        source_url="https://ict.example", native_id="embrapii:ict-um", existing_paths=set(),
    )
    for fp in (requisito_fp, status_fp, mecanismo_fp, *ict_paths.values()):
        assert fp["producer"] == {
            "kind": "backfill", "name": "rt01_t12_backfill", "version": "1",
            "model": None, "prompt_version": None,
        }


# ===========================================================================
# âncora de catálogo — ICT stated / investidor unknown / registro ausente
# ===========================================================================


def test_ict_catalog_anchor_is_stated_name_and_url_only():
    paths = pb.decide_catalog_anchor_paths(
        kind="ict", record={"name": "ICT Um", "url": "https://ict.example"}, document="embrapii_2026.json",
        source_url="https://ict.example", native_id="embrapii:ict-um", existing_paths=set(),
    )
    assert set(paths) == {"name", "metadata.url"}
    assert all(v["state"] == "stated" for v in paths.values())


def test_investidor_catalog_anchor_is_unknown_copied_fields():
    paths = pb.decide_catalog_anchor_paths(
        kind="investidor", record={"name": "Fundo X", "tese": "early stage", "site": "https://fundo.example"},
        document="data/silver/investidores.json", source_url="https://fundo.example",
        native_id="fundo-x", existing_paths=set(),
    )
    assert set(paths) == {"name", "description", "metadata.site"}
    assert all(v["state"] == "unknown" for v in paths.values())


def test_catalog_anchor_missing_document_returns_empty():
    paths = pb.decide_catalog_anchor_paths(
        kind="investidor", record={"name": "Fundo X"}, document=None, source_url=None,
        native_id="fundo-x", existing_paths=set(),
    )
    assert paths == {}


# ===========================================================================
# dry-run não escreve
# ===========================================================================


class _RecordingCursor:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.calls.append((sql, params))


class _RecordingConn:
    def __init__(self):
        self.cursor_obj = _RecordingCursor()

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        pass


def _install_fixture(monkeypatch):
    edital_row = {
        "id": "e1", "source": "finep", "native_id": "finep:589", "status": "aberta",
        "mecanismo": None, "requisitos_texto": ["Requisito X"], "provenance": {},
    }
    silver_blocks = [{
        "idx": 0, "doc": "edital.pdf", "page": 1, "section_path": [], "kind": "paragraph",
        "text": "Aqui está o Requisito X por extenso.",
    }]

    def fake_fetch_rows(_cur, kind):
        return [edital_row] if kind == "edital" else []

    monkeypatch.setattr(pb, "_fetch_rows", fake_fetch_rows)
    monkeypatch.setattr(pb, "_fetch_chunks", lambda _cur: [])
    monkeypatch.setattr(pb, "_load_investidor_catalog", lambda: {})
    monkeypatch.setattr(pb, "_load_programa_catalog", lambda: {})
    monkeypatch.setattr(pb, "_load_ict_catalog", lambda: {})
    monkeypatch.setattr(gold, "_read_silver_blocks", lambda source, stem: silver_blocks)
    monkeypatch.setattr(gold, "_read_silver_hash", lambda source, stem: "abc123")
    monkeypatch.setattr(
        gold, "_edital_metadata",
        lambda source, stem, blocks: {"status": "aberta", "descricao_bronze": "", "deadline": None},
    )


def test_dry_run_does_not_write(monkeypatch):
    _install_fixture(monkeypatch)
    conn = _RecordingConn()

    report = pb.run_backfill(conn, execute=False, sample=5)

    finep = report["origins"]["finep"]
    assert finep["paths"]["stated"] == 1  # calcula tudo...
    assert finep["paths"]["inferred"] == 1  # (requisito stated + status inferred)
    update_calls = [c for c in conn.cursor_obj.calls if "update" in c[0].lower()]
    assert update_calls == []  # ...mas não escreve


def test_execute_writes_when_not_already_present(monkeypatch):
    _install_fixture(monkeypatch)
    monkeypatch.setattr(pb, "assert_database_target", lambda *a, **k: {})
    conn = _RecordingConn()

    report = pb.run_backfill(conn, execute=True, sample=5, prestate_path=None)

    finep = report["origins"]["finep"]
    assert finep["write"]["entities_written"] == 1
    assert finep["write"]["paths_written"] == 2  # requisitos_texto.0 + status (mecanismo é None, sem path)
    update_calls = [c for c in conn.cursor_obj.calls if "update" in c[0].lower()]
    assert len(update_calls) == 1


def test_defer_editais_measures_but_does_not_write_editais(monkeypatch):
    """Rework RT01-T12: `defer_editais=True` mede os editais (métricas do
    relatório intactas) mas NÃO escreve — o adiamento é por decisão explícita,
    não por ausência de dados (os silver blocks estão presentes no fixture)."""
    _install_fixture(monkeypatch)
    monkeypatch.setattr(pb, "assert_database_target", lambda *a, **k: {})
    conn = _RecordingConn()

    report = pb.run_backfill(conn, execute=True, sample=5, prestate_path=None, defer_editais=True)

    finep = report["origins"]["finep"]
    # medição preservada: os paths continuam sendo CALCULADOS...
    assert finep["paths"]["stated"] == 1
    assert finep["paths"]["inferred"] == 1
    # ...mas nada é escrito para a origem de edital.
    assert finep["write"]["entities_written"] == 0
    update_calls = [c for c in conn.cursor_obj.calls if "update" in c[0].lower()]
    assert update_calls == []
