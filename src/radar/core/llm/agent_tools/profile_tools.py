"""Tools do agente de ProfileExtractor (Sprint 4 do Cenário B).

Quatro tools que substituem o pipeline atual ("1 fetch da home + 1 LLM call"):

  • fetch_page          — busca uma URL e extrai texto limpo (com cache local)
  • list_links_matching — lista links da página que combinam com um pattern
                          (sobre, produtos, etc.) pra o agente decidir onde
                          aprofundar
  • lookup_cnpj         — consulta dados públicos via BrasilAPI v1 (Receita)
  • submit_profile      — side effect: armazena o CompanyProfile final
                          extraído pelo agente (último step típico)

Princípios:
  • Estado por extração — `build_profile_tools(state)` recebe um dict mutável
    que serve como cache de páginas visitadas + slot do profile final.
  • Erro-como-string em todas as tools (mesmo padrão das outras sprints).
  • Limites defensivos: max 10 páginas visitadas, 12 kB de texto por página,
    cap de links retornados.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

import requests
from langchain_core.tools import BaseTool, tool

from radar.core.web.fetch import fetch_and_parse as _fetch_and_parse  # camada canônica

logger = logging.getLogger(__name__)

# Limites defensivos compartilhados.
_MAX_PAGES = 10
_MAX_LINKS = 20
_BRASILAPI_TIMEOUT = 8.0
_USER_AGENT = "Mozilla/5.0 (compatible; RadarEditais-Agent/1.0)"

# CNPJ: 14 dígitos, aceita com ou sem máscara (XX.XXX.XXX/XXXX-XX).
_CNPJ_RE = re.compile(r"\D")


@dataclass
class ExtractionState:
    """Estado mutável compartilhado entre as 4 tools de uma extração.

    fetched: cache URL → {text, title, links}. Previne loop do agente em
             mesma página + economiza HTTP.
    submitted_profile: setado pela tool submit_profile; lido pelo extractor
                       no fim do loop.
    """
    fetched: dict[str, dict[str, Any]] = field(default_factory=dict)
    submitted_profile: dict | None = None


def build_profile_tools(state: ExtractionState) -> list[BaseTool]:
    """Constrói as 4 tools do agente de extração de perfil.

    O `state` é um ExtractionState criado por extração — captura cache de
    páginas visitadas e o slot do profile final via closure. Cada extract()
    cria seu próprio ExtractionState (isolamento).
    """

    @tool
    def fetch_page(url: str) -> str:
        """Busca uma página web e retorna o texto limpo (até 12k chars).

        Use para abrir a home da empresa primeiro, depois páginas internas
        como /sobre, /produtos, /clientes que você descobriu via
        list_links_matching. Páginas já visitadas são servidas do cache.

        Args:
            url: URL absoluta da página a buscar.
        """
        if not url.strip():
            return "Erro: url vazia."
        if len(state.fetched) >= _MAX_PAGES and url not in state.fetched:
            return (
                f"Limite de {_MAX_PAGES} páginas atingido. Use o que já "
                "coletou para extrair o perfil e chame submit_profile."
            )

        cached = state.fetched.get(url)
        if cached is not None:
            return (
                f"[cache] {cached['title']}\n\n"
                f"<dados_externos>\n{cached['text']}\n</dados_externos>"
                if cached.get("text")
                else f"[cache] Página '{url}' já buscada antes e não tinha texto útil."
            )

        try:
            data = _fetch_and_parse(url)
        except requests.HTTPError as e:
            state.fetched[url] = {"text": "", "title": url, "links": []}
            return (
                f"Erro HTTP {e.response.status_code} em {url}. "
                "Tente outro link ou prossiga com o que tem."
            )
        except requests.Timeout:
            return f"Timeout ao buscar {url}. Tente outro link."
        except Exception as e:
            logger.warning("fetch_page(%s) falhou: %s", url, e)
            return f"Erro ao buscar {url}: {e}. Tente outro link."

        state.fetched[url] = data
        if not data["text"]:
            return f"Página '{url}' carregou mas sem texto útil. Tente outro link."
        return f"{data['title']}\n\n<dados_externos>\n{data['text']}\n</dados_externos>"

    @tool
    def list_links_matching(url: str, pattern: str) -> str:
        """Lista links da página `url` cujo texto ou href contém `pattern`
        (case-insensitive substring).

        Use para descobrir páginas internas úteis: 'sobre', 'produtos',
        'clientes', 'tecnologia', 'contato'. Retorna até 20 matches. A
        página precisa ter sido buscada antes com fetch_page; se não,
        busca antes automaticamente.

        Args:
            url: URL absoluta da página onde listar links.
            pattern: substring a procurar no texto do link ou no href
                (ex.: 'sobre', 'about', 'produto', 'contato').
        """
        if not pattern.strip():
            return "Erro: pattern vazio."

        data = state.fetched.get(url)
        if data is None:
            # Busca on-demand pra que a tool seja útil mesmo sem fetch prévio.
            try:
                data = _fetch_and_parse(url)
                state.fetched[url] = data
            except Exception as e:
                return f"Erro ao buscar {url} para listar links: {e}."

        pat = pattern.lower()
        matches = [
            link for link in data.get("links", [])
            if pat in link["text"].lower() or pat in link["href"].lower()
        ]
        if not matches:
            return f"Nenhum link em {url} combina com '{pattern}'."

        lines = [f"Links em {url} combinando com '{pattern}' ({len(matches)} encontrados):"]
        for link in matches[:_MAX_LINKS]:
            text = link["text"] or "(sem texto)"
            lines.append(f"  • {text} → {link['href']}")
        if len(matches) > _MAX_LINKS:
            lines.append(f"  ... e mais {len(matches) - _MAX_LINKS} links")
        return "\n".join(lines)

    @tool
    def lookup_cnpj(cnpj: str) -> str:
        """Consulta dados públicos de um CNPJ na Receita Federal via BrasilAPI.

        Use quando achar um CNPJ no site da empresa (geralmente no rodapé,
        página de contato ou termos de uso). Retorna razão social, nome
        fantasia, situação cadastral, atividade principal, porte.

        Args:
            cnpj: CNPJ em qualquer formato (com ou sem máscara).
                Apenas os 14 dígitos são usados.
        """
        digits = _CNPJ_RE.sub("", cnpj or "")
        if len(digits) != 14:
            return (
                f"Erro: '{cnpj}' não parece um CNPJ válido "
                "(esperado 14 dígitos, com ou sem máscara)."
            )

        try:
            resp = requests.get(
                f"https://brasilapi.com.br/api/cnpj/v1/{digits}",
                headers={"User-Agent": _USER_AGENT},
                timeout=_BRASILAPI_TIMEOUT,
            )
        except requests.Timeout:
            return "BrasilAPI timeout. Prossiga sem os dados da Receita."
        except Exception as e:
            return f"Erro ao consultar BrasilAPI: {e}. Prossiga sem isso."

        if resp.status_code == 404:
            return f"CNPJ {digits} não encontrado na Receita."
        if resp.status_code >= 400:
            return f"BrasilAPI retornou status {resp.status_code}. Prossiga sem isso."

        try:
            data = resp.json()
        except ValueError:
            return "BrasilAPI devolveu resposta inválida. Prossiga sem isso."

        parts = [f"Dados públicos do CNPJ {digits}:"]
        for key, label in [
            ("razao_social", "Razão social"),
            ("nome_fantasia", "Nome fantasia"),
            ("descricao_situacao_cadastral", "Situação"),
            ("data_inicio_atividade", "Início de atividade"),
            ("porte", "Porte"),
            ("natureza_juridica", "Natureza jurídica"),
            ("cnae_fiscal_descricao", "Atividade principal"),
            ("municipio", "Município"),
            ("uf", "UF"),
        ]:
            val = data.get(key)
            if val:
                parts.append(f"  {label}: {val}")
        return "\n".join(parts)

    @tool
    def submit_profile(
        nome: str,
        tipo_entidade: str,
        one_liner: str,
        descricao_atividades: str,
        solution_summary: str = "",
        tamanho_empresa: str = "",
        trl: int | None = None,
        uf: str = "",
        ano_fundacao: int | None = None,
        faturamento_anual: float | None = None,
    ) -> str:
        """Submete o perfil extraído da empresa. Termine sua investigação
        chamando esta tool exatamente uma vez quando tiver coletado dados
        suficientes para preencher os campos.

        Args:
            nome: razão social ou nome fantasia da empresa.
            tipo_entidade: "empresa" | "startup" | "universidade" | "ICT".
            one_liner: 1 frase descrevendo o que a empresa faz.
            descricao_atividades: 2-3 frases detalhando atividades, produtos,
                serviços, área de atuação.
            solution_summary: 1 frase sobre a solução principal (se for
                startup/empresa de tecnologia). Vazio se não souber.
            tamanho_empresa: "MEI" | "ME" | "EPP" | "MEDIO" | "GRANDE". Vazio
                se não souber inferir.
            trl: 1-9 (technology readiness level). None se não souber.
            uf: sigla de 2 letras do estado-sede (ex.: "SP"). Se consultou o
                CNPJ, use o campo "UF" da Receita. Vazio se não souber.
            ano_fundacao: ano de constituição (ex.: 2018). Se consultou o CNPJ,
                derive do ano de "Início de atividade". None se não souber.
            faturamento_anual: receita bruta anual em R$ (número). Raramente
                consta no site/Receita — preencha só com evidência. None se não.
        """
        if not nome.strip() or not tipo_entidade.strip():
            return (
                "Erro: nome e tipo_entidade são obrigatórios e não-vazios. "
                "Continue investigando até achar pelo menos esses dois."
            )
        if state.submitted_profile is not None:
            return (
                "Erro: profile já foi submetido neste fluxo. "
                "Apenas uma chamada de submit_profile por extração."
            )

        valid_tipos = {"empresa", "startup", "universidade", "ict"}
        if tipo_entidade.lower() not in valid_tipos:
            return (
                f"Erro: tipo_entidade '{tipo_entidade}' inválido. "
                f"Use um de: {', '.join(sorted(valid_tipos))}."
            )

        valid_portes = {"MEI", "ME", "EPP", "MEDIO", "GRANDE"}
        if tamanho_empresa and tamanho_empresa.upper() not in valid_portes:
            return (
                f"Erro: tamanho_empresa '{tamanho_empresa}' inválido. "
                f"Use um de: {', '.join(sorted(valid_portes))} ou vazio."
            )

        if trl is not None and not (1 <= int(trl) <= 9):
            return f"Erro: trl={trl} fora do range 1-9. Use None se não souber."

        uf_clean = (uf or "").strip().upper()
        if uf_clean and not re.fullmatch(r"[A-Z]{2}", uf_clean):
            return f"Erro: uf='{uf}' inválida. Use a sigla de 2 letras (ex.: SP) ou vazio."

        from datetime import date
        if ano_fundacao is not None and not (1900 <= int(ano_fundacao) <= date.today().year):
            return f"Erro: ano_fundacao={ano_fundacao} fora do range 1900-{date.today().year}. Use None se não souber."

        state.submitted_profile = {
            "nome": nome.strip(),
            "tipo_entidade": tipo_entidade.lower(),
            "one_liner": one_liner.strip(),
            "descricao_atividades": descricao_atividades.strip(),
            "solution_summary": solution_summary.strip(),
            "tamanho_empresa": tamanho_empresa.upper() if tamanho_empresa else "",
            "trl": int(trl) if trl is not None else None,
            "uf": uf_clean,
            "ano_fundacao": int(ano_fundacao) if ano_fundacao is not None else None,
            "faturamento_anual": float(faturamento_anual) if faturamento_anual is not None else None,
        }
        return (
            "Perfil submetido com sucesso. Responda ao usuário com um "
            "resumo curto (1-2 frases) do que foi extraído e termine."
        )

    # CNPJ/BrasilAPI (Receita) DESATIVADO por padrão (decisão 2026-06-21): não
    # depender de API externa frágil — e faturamento nem consta no CNPJ. Reversível
    # via CNPJ_LOOKUP_ENABLED=true. O EXTRACTOR_AGENT_SYSTEM ainda menciona
    # lookup_cnpj; se o agente a chamar com o gate fechado, o runtime devolve
    # erro-string e o modelo segue sem os dados (degradação graciosa).
    tools = [fetch_page, list_links_matching, submit_profile]
    if os.getenv("CNPJ_LOOKUP_ENABLED", "false").lower() == "true":
        tools.insert(2, lookup_cnpj)
    return tools
