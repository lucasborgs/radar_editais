"""
Playbook loader — compõe a COMPETÊNCIA de escrita por mecanismo (+ overlay de fonte).

Modelo (ver docs/specs/skills-by-mechanism.md): a skill NÃO é conhecimento, é
competência. Regra dura do edital vem do RAG; aqui mora o craft tácito — keyed por
`mechanism` (instrumento), com overlays de praxe por `source` (agência).

Resolução em 3 camadas, merge POR SEÇÃO (cada `## titulo` é um tipo que roteia para
um consumidor):

    playbook = mechanism/<mech>.md          (base reusável)
             + source/<source>/global.md    (praxe da agência, todo mecanismo)
             + source/<source>/<mech>.md     (praxe agência × mecanismo)

Convenções dos arquivos de playbook:
  • `# Título` (H1) é ignorado; o comentário `<!-- SEED ... -->` e os comentários
    inline `<!-- → consumidor -->` nos cabeçalhos são removidos.
  • A prosa entre o H1 e o primeiro `##` é a **Lente** (vai ao Redator).
  • Uma linha `---` encerra o conteúdo roteável: tudo abaixo (o rodapé fato↔craft)
    é meta de autoria e NÃO entra em nenhum prompt.
  • `PLACEHOLDER` no corpo → arquivo tratado como ausente (scaffolding).

Roteamento:
  • Redator (geração)          → Lente + Padrões de escrita e tom + Praxe da agência
  • ComplianceMonitor (avalia) → Heurísticas de aprovação + Anti-padrões + Praxe da agência
  • Critic                     → NÃO recebe playbook (contrato estreito: só contradição).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"

# ── Seções canônicas (nome do `##` → tipo → consumidor) ─────────────────────
SECTION_LENTE = "Lente"
SECTION_WRITING = "Padrões de escrita e tom"
SECTION_HEURISTICS = "Heurísticas de aprovação"
SECTION_ANTIPATTERNS = "Anti-padrões / red flags"
SECTION_PRAXE = "Praxe da agência"

# Praxe da agência é contexto tonal/estratégico útil aos dois consumidores.
_WRITER_ORDER = (SECTION_WRITING, SECTION_PRAXE)
_MONITOR_ORDER = (SECTION_HEURISTICS, SECTION_ANTIPATTERNS, SECTION_PRAXE)

_LOW_CONF_MARKER = (
    "CONFIANÇA: BAIXA — mecanismo não identificado; orientação genérica, "
    "não específica do instrumento."
)

# ── Vocabulário canônico de mecanismo (D1) + sinônimos (D2: investimento→credito) ──
_CANONICAL_MECHANISMS = {
    "subvencao", "credito", "bolsa", "matching", "equity", "premio", "outro",
}
_MECHANISM_SYNONYMS = {
    "investimento": "credito",
    "financiamento": "credito",
    "reembolsavel": "credito",
    "naoreembolsavel": "subvencao",
    "subvencaoeconomica": "subvencao",
    "pitch": "equity",
    "captacao": "equity",
    "embrapii": "matching",
}


def _deburr(s: str) -> str:
    """lowercase + remove acentos + colapsa não-alfanuméricos."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _normalize_source(source: str | None) -> str:
    if not source:
        return ""
    return source.lower().strip().replace(" ", "_")


def _normalize_mechanism(mechanism: str | None) -> str | None:
    """Resolve o mecanismo para um slug canônico, ou None se desconhecido/ausente."""
    if not mechanism:
        return None
    key = _deburr(mechanism)
    if key in _CANONICAL_MECHANISMS:
        return key
    if key in _MECHANISM_SYNONYMS:
        return _MECHANISM_SYNONYMS[key]
    return None


# ── Parsing ─────────────────────────────────────────────────────────────────

def _parse_playbook_file(path: Path) -> tuple[str, dict[str, str]] | None:
    """Lê um arquivo de playbook → (lente, {seção: corpo}).

    Retorna None se ausente ou PLACEHOLDER (tratado como ausente).
    """
    if not path.exists():
        return None
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("falha ao ler playbook %s: %s", path, e)
        return None
    if "PLACEHOLDER" in raw:
        logger.debug("playbook %s é placeholder — tratado como ausente", path.name)
        return None

    # Remove comentários HTML (cabeçalho SEED + anotações `<!-- → consumidor -->`).
    text = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)

    lente_buf: list[str] = []
    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal lente_buf
        if current is None:
            lente_buf = buf[:]
        else:
            sections[current] = "\n".join(buf).strip()

    for line in text.splitlines():
        s = line.strip()
        if s == "---":
            break  # rodapé fato↔craft e meta de autoria — fora do conteúdo roteável
        if s.startswith("## "):
            _flush()
            current = s[3:].strip()
            buf = []
            continue
        if s.startswith("# "):
            continue  # H1 (título) ignorado
        buf.append(line)
    _flush()

    lente = "\n".join(lente_buf).strip()
    return lente, sections


# ── Playbook composto ───────────────────────────────────────────────────────

@dataclass
class Playbook:
    """Playbook resolvido e composto, endereçável por seção."""
    mechanism: str | None
    source: str | None
    lente: str = ""
    sections: dict[str, str] = field(default_factory=dict)
    confidence: str = "ok"  # "ok" | "low" (mecanismo não identificado)

    def _render(self, order: tuple[str, ...], *, include_lente: bool) -> str:
        parts: list[str] = []
        if include_lente and self.lente:
            parts.append(f"## {SECTION_LENTE}\n{self.lente}")
        for name in order:
            body = self.sections.get(name)
            if body:
                parts.append(f"## {name}\n{body}")
        if not parts:
            return ""
        out = "\n\n".join(parts)
        if self.confidence == "low":
            out = f"{_LOW_CONF_MARKER}\n\n{out}"
        return out

    def for_writer(self) -> str:
        """Payload para o Redator: Lente + Padrões de escrita + Praxe da agência."""
        return self._render(_WRITER_ORDER, include_lente=True)

    def for_monitor(self) -> str:
        """Payload para o ComplianceMonitor: Heurísticas + Anti-padrões + Praxe."""
        return self._render(_MONITOR_ORDER, include_lente=False)

    def is_empty(self) -> bool:
        return not self.lente and not any(self.sections.values())


def load_playbook(mechanism: str | None, source: str | None = None) -> Playbook:
    """Compõe o playbook efetivo para (mechanism, source), merge por seção.

    Fallback gracioso: mechanism None/desconhecido → tenta `mechanism/_generic.md`,
    marca `confidence="low"` e NÃO bloqueia (D3). Camada/seção ausente é ignorada.
    """
    mech = _normalize_mechanism(mechanism)
    src = _normalize_source(source)
    confidence = "ok"

    base = _parse_playbook_file(_SKILLS_DIR / "mechanism" / f"{mech}.md") if mech else None
    if base is None:
        confidence = "low"
        base = _parse_playbook_file(_SKILLS_DIR / "mechanism" / "_generic.md")

    lente = ""
    merged: dict[str, list[str]] = {}

    def _add(parsed: tuple[str, dict[str, str]] | None) -> None:
        nonlocal lente
        if not parsed:
            return
        l, secs = parsed
        if l and not lente:
            lente = l
        for name, body in secs.items():
            if body:
                merged.setdefault(name, []).append(body)

    _add(base)
    if src and mech:
        _add(_parse_playbook_file(_SKILLS_DIR / "source" / src / "global.md"))
        _add(_parse_playbook_file(_SKILLS_DIR / "source" / src / f"{mech}.md"))

    sections = {name: "\n\n".join(parts) for name, parts in merged.items()}
    return Playbook(
        mechanism=mech,
        source=src or None,
        lente=lente,
        sections=sections,
        confidence=confidence,
    )


def available_skills() -> list[dict]:
    """Lista playbooks disponíveis (mecanismos + overlays de fonte). Debug/UI."""
    out: list[dict] = []
    mech_dir = _SKILLS_DIR / "mechanism"
    if mech_dir.exists():
        for f in sorted(mech_dir.glob("*.md")):
            out.append({
                "kind": "mechanism",
                "mechanism": f.stem,
                "file": str(f.relative_to(_SKILLS_DIR)),
                "size_bytes": f.stat().st_size,
            })
    src_dir = _SKILLS_DIR / "source"
    if src_dir.exists():
        for f in sorted(src_dir.glob("*/*.md")):
            out.append({
                "kind": "source_overlay",
                "source": f.parent.name,
                "mechanism": f.stem,
                "file": str(f.relative_to(_SKILLS_DIR)),
                "size_bytes": f.stat().st_size,
            })
    return out
