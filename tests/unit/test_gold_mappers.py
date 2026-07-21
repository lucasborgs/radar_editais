"""Testes dos mapeadores DETERMINÍSTICOS do ingest gold v3 (core/kg/gold.py).

Rodam sem LLM nem DB — só as funções puras que leem os vocabulários do docs/domain/schema.md
(§13). Cobrem o passe de normalização de tags/setores, a classificação de seções
(temática/elegibilidade/boilerplate), extração de UF, split de operador, mapeamento
de mecanismo/formato e a detecção conservadora de programa-pai (subordinado_a).
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from radar.core.kg import gold

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Normalização de tags (§4.3) — lowercase, sinônimo, singular, anti_class
# ---------------------------------------------------------------------------
class TestNormalizeTags:
    def test_sinonimos_case_e_acronimos(self):
        assert gold.normalize_tag("IoT") == "internet das coisas"
        assert gold.normalize_tag("IA") == "inteligência artificial"
        assert gold.normalize_tag("Machine Learning") == "aprendizado de máquina"

    def test_singular_simples_palavra_unica(self):
        assert gold.normalize_tag("startups") == "startup"
        assert gold.normalize_tag("sistemas") == "sistema"

    def test_singular_pula_irregular(self):
        # -ais não deve virar -ai (materiais → materiai seria errado)
        assert gold.normalize_tag("materiais") == "materiais"

    def test_frase_multipalavra_nao_singulariza(self):
        assert gold.normalize_tag("internet das coisas") == "internet das coisas"

    def test_curta_demais_vira_none(self):
        assert gold.normalize_tag("a") is None
        assert gold.normalize_tag("") is None

    def test_lista_filtra_generico_e_dedup(self):
        out = gold.normalize_tags(
            ["IoT", "IA", "startups", "tecnologia", "deep tech", "sistemas", "visão computacional", "IoT"]
        )
        # 'tecnologia' e 'sistemas' são genéricos (anti_class_verdict) → removidos.
        assert "internet das coisas" in out
        assert "inteligência artificial" in out
        assert "visão computacional" in out
        assert "deep-tech" in out
        assert "tecnologia" not in out
        assert "sistema" not in out
        assert out.count("internet das coisas") == 1  # dedup


# ---------------------------------------------------------------------------
# Normalização de setores (§4.3) — taxonomia fechada, 1-3, fallback
# ---------------------------------------------------------------------------
class TestNormalizeSetores:
    def test_case_curado(self):
        assert gold.normalize_setores(["multissetorial"]) == ["Multissetorial"]

    def test_tese_themes_expandem_para_labels(self):
        out = gold.normalize_setores(["agro - bioeconomia e alimentos"])
        assert out == ["Agro", "Bioeconomia"]

    def test_alias_de_slug(self):
        assert gold.normalize_setores(["ti-software"]) == ["TIC"]
        assert gold.normalize_setores(["meio-ambiente"]) == ["Sustentabilidade"]

    def test_fallback_quando_vazio_ou_desconhecido(self):
        assert gold.normalize_setores([]) == ["Multissetorial"]
        assert gold.normalize_setores(["xyzzy inexistente"]) == ["Multissetorial"]

    def test_cap_em_3(self):
        out = gold.normalize_setores(["Saúde", "TIC", "Energia", "Agro", "Defesa"])
        assert len(out) == 3
        assert out == ["Saúde", "TIC", "Energia"]

    def test_dedup_e_dentro_do_vocabulario(self):
        out = gold.normalize_setores(["saude", "Saúde", "healthtech"])
        assert out == ["Saúde"]
        labels = gold.schema.setores_taxonomia()["labels"]
        assert all(s in labels for s in out)


# ---------------------------------------------------------------------------
# Classificação de seções (§5.3)
# ---------------------------------------------------------------------------
class TestClassifySection:
    def test_elegibilidade_tem_precedencia(self):
        assert gold.classify_section(["2. Eligibilidade dos Partícipes"], "heading") == "eligibility"
        assert gold.classify_section(["Elegibilidade", "Público-alvo"], "paragraph") == "eligibility"

    def test_boilerplate_por_padrao_de_secao(self):
        assert gold.classify_section(["CHAMADA X", "ANEXO 1"], "paragraph") == "boilerplate"
        assert gold.classify_section(["3. Cronograma"], "paragraph") == "boilerplate"
        assert gold.classify_section(["Documentação exigida"], "paragraph") == "boilerplate"

    def test_boilerplate_por_kind(self):
        assert gold.classify_section(["Objetivos"], "signature") == "boilerplate"
        assert gold.classify_section(["Objetivos"], "boilerplate") == "boilerplate"

    def test_tematico_default(self):
        assert gold.classify_section(["OBJETIVOS DA COOPERAÇÃO"], "paragraph") == "thematic"
        assert gold.classify_section(["Linhas Temáticas", "Escopo"], "paragraph") == "thematic"
        assert gold.classify_section([], "paragraph") == "thematic"


# ---------------------------------------------------------------------------
# UF por endereço (EMBRAPII)
# ---------------------------------------------------------------------------
class TestUfFromAddress:
    def test_uf_no_fim(self):
        assert gold._uf_from_address("Rua X, 330, Assunção, São Paulo, SP") == "SP"
        assert gold._uf_from_address("Campus UFSC, Trindade, Florianópolis, SC") == "SC"

    def test_sem_uf_valida(self):
        assert gold._uf_from_address("Rua sem sigla, 100") is None
        assert gold._uf_from_address(None) is None

    def test_sigla_de_2_letras_que_nao_e_uf(self):
        # "XX" não é UF válida → None
        assert gold._uf_from_address("Rua Y, Cidade, XX") is None


# ---------------------------------------------------------------------------
# Operador → agências / canonicalização
# ---------------------------------------------------------------------------
class TestOperador:
    def test_split_por_barra(self):
        assert gold._split_operador("MCTI / FINEP / FAPs estaduais") == ["MCTI", "FINEP", "FAPs estaduais"]

    def test_canon_normaliza_case_e_variantes(self):
        assert gold._canon_agency("Finep") == "FINEP"
        assert gold._canon_agency("faps estaduais") == "FAPs estaduais"

    def test_vazio(self):
        assert gold._split_operador("") == []
        assert gold._split_operador(None) == []


# ---------------------------------------------------------------------------
# Mecanismo / formato (conflito do enum §5.1)
# ---------------------------------------------------------------------------
class TestMecanismoFormato:
    def test_mecanismo_so_no_enum(self):
        assert gold._map_mecanismo("subvencao") == "subvencao"
        assert gold._map_mecanismo("capacitacao") is None  # fora do enum → NULL
        assert gold._map_mecanismo("aceleracao") is None
        assert gold._map_mecanismo(None) is None

    def test_formato_normaliza_hifen(self):
        assert gold._map_formato("edital-periodico") == "edital_periodico"
        assert gold._map_formato("fluxo-continuo") == "fluxo_continuo"

    def test_infer_mecanismo_conservador(self):
        assert gold._infer_mecanismo_from_text("recursos de subvenção econômica não reembolsável") == "subvencao"
        assert gold._infer_mecanismo_from_text("edital genérico sem pista") is None


# ---------------------------------------------------------------------------
# Detecção de programa-pai (subordinado_a) — conservadora
# ---------------------------------------------------------------------------
class TestProgramaDetection:
    """Hermético: `_programa_detection` é data-driven de SILVER_DIR/programas.json
    (gitignored — ausente no CI), então o teste monta a própria fixture em
    tmp_path em vez de depender do arquivo real."""

    @pytest.fixture(autouse=True)
    def _silver_fixture(self, tmp_path, monkeypatch):
        (tmp_path / "programas.json").write_text(
            json.dumps({
                "programas": [
                    {"id": "programa:centelha", "name": "Programa Centelha"},
                    {"id": "programa:tecnova", "name": "Tecnova"},
                ]
            }),
            encoding="utf-8",
        )
        monkeypatch.setattr(gold, "SILVER_DIR", tmp_path)
        # _programa_detection é @lru_cache: limpar antes (outro teste pode ter
        # cacheado um mapa de outro SILVER_DIR) e depois (não vazar a fixture).
        gold._programa_detection.cache_clear()
        yield
        gold._programa_detection.cache_clear()

    def test_brand_inequivoca(self):
        assert gold._detect_programa("Centelha SC 2026") == "programa:centelha"
        assert gold._detect_programa("TECNOVA Santa Catarina") == "programa:tecnova"

    def test_nao_marca_generico(self):
        assert gold._detect_programa("Chamada Pública Bilateral FINEP-CDTI") is None
        assert gold._detect_programa("Auxílio à Inovação Regular") is None

    def test_sem_arquivo_retorna_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(gold, "SILVER_DIR", tmp_path / "vazio")
        gold._programa_detection.cache_clear()
        assert gold._detect_programa("Centelha SC 2026") is None


# ---------------------------------------------------------------------------
# Status normalizado
# ---------------------------------------------------------------------------
class TestStatus:
    def test_deadline_futura_abre(self):
        assert gold._normalize_status("ENCERRADA", date.today() + timedelta(days=10)) == "aberta"

    def test_deadline_passada_encerra(self):
        assert gold._normalize_status("ABERTA", date.today() - timedelta(days=1)) == "encerrada"

    def test_sem_deadline_usa_raw(self):
        assert gold._normalize_status("ABERTA", None) == "aberta"
        assert gold._normalize_status("ENCERRADA", None) == "encerrada"
        assert gold._normalize_status("SEI LÁ", None) is None


# ---------------------------------------------------------------------------
# Empacotamento de match_chunks
# ---------------------------------------------------------------------------
class TestPackChunks:
    def test_empacota_e_preserva_section_path(self):
        blocks = [
            {"section_path": ["Objetivos"], "kind": "heading", "text": "A" * 700},
            {"section_path": ["Objetivos"], "kind": "paragraph", "text": "B" * 700},
            {"section_path": ["Escopo"], "kind": "paragraph", "text": "C" * 300},
        ]
        chunks = gold._pack_chunks(blocks)
        # 700+700 estoura _CHUNK_CHARS (1200) → 2 chunks
        assert len(chunks) >= 2
        assert chunks[0]["section_path"] == ["Objetivos"]
        assert all(c["text"] for c in chunks)

    def test_ignora_blocos_vazios(self):
        assert gold._pack_chunks([{"section_path": [], "kind": "paragraph", "text": "   "}]) == []
