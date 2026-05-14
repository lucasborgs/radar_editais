"""
Tests for the RAG indexing path: chunker (split + section + metadata) and
extraction helpers em core.tasks (table → markdown, header cleanup).

Run via:
    .venv/bin/python -m pytest tests/test_chunker.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.chunker import _SECTION_TITLE_RE, _STRUCTURAL_SPLIT_RE, _detect_metadata, chunk_edital  # noqa: E402
from core.tasks import (  # noqa: E402
    _clean_edital_text,
    _filter_to_latest_versions,
    _table_to_markdown,
    _version_info,
)


def _filler(approx_words: int, seed: str = "lorem") -> str:
    """Bloco de filler com aprox. N palavras — usado pra dar tamanho às seções
    de teste e forçar o chunker a produzir chunks separados."""
    base = (
        f"{seed} ipsum dolor sit amet consectetur adipiscing elit "
        "sed do eiusmod tempor incididunt ut labore et dolore magna "
        "aliqua ut enim ad minim veniam quis nostrud exercitation "
        "ullamco laboris nisi ut aliquip ex ea commodo consequat "
    )
    words = base.split()
    out: list[str] = []
    while len(out) < approx_words:
        out.extend(words)
    return " ".join(out[:approx_words])


# =============================================================================
# Section detection — regex direta (sem passar pelo chunker)
# =============================================================================

def test_section_title_re_matches_finep_decimal():
    """A regex de detecção de section reconhece "N.  TITULO" e "Desafio Tecnológico N"."""
    matches = [
        "1.  OBJETIVO",
        "2.  EIXO TECNOLÓGICO",
        "12.  CRONOGRAMA DA SELEÇÃO PÚBLICA",
        "Desafio Tecnológico 1",
        "Desafio Tecnológico 42",
    ]
    for line in matches:
        assert _SECTION_TITLE_RE.match(line), f"Deveria matchear: {line!r}"


def test_section_title_re_rejeita_falsos_positivos():
    """1 espaço, letra minúscula, ou números soltos NÃO devem virar section."""
    rejects = [
        "1. abc",            # 1 espaço, minúscula → lista inline
        "1. Pequena ideia",  # 1 espaço → não é seção top-level
        "1.1. A Financiadora",  # subseção
        "página 1 de 16",
        "Trecho qualquer com 1.  OBJETIVO no meio",  # não está no início
    ]
    for line in rejects:
        assert not _SECTION_TITLE_RE.match(line), f"NÃO deveria matchear: {line!r}"


def test_section_title_re_marcadores_juridicos():
    """Marcadores clássicos (Art./§/Capítulo/Seção) continuam reconhecidos."""
    for line in ("Art. 1º — Disposições", "Artigo 5º", "§ 1º Parágrafo", "Capítulo I", "Seção II"):
        assert _SECTION_TITLE_RE.match(line), f"Deveria matchear: {line!r}"


# =============================================================================
# Structural split — regex direta
# =============================================================================

def test_structural_split_quebra_em_secoes_finep():
    """O splitter produz uma peça por seção top-level (com 2+ espaços)."""
    text = (
        "Preâmbulo curto.\n\n"
        "1.  OBJETIVO\nconteúdo da seção 1.\n\n"
        "2.  EIXO TECNOLÓGICO\nconteúdo da seção 2.\n\n"
        "Desafio Tecnológico 1: insumos.\n\n"
        "3.  RECURSOS\nconteúdo da seção 3.\n"
    )
    pieces = [p.strip() for p in _STRUCTURAL_SPLIT_RE.split(text) if p.strip()]
    starts = [p.split("\n", 1)[0] for p in pieces]
    assert any(s.startswith("1.") for s in starts), f"Sem 1.  OBJETIVO: {starts}"
    assert any(s.startswith("2.") for s in starts), f"Sem 2.  EIXO: {starts}"
    assert any(s.startswith("Desafio") for s in starts), f"Sem Desafio: {starts}"
    assert any(s.startswith("3.") for s in starts), f"Sem 3.  RECURSOS: {starts}"


# =============================================================================
# Section detection — integração end-to-end via chunk_edital
# =============================================================================

def test_section_finep_decimal_chunks_separados():
    """Com seções longas o suficiente (>TARGET_TOKENS), cada uma vira chunk
    próprio com `section` correto."""
    text = (
        "Preâmbulo do edital sem marcador estrutural.\n\n"
        "1.  OBJETIVO\n" + _filler(900, "alpha") + "\n\n"
        "2.  EIXO TECNOLÓGICO\n" + _filler(900, "beta") + "\n\n"
        "Desafio Tecnológico 1\n" + _filler(900, "gamma") + "\n\n"
        "3.  RECURSOS\n" + _filler(900, "delta") + "\n"
    )
    chunks = chunk_edital(text)
    sections = [c["section"] for c in chunks if c["section"]]
    joined = " | ".join(sections)
    assert any("OBJETIVO" in s for s in sections), f"Faltou OBJETIVO em: {joined}"
    assert any("EIXO" in s for s in sections), f"Faltou EIXO em: {joined}"
    assert any("Desafio" in s for s in sections), f"Faltou Desafio em: {joined}"
    assert any("RECURSOS" in s for s in sections), f"Faltou RECURSOS em: {joined}"


def test_section_finep_nao_quebra_em_subitens():
    """Subitens "1.1.", "1.2." (1 espaço) NÃO devem virar boundary — só
    seções top-level com 2+ espaços é que abrem chunk novo."""
    text = (
        "1.  OBJETIVO\n"
        "1.1. Apoiar projetos inovadores em biorrefino.\n"
        "1.2. Mobilizar o sistema nacional de inovação.\n"
        "1.3. Estimular parcerias entre ICTs e empresas.\n"
    )
    chunks = chunk_edital(text)
    # Tudo cabe num chunk só — a divisão decimal de subitem não dispara split.
    assert len(chunks) == 1, f"Esperado 1 chunk, veio {len(chunks)}"
    assert chunks[0]["section"] is not None
    assert "OBJETIVO" in chunks[0]["section"]


def test_section_finep_ignora_lista_inline():
    """Listas inline ("Considerando que: 1. ...; 2. ...;") com 1 espaço
    e/ou letra minúscula NÃO devem disparar split."""
    text = (
        "Considerando que:\n"
        "1. o cenário atual exige resposta rápida;\n"
        "2. a inovação é estratégica para o país;\n"
        "3. as parcerias devem ser fortalecidas.\n"
    )
    chunks = chunk_edital(text)
    assert len(chunks) == 1, f"Esperado 1 chunk, veio {len(chunks)}"
    assert chunks[0]["section"] is None  # nenhum marcador top-level


def test_section_marcadores_juridicos_classicos():
    """Editais que usam Art./§ continuam sendo chunkados corretamente."""
    text = (
        "Art. 1º — Disposições gerais aplicam-se ao edital.\n\n"
        "Art. 2º — Os participantes devem cumprir as exigências formais.\n\n"
        "§ 1º Parágrafo único de exceção sobre o critério X.\n"
    )
    chunks = chunk_edital(text)
    sections = [c["section"] for c in chunks if c["section"]]
    assert sections, "Marcadores Art./§ deveriam ter produzido sections"
    assert any("Art" in s for s in sections), f"Sem Art em: {sections}"


# =============================================================================
# Metadata detection
# =============================================================================

def test_metadata_cronograma_tem_data():
    text = (
        "12. CRONOGRAMA DA SELEÇÃO PÚBLICA\n"
        "Término do prazo para envio: 29/05/2026\n"
        "Divulgação do resultado preliminar: Até 26/06/2026\n"
    )
    md = _detect_metadata(text)
    assert md["contem_data"] is True
    assert md["contem_tabela"] is False


def test_metadata_criterios_com_tabela_markdown():
    text = (
        "9.2.4. Os critérios de avaliação com nota e peso:\n"
        "| Critério | Nota | Peso |\n"
        "|---|---|---|\n"
        "| Grau de Inovação | 1 a 5 | 2 |\n"
    )
    md = _detect_metadata(text)
    assert md["contem_criterios"] is True
    assert md["contem_tabela"] is True


def test_metadata_valor_financeiro_real_e_milhoes():
    text_real = "Os recursos totalizam R$ 30.000.000,00 (trinta milhões de reais)."
    text_milhoes = "O aporte previsto é de 50 milhões."
    text_neutro = "Os participantes devem submeter a proposta no portal."
    assert _detect_metadata(text_real)["contem_valor_financeiro"] is True
    assert _detect_metadata(text_milhoes)["contem_valor_financeiro"] is True
    assert _detect_metadata(text_neutro)["contem_valor_financeiro"] is False


def test_metadata_elegibilidade():
    text = "Será considerado elegível o proponente que apresentar CNPJ ativo e ICT parceira."
    md = _detect_metadata(text)
    assert md["contem_elegibilidade"] is True


def test_metadata_chunk_inclui_dict():
    text = "1.  OBJETIVO\nApoiar inovação com R$ 10 milhões. Prazo: 29/05/2026.\n"
    chunks = chunk_edital(text)
    assert chunks
    for c in chunks:
        assert "metadata" in c, "chunk dict deve conter chave 'metadata'"
        assert isinstance(c["metadata"], dict)
        # As 5 flags devem estar presentes (mesmo que False).
        for key in (
            "contem_tabela", "contem_data", "contem_valor_financeiro",
            "contem_elegibilidade", "contem_criterios",
        ):
            assert key in c["metadata"], f"flag {key} faltando"


# =============================================================================
# Table → Markdown
# =============================================================================

def test_table_to_markdown_criterios():
    rows = [
        ["#", "Critério", "Descrição", "Nota", "Peso"],
        ["1", "Grau de Inovação", "Avalia a intensidade", "1 a 5", "2"],
        ["2", "Risco Tecnológico", "Risco tecnológico", "1 a 5", "1"],
    ]
    md = _table_to_markdown(rows)
    lines = md.split("\n")
    assert lines[0].startswith("| # | Critério |")
    # Linha separadora: cada coluna fica "---" entre pipes. Pode ter espaços.
    assert "---" in lines[1] and lines[1].count("---") == 5
    assert "Grau de Inovação" in md
    # Peso da linha 1 (último valor "2") preservado.
    assert "| 1 a 5 | 2 |" in lines[2]


def test_table_to_markdown_padding_de_linha_curta():
    """Tabelas FINEP às vezes têm células mescladas → pdfplumber retorna
    linhas com menos colunas. O conversor deve preencher com vazio."""
    rows = [
        ["A", "B", "C"],
        ["x", "y"],          # short row — pad with empty
        ["p", "q", "r", "s"],  # long row — truncate
    ]
    md = _table_to_markdown(rows)
    assert "| A | B | C |" in md
    assert "| x | y |" in md.replace("|  |", "|").replace("  ", " ") or "x" in md
    # Nenhuma linha do body deve quebrar o formato
    for line in md.split("\n")[2:]:
        assert line.startswith("| ") and line.endswith(" |")


def test_table_to_markdown_skip_linhas_vazias():
    rows = [
        ["A", "B"],
        ["x", "y"],
        [None, None],
        ["", ""],
        ["p", "q"],
    ]
    md = _table_to_markdown(rows)
    body_lines = [ln for ln in md.split("\n") if ln.startswith("| ") and "---" not in ln]
    # Header + 2 linhas de dados (sem as vazias)
    assert len(body_lines) == 3, f"Esperado 3 (header+2), veio {len(body_lines)}: {body_lines}"


def test_table_to_markdown_escapa_pipe_em_celula():
    rows = [["Campo", "Valor"], ["foo", "a|b"]]
    md = _table_to_markdown(rows)
    assert "a\\|b" in md, "Pipe dentro de célula deve ser escapado"


# =============================================================================
# Header / footer cleanup
# =============================================================================

def test_clean_remove_cabecalho_institucional():
    dirty = (
        "PETROBRAS Finep MINISTÉRIO DA\n"
        "CIÊNCIA, TECNOLOGIA E INOVAÇÃO GOVERNO DO BRASIL\n"
        "DO LADO DO POVO BRASILEIRO\n"
        "\n"
        "1.  OBJETIVO\n"
        "1.1. A Financiadora de Estudos e Projetos apoia inovação.\n"
        "\n"
        "Página 1 de 16\n"
    )
    cleaned = _clean_edital_text(dirty)
    assert "MINISTÉRIO DA" not in cleaned
    assert "GOVERNO DO BRASIL" not in cleaned
    assert "DO LADO DO POVO" not in cleaned
    assert "Página 1 de 16" not in cleaned
    # Conteúdo preservado
    assert "1.  OBJETIVO" in cleaned
    assert "Financiadora" in cleaned


def test_clean_colapsa_linhas_em_branco_excessivas():
    dirty = "Linha A\n\n\n\n\nLinha B"
    cleaned = _clean_edital_text(dirty)
    assert "\n\n\n" not in cleaned
    assert "Linha A" in cleaned and "Linha B" in cleaned


def test_clean_idempotente_em_texto_limpo():
    clean = "1.  OBJETIVO\nA Financiadora apoia inovação.\n"
    assert _clean_edital_text(clean) == clean.strip()


# =============================================================================
# Version dedup — _version_info + _filter_to_latest_versions
# =============================================================================

def test_version_info_faq_grupo_e_recencia():
    """FAQs vão pro grupo __faq__; recência cresce com nº de versão e com data."""
    g_orig, r_orig = _version_info("FAQ")
    g_v2, r_v2 = _version_info("FAQ_Versão_2")
    g_v4, r_v4 = _version_info("FAQ_Versão_4_-_atualizado_em_06_04_2026")
    g_pf, r_pf = _version_info("FAQ_-_Perguntas_Frequentes_-_Atualizado_em_30_03_2026")

    assert g_orig == g_v2 == g_v4 == g_pf == "__faq__"
    assert r_orig < r_v2 < r_v4
    assert r_pf > r_orig  # data presente vence "FAQ" puro


def test_version_info_edital_regulamento_e_rerratificacao():
    g_e, r_e = _version_info("Edital")
    g_r1, r_r1 = _version_info("Rerratificação_-_Edital_rerratificado")
    g_r2, r_r2 = _version_info("2ª_Rerratificação_-_Edital_rerratificado")
    g_r3, r_r3 = _version_info("3ª_Rerratificação_-_Edital_rerratificado")

    assert g_e == g_r1 == g_r2 == g_r3 == "__edital__"
    assert r_e < r_r1 < r_r2 < r_r3


def test_version_info_nao_versionados_passam():
    """Anexos, comunicados, avisos NÃO devem ser agrupados."""
    naos = [
        "Anexo_I_Diretrizes",
        "Anexo_V__Condições_para_Despesas_Relativas_a_Bolsas",
        "Comunicado_de_rerratificação",  # rerratificação SEM 'edital' → não agrupa
        "Aviso_-_2ª_rerratificação",
        "Rerratificação_do_resultado_final_da_2ª_Etapa",
        "Orientações_para_apresentação",
    ]
    for stem in naos:
        group, _ = _version_info(stem)
        assert group is None, f"{stem!r} foi agrupado em {group!r} mas não deveria"


def test_filter_to_latest_keeps_winner_per_group():
    pdfs = [Path(f) for f in (
        "Edital.pdf",
        "Rerratificação_-_Edital_rerratificado.pdf",
        "2ª_Rerratificação_-_Edital_rerratificado.pdf",
        "FAQ.pdf",
        "FAQ_Versão_2.pdf",
        "FAQ_Versão_4_-_atualizado_em_06_04_2026.pdf",
        "Anexo_I.pdf",
        "Anexo_II.pdf",
    )]
    kept = {p.name for p in _filter_to_latest_versions(pdfs)}
    # Vencedores
    assert "2ª_Rerratificação_-_Edital_rerratificado.pdf" in kept
    assert "FAQ_Versão_4_-_atualizado_em_06_04_2026.pdf" in kept
    # Anexos passam (não-versionados)
    assert "Anexo_I.pdf" in kept
    assert "Anexo_II.pdf" in kept
    # Perdedores
    assert "Edital.pdf" not in kept
    assert "Rerratificação_-_Edital_rerratificado.pdf" not in kept
    assert "FAQ.pdf" not in kept
    assert "FAQ_Versão_2.pdf" not in kept


def test_filter_to_latest_idempotente_quando_sem_versoes():
    pdfs = [Path("Anexo_I.pdf"), Path("Anexo_II.pdf"), Path("Comunicado.pdf")]
    kept = _filter_to_latest_versions(pdfs)
    assert {p.name for p in kept} == {p.name for p in pdfs}


def test_filter_to_latest_lista_vazia():
    assert _filter_to_latest_versions([]) == []
