"""
Extractor PNIPE — laboratórios como CAPACIDADES (ICTs), não editais.

PNIPE (Plataforma Nacional de Infraestrutura de Pesquisa, pnipe.mcti.gov.br) é
uma SPA client-side SEM API pública estável (sondado em 2026-08: /api/search e
/api/labs devolvem o HTML shell, sem JSON). Per docs/specs/ict-pnipe-capabilities.md,
a integração NÃO é um scraper ao vivo de uma API que não existe: este módulo é o
**ponto de entrada** de uma fonte curada — um dump representativo é normalizado
por `parse_pnipe_record` para o contrato bronze consumido por
core/kg/gold.py (`_ingest_icts`, fonte `pnipe`).

Não claims (spec §4): não afirma completude nacional; não negocia contato; não
infere disponibilidade/parceria; laboratório nunca vira edital. O `data_extracao`
do registro é a **data de verificação** do curador, preservada como proveniência
(document_only, ver docs/domain/sources/pnipe.md).

Output bronze (bronze_data/ict_raw/pnipe_*.json):
  name, slug, source='pnipe', kind='laboratorio', url, about, institution,
  institution_type, address, municipio, competencias[], equipamentos[],
  condicoes_acesso, contact{responsavel,email,telefone,site}, areas_raw[],
  data_extracao.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from radar.core.config import BRONZE_DIR

from .base import BaseScraper

logger = logging.getLogger(__name__)

#: Nome do arquivo de dump curado (raw) por padrão, relativo a BRONZE_DIR. O
#: operador grava o dump aqui e roda `PnipeScraper.run()`; sobrescrevível via
#: `PNIPE_DUMP_PATH`.
DEFAULT_DUMP_RELPATH = "ict_raw/pnipe_dump.json"


def parse_pnipe_record(raw: dict) -> dict:
    """Normaliza UM registro do dump curado do PNIPE para o contrato bronze.

    Contrato de entrada (dump raw) e saída (bronze) definidos em
    docs/domain/sources/pnipe.md (`pnipe_schema`). Regras:
    - `name`/`nome` é obrigatório; `slug` derivado de `schema.slugify` quando
      ausente.
    - `url` obrigatório (página oficial do laboratório no índice PNIPE) —
      vira `metadata.url`, a âncora `document_only` e o `canal_de_acesso`.
    - `data_extracao` = `verificado_em` (data de verificação do curador); o
      ingest a usa como `collected_at` do source bundle e como
      `entities.verificado_em`.
    - `competencias`/`equipamentos`/`condicoes_acesso`/`institution`/
      `municipio` são passados adiante VERBATIM (sem inferir disponibilidade);
      alimentam `description`/embedding e `metadata.capacidades`.
    - `areas` (temas declarados pela fonte) vira `areas_raw`; ausente → [] (o
      match por competência ainda funciona via semântica sobre description).
    - Campos sem valor são omitidos ou "" — nunca `unknown` fabricado.
    """
    from radar.core.kg import schema

    name = (raw.get("name") or raw.get("nome") or "").strip()
    if not name:
        raise ValueError("pnipe record sem nome")
    url = (raw.get("url") or "").strip()
    if not url:
        raise ValueError(f"pnipe record sem url: {name!r}")
    verificado = (raw.get("verificado_em") or raw.get("data_extracao") or "").strip()
    if not verificado:
        raise ValueError(f"pnipe record sem verificado_em: {name!r}")

    address = ", ".join(
        p for p in (raw.get("endereco"), raw.get("municipio"), raw.get("uf"))
        if isinstance(p, str) and p.strip()
    )
    areas = raw.get("areas")
    if not isinstance(areas, list):
        areas = []
    competencias = raw.get("competencias")
    equipamentos = raw.get("equipamentos")
    if not isinstance(competencias, list):
        competencias = []
    if not isinstance(equipamentos, list):
        equipamentos = []

    return {
        "name": name,
        "slug": (raw.get("slug") or schema.slugify(name)),
        "source": "pnipe",
        "kind": "laboratorio",
        "url": url,
        "about": (raw.get("about") or raw.get("descricao") or "").strip(),
        "institution": (raw.get("institution") or raw.get("instituicao") or "").strip(),
        "institution_type": (raw.get("institution_type") or raw.get("tipo_instituicao") or "").strip(),
        "address": address,
        "municipio": (raw.get("municipio") or "").strip(),
        "competencias": [str(c).strip() for c in competencias if str(c).strip()],
        "equipamentos": [str(eq).strip() for eq in equipamentos if str(eq).strip()],
        "condicoes_acesso": (raw.get("condicoes_acesso") or "").strip(),
        "contact": {
            "responsavel": (raw.get("contato_responsavel") or "").strip(),
            "email": (raw.get("contato_email") or "").strip(),
            "telefone": (raw.get("contato_telefone") or "").strip(),
            "site": (raw.get("contato_site") or "").strip(),
        },
        "areas_raw": [str(a).strip() for a in areas if str(a).strip()],
        "data_extracao": verificado,
    }


class PnipeScraper(BaseScraper):
    """Ponto de entrada da fonte PNIPE: normaliza um dump curado, não faz
    scraping ao vivo (não há API pública estável — ver módulo)."""

    def __init__(self, dump_path: str | Path | None = None):
        super().__init__(source_name="PNIPE", output_subdir="ict_raw")
        self.dump_path = Path(dump_path or os.getenv("PNIPE_DUMP_PATH") or BRONZE_DIR / DEFAULT_DUMP_RELPATH)

    def extract(self) -> list[dict]:
        if not self.dump_path.exists():
            logger.warning("PNIPE: dump curado ausente (%s) — nenhum lab materializado", self.dump_path)
            return []
        raw_records = json.loads(self.dump_path.read_text(encoding="utf-8"))
        records = [parse_pnipe_record(r) for r in raw_records]
        logger.info("PNIPE: %d registros normalizados de %s", len(records), self.dump_path)
        return records


def run() -> list[dict]:
    """Roda o normalizador do dump curado e salva o bronze. Retorna os registros."""
    scraper = PnipeScraper()
    records = scraper.run()
    if records:
        scraper._save(records, prefix="pnipe")
    return records


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    out = run()
    print(f"\nPNIPE: {len(out)} laboratórios normalizados.")
    if out:
        ex = out[0]
        print(f"  exemplo: {ex['name']}")
        print(f"    competencias ({len(ex['competencias'])}): {ex['competencias'][:5]}...")
