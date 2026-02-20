"""
Radar de Editais - Interface Principal

Streamlit app com:
- Dashboard visual (bolhas por tema, fonte, timeline)
- Matching empresa↔editais (ranking por compatibilidade)
- Chat conversacional com LLM
- Análise de aderência detalhada (LLM)
- Geração de propostas técnicas (LLM)
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from pathlib import Path
from collections import Counter

from rag_service import RAGService
from matching_engine import MatchingEngine
from user_profile import CompanyProfile
from analyst_agent import AdherenceAnalyzer
from writer_agent import ProposalDrafter

# =============================================================================
# CONFIGURAÇÃO DA PÁGINA
# =============================================================================

st.set_page_config(
    page_title="Radar de Editais",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .score-high { color: #2e7d32; font-size: 3rem; font-weight: bold; }
    .score-mid  { color: #f57f17; font-size: 3rem; font-weight: bold; }
    .score-low  { color: #c62828; font-size: 3rem; font-weight: bold; }
    .risk-card {
        background-color: #fff3e0; border-left: 4px solid #e65100;
        padding: 8px 12px; margin: 4px 0; border-radius: 4px;
    }
    .positive-card {
        background-color: #e8f5e9; border-left: 4px solid #2e7d32;
        padding: 8px 12px; margin: 4px 0; border-radius: 4px;
    }
    .doc-card {
        background-color: #e3f2fd; border-left: 4px solid #1565c0;
        padding: 8px 12px; margin: 4px 0; border-radius: 4px;
    }
    .match-card {
        background-color: #f5f5f5; border-left: 4px solid #455a64;
        padding: 10px 14px; margin: 6px 0; border-radius: 4px;
    }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# INICIALIZAÇÃO
# =============================================================================

@st.cache_resource
def get_matching_engine():
    return MatchingEngine()

@st.cache_resource
def get_rag_service():
    return RAGService()

@st.cache_resource
def get_analyzer():
    return AdherenceAnalyzer()

@st.cache_resource
def get_drafter():
    return ProposalDrafter()


def init_session_state():
    defaults = {
        "messages": [],
        "task_type": "general",
        "filters": {},
        "selected_edital": None,
        "analysis_result": None,
        "draft_result": None,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_company_profile() -> CompanyProfile:
    return CompanyProfile(
        nome=st.session_state.get("cp_nome", ""),
        cnpj=st.session_state.get("cp_cnpj", ""),
        descricao_atividades=st.session_state.get("cp_atividades", ""),
        cnaes=[c.strip() for c in st.session_state.get("cp_cnaes", "").split(",") if c.strip()],
        portfolio_projetos=st.session_state.get("cp_portfolio", ""),
        tamanho_empresa=st.session_state.get("cp_porte", ""),
        localizacao=st.session_state.get("cp_localizacao", ""),
        capital_social=st.session_state.get("cp_capital", None),
        certificacoes=[c.strip() for c in st.session_state.get("cp_certificacoes", "").split(",") if c.strip()],
        equipe_resumo=st.session_state.get("cp_equipe", ""),
    )


# =============================================================================
# SIDEBAR
# =============================================================================

def render_sidebar():
    with st.sidebar:
        st.title("📡 Radar de Editais")
        st.caption("Consultor Digital de Fomento")

        with st.expander("🏢 Perfil da Empresa", expanded=False):
            st.text_input("Nome da Empresa", key="cp_nome")
            st.text_input("CNPJ", key="cp_cnpj", placeholder="00.000.000/0001-00")
            st.selectbox("Porte", ["", "MEI", "ME", "EPP", "MEDIO", "GRANDE"], key="cp_porte")
            st.text_input("Localização (UF/Cidade)", key="cp_localizacao", placeholder="São Paulo/SP")
            st.number_input("Capital Social (R$)", min_value=0.0, step=10000.0, key="cp_capital", format="%.2f")
            st.text_area("Atividades Principais", key="cp_atividades", height=80,
                         placeholder="Descreva as atividades da empresa...")
            st.text_input("CNAEs (separados por vírgula)", key="cp_cnaes", placeholder="6201-5/01, 6202-3/00")
            st.text_area("Portfólio de Projetos", key="cp_portfolio", height=80,
                         placeholder="Projetos relevantes executados...")
            st.text_area("Equipe", key="cp_equipe", height=68,
                         placeholder="Resumo da equipe técnica...")
            st.text_input("Certificações (separadas por vírgula)", key="cp_certificacoes",
                          placeholder="ISO 9001, PMP, CMMI")

            profile = get_company_profile()
            pct = profile.completion_pct()
            st.progress(pct / 100, text=f"Perfil {pct}% completo")

            if st.button("💾 Salvar Perfil", use_container_width=True):
                path = profile.save()
                st.success(f"Salvo em {path}")

        st.divider()

        # Filtros
        st.subheader("🔍 Filtros")
        engine = get_matching_engine()
        available_sources = engine.get_sources() if engine.df is not None and not engine.df.empty else []
        sources = ["Todas"] + available_sources
        selected_source = st.selectbox("Fonte:", sources, index=0)
        selected_status = st.selectbox("Status:", ["Todos", "ABERTA", "ENCERRADA", "FLUXO_CONTINUO"], index=0)

        filters = {}
        if selected_source != "Todas":
            filters["source"] = [selected_source]
        if selected_status != "Todos":
            filters["status"] = selected_status
        st.session_state.filters = filters if filters else None

        st.divider()
        if st.button("🗑️ Limpar Conversa", use_container_width=True):
            st.session_state.messages = []
            st.session_state.selected_edital = None
            st.session_state.analysis_result = None
            st.session_state.draft_result = None
            st.rerun()


# =============================================================================
# ABA: DASHBOARD
# =============================================================================

def render_tab_dashboard(engine: MatchingEngine):
    st.header("📊 Panorama de Editais")

    df = engine.df
    if df is None or df.empty:
        st.warning("Nenhum dado disponível. Execute o pipeline ETL primeiro.")
        return

    # Métricas gerais
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total de Editais", len(df))
    with col2:
        abertos = len(df[df["status"] == "ABERTA"]) if "status" in df.columns else 0
        st.metric("Abertos", abertos)
    with col3:
        st.metric("Fontes", df["source"].nunique())
    with col4:
        all_themes = []
        if "themes" in df.columns:
            for t in df["themes"]:
                if hasattr(t, 'tolist'):
                    all_themes.extend(t.tolist())
                elif isinstance(t, list):
                    all_themes.extend(t)
        st.metric("Temáticas", len(set(all_themes)))

    st.divider()

    # ---- Gráficos ----
    col_left, col_right = st.columns(2)

    with col_left:
        # Editais por fonte
        source_counts = df["source"].value_counts().reset_index()
        source_counts.columns = ["Fonte", "Quantidade"]
        fig_source = px.bar(
            source_counts, x="Fonte", y="Quantidade",
            color="Fonte", title="Editais por Fonte",
            color_discrete_sequence=px.colors.qualitative.Set2
        )
        fig_source.update_layout(showlegend=False, height=350)
        st.plotly_chart(fig_source, use_container_width=True)

    with col_right:
        # Editais por status
        if "status" in df.columns:
            status_counts = df["status"].value_counts().reset_index()
            status_counts.columns = ["Status", "Quantidade"]
            fig_status = px.pie(
                status_counts, names="Status", values="Quantidade",
                title="Distribuição por Status",
                color_discrete_sequence=px.colors.qualitative.Pastel
            )
            fig_status.update_layout(height=350)
            st.plotly_chart(fig_status, use_container_width=True)

    # ---- Bubble chart: Temáticas ----
    if all_themes:
        st.subheader("🫧 Mapa de Temáticas")
        theme_counts = Counter(all_themes)

        # Associar fonte predominante a cada tema
        theme_source = {}
        if "themes" in df.columns:
            for _, row in df.iterrows():
                themes = row.get("themes", [])
                if hasattr(themes, 'tolist'):
                    themes = themes.tolist()
                if isinstance(themes, list):
                    for t in themes:
                        if t not in theme_source:
                            theme_source[t] = row.get("source", "")

        bubble_data = pd.DataFrame([
            {
                "Tema": theme,
                "Editais": count,
                "Fonte Principal": theme_source.get(theme, ""),
            }
            for theme, count in theme_counts.most_common(30)
        ])

        fig_bubble = px.scatter(
            bubble_data, x="Tema", y="Editais",
            size="Editais", color="Fonte Principal",
            title="Temáticas por volume de editais",
            size_max=50,
            color_discrete_sequence=px.colors.qualitative.Set2,
        )
        fig_bubble.update_layout(height=450, xaxis_tickangle=-45)
        st.plotly_chart(fig_bubble, use_container_width=True)

    # ---- Timeline de deadlines ----
    if "deadline_date" in df.columns:
        df_timeline = df.copy()
        df_timeline["deadline_date"] = pd.to_datetime(df_timeline["deadline_date"], errors="coerce")
        df_timeline = df_timeline.dropna(subset=["deadline_date"])
        df_timeline = df_timeline[df_timeline["deadline_date"] >= pd.Timestamp.now()]
        df_timeline = df_timeline.sort_values("deadline_date")

        if not df_timeline.empty:
            st.subheader("📅 Timeline de Prazos")
            fig_timeline = px.scatter(
                df_timeline.head(30),
                x="deadline_date", y="source",
                color="source",
                hover_data=["title"],
                title="Próximos prazos",
                color_discrete_sequence=px.colors.qualitative.Set2,
            )
            fig_timeline.update_layout(height=350, xaxis_title="Prazo", yaxis_title="Fonte")
            st.plotly_chart(fig_timeline, use_container_width=True)


# =============================================================================
# ABA: MATCHING
# =============================================================================

def render_tab_matching(engine: MatchingEngine):
    st.header("🎯 Matching Empresa ↔ Editais")

    profile = get_company_profile()
    if not profile.is_complete():
        st.warning("⚠️ Preencha o Perfil da Empresa na barra lateral (ao menos Nome e Atividades).")
        return

    st.success(f"Empresa: **{profile.nome}** | Porte: {profile.tamanho_empresa or 'N/I'} | {profile.localizacao or 'N/I'}")

    # Filtros
    filters = st.session_state.get("filters")
    source_filter = filters.get("source") if filters else None
    status_filter = filters.get("status") if filters else "ABERTA"

    if st.button("🔄 Calcular Matching", type="primary", use_container_width=True):
        with st.spinner("Calculando compatibilidade..."):
            matches = engine.match(
                profile,
                top_k=20,
                source_filter=source_filter,
                status_filter=status_filter,
            )
        st.session_state["_matches"] = matches

    matches = st.session_state.get("_matches", [])
    if not matches:
        st.info("Clique em 'Calcular Matching' para ver os editais mais compatíveis.")
        return

    # Resumo visual
    st.subheader(f"Top {len(matches)} editais compatíveis")

    # Score distribution
    scores = [m["total_score"] for m in matches]
    col1, col2, col3 = st.columns(3)
    with col1:
        alta = sum(1 for s in scores if s >= 75)
        st.metric("Alta Aderência", alta, help="Score >= 75")
    with col2:
        media = sum(1 for s in scores if 50 <= s < 75)
        st.metric("Média Aderência", media, help="Score 50-74")
    with col3:
        baixa = sum(1 for s in scores if s < 50)
        st.metric("Baixa Aderência", baixa, help="Score < 50")

    st.divider()

    # Lista de matches
    for i, m in enumerate(matches):
        score = m["total_score"]
        if score >= 75:
            emoji = "🟢"
        elif score >= 50:
            emoji = "🟡"
        else:
            emoji = "🔴"

        with st.expander(f"{emoji} [{score:.0f}pts] {m['title'][:70]} — {m['source']}", expanded=(i < 3)):
            col_score, col_info = st.columns([1, 3])

            with col_score:
                st.markdown(f"### {score:.0f}/100")
                st.caption(m["recommendation"].replace("_", " "))

            with col_info:
                st.markdown(f"**Fonte:** {m['source']} | **Categoria:** {m['category']}")
                st.markdown(f"**Prazo:** {m.get('deadline_date') or 'Não informado'}")
                if m.get("url"):
                    st.markdown(f"🔗 [Acessar edital]({m['url']})")

            # Breakdown
            st.caption("Breakdown do score:")
            breakdown_cols = st.columns(len(m["breakdown"]))
            for j, (dim, val) in enumerate(m["breakdown"].items()):
                with breakdown_cols[j]:
                    label = dim.replace("_match", "").replace("_", " ").title()
                    max_val = 25  # Approximate max
                    st.progress(min(val / max_val, 1.0), text=f"{label}: {val:.0f}")

            # Reasons
            with st.expander("Detalhes da análise"):
                for reason in m.get("reasons", []):
                    st.markdown(f"- {reason}")

            st.markdown(f"*{m['description_preview'][:200]}...*")

            # Botão para análise detalhada
            if st.button(f"📊 Análise Detalhada", key=f"match_analyze_{i}"):
                # Buscar edital completo
                edital_data = engine.get_edital_by_id(m["edital_id"])
                if edital_data:
                    st.session_state.selected_edital = edital_data
                    st.session_state.analysis_result = None
                    st.session_state.draft_result = None
                    st.info("Vá para a aba **Análise** para ver a análise detalhada via LLM.")


# =============================================================================
# ABA: CHAT
# =============================================================================

def render_tab_chat(service: RAGService):
    st.header("💬 Chat")

    # Atualiza perfil no service
    profile = get_company_profile()
    if profile.is_complete():
        service.set_profile(profile)

    # Modo
    task_options = {"general": "💬 Geral", "match": "🎯 Match", "explore": "🔍 Explorar"}
    cols = st.columns(len(task_options))
    for i, (key, label) in enumerate(task_options.items()):
        with cols[i]:
            if st.button(label, use_container_width=True,
                         type="primary" if st.session_state.task_type == key else "secondary"):
                st.session_state.task_type = key
                st.rerun()

    st.divider()

    # Histórico
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("sources"):
                with st.expander("📚 Fontes"):
                    for src in msg["sources"][:5]:
                        score_val = src.get("score", 0)
                        score_str = f"{score_val:.0f}pts" if isinstance(score_val, (int, float)) and score_val > 1 else f"{score_val:.0%}"
                        st.markdown(
                            f"**{src.get('title', 'N/A')[:60]}** | "
                            f"`{src.get('source', 'N/A')}` | "
                            f"Score: `{score_str}` | "
                            f"🔗 {src.get('url', 'N/A')}"
                        )

    # Input
    if prompt := st.chat_input("Pergunte sobre editais..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        with st.chat_message("assistant"):
            with st.spinner("🔍 Processando..."):
                result = service.generate(
                    query=prompt,
                    task_type=st.session_state.task_type,
                    filters=st.session_state.filters,
                    top_k=10,
                )
            st.markdown(result["answer"])
            if result.get("sources"):
                with st.expander("📚 Fontes"):
                    for src in result["sources"][:5]:
                        score_val = src.get("score", 0)
                        score_str = f"{score_val:.0f}pts" if isinstance(score_val, (int, float)) and score_val > 1 else f"{score_val:.0%}"
                        st.markdown(
                            f"**{src.get('title', 'N/A')[:60]}** | "
                            f"`{src.get('source', 'N/A')}` | "
                            f"Score: `{score_str}` | "
                            f"🔗 {src.get('url', 'N/A')}"
                        )

            st.session_state.messages.append({
                "role": "assistant",
                "content": result["answer"],
                "sources": result.get("sources", []),
            })


# =============================================================================
# ABA: ANÁLISE DE ADERÊNCIA
# =============================================================================

def render_score_card(score: int):
    if score >= 70:
        css_class, emoji, label = "score-high", "🟢", "ALTA ADERÊNCIA"
    elif score >= 40:
        css_class, emoji, label = "score-mid", "🟡", "MÉDIA ADERÊNCIA"
    else:
        css_class, emoji, label = "score-low", "🔴", "BAIXA ADERÊNCIA"

    col1, col2 = st.columns([1, 3])
    with col1:
        st.markdown(f'<div class="{css_class}">{emoji} {score}</div>', unsafe_allow_html=True)
    with col2:
        st.markdown(f"### {label}")


def render_tab_analysis(analyzer: AdherenceAnalyzer):
    st.header("📊 Análise de Aderência (LLM)")

    profile = get_company_profile()
    if not profile.is_complete():
        st.warning("⚠️ Preencha o Perfil da Empresa na barra lateral.")
        return

    st.success(f"Perfil: **{profile.nome}** ({profile.completion_pct()}% completo)")

    edital = st.session_state.get("selected_edital")
    if not edital:
        st.info("📋 Selecione um edital na aba **Matching** (botão 'Análise Detalhada').")
        return

    title = str(edital.get("title", "Edital"))[:80]
    st.subheader(f"📋 {title}")
    st.caption(f"Fonte: {edital.get('source')} | Status: {edital.get('status', 'N/A')}")

    # Executar análise
    if st.session_state.analysis_result is None:
        edital_text = str(edital.get("description", ""))
        edital_meta = {
            "title": edital.get("title"),
            "source": edital.get("source"),
            "deadline_date": edital.get("deadline_date"),
            "value_brl": edital.get("value_brl"),
            "category": edital.get("category"),
        }

        with st.spinner("🤖 Analisando aderência via LLM..."):
            analysis = analyzer.calculate_score(edital_text, profile, edital_meta)
        st.session_state.analysis_result = analysis

    analysis = st.session_state.analysis_result

    if not analysis.get("success"):
        st.error(f"Erro na análise: {analysis.get('error')}")
        if st.button("🔄 Tentar Novamente"):
            st.session_state.analysis_result = None
            st.rerun()
        return

    # Score card
    render_score_card(analysis["score"])

    # Recomendação
    rec = analysis.get("recommendation", "AVALIAR_COM_CAUTELA")
    rec_map = {
        "SUBMETER": ("✅ Recomendação: Submeter proposta", "success"),
        "AVALIAR_COM_CAUTELA": ("⚠️ Recomendação: Avaliar com cautela", "warning"),
        "NAO_SUBMETER": ("🚫 Recomendação: Não submeter", "error"),
    }
    msg, method = rec_map.get(rec, rec_map["AVALIAR_COM_CAUTELA"])
    getattr(st, method)(msg)

    if analysis.get("summary"):
        st.markdown(f"**Resumo:** {analysis['summary']}")

    col_pos, col_risk = st.columns(2)

    with col_pos:
        st.markdown("#### ✅ Pontos Positivos")
        for p in analysis.get("positives", []):
            st.markdown(f'<div class="positive-card">{p}</div>', unsafe_allow_html=True)
        if not analysis.get("positives"):
            st.caption("Nenhum ponto positivo identificado.")

    with col_risk:
        st.markdown("#### ⚠️ Riscos")
        for r in analysis.get("risks", []):
            st.markdown(f'<div class="risk-card">{r}</div>', unsafe_allow_html=True)
        if not analysis.get("risks"):
            st.caption("Nenhum risco identificado.")

    if analysis.get("missing_docs"):
        st.markdown("#### 📄 Documentos Exigidos")
        for doc in analysis["missing_docs"]:
            st.markdown(f'<div class="doc-card">📋 {doc}</div>', unsafe_allow_html=True)

    st.divider()
    if st.button("✍️ Gerar Rascunho de Proposta", type="primary", use_container_width=True):
        st.session_state.draft_result = "__GENERATE__"
        st.rerun()


# =============================================================================
# ABA: PROPOSTA
# =============================================================================

def render_tab_proposal(drafter: ProposalDrafter):
    st.header("✍️ Rascunho de Proposta")

    profile = get_company_profile()
    edital = st.session_state.get("selected_edital")

    if not profile.is_complete():
        st.warning("⚠️ Preencha o Perfil da Empresa na barra lateral.")
        return

    if not edital:
        st.info("📋 Selecione um edital na aba **Matching** e analise-o primeiro.")
        return

    st.success(f"Edital: **{str(edital.get('title', 'N/A'))[:70]}** | Empresa: **{profile.nome}**")

    style = st.selectbox("Estilo de escrita:", ["formal", "consultivo", "academico"], index=0)

    draft = st.session_state.get("draft_result")
    should_generate = draft == "__GENERATE__" or st.button("Gerar Proposta", type="primary")

    if should_generate and (draft == "__GENERATE__" or draft is None or not draft.get("success")):
        edital_text = str(edital.get("description", ""))
        edital_meta = {
            "title": edital.get("title"),
            "source": edital.get("source"),
            "deadline_date": edital.get("deadline_date"),
            "value_brl": edital.get("value_brl"),
        }

        with st.spinner("✍️ Gerando rascunho... (pode levar 1-2 minutos)"):
            draft = drafter.draft_proposal(edital_text, profile, style=style, edital_metadata=edital_meta)
        st.session_state.draft_result = draft

    if draft and isinstance(draft, dict) and draft.get("success"):
        st.divider()
        st.markdown(draft["proposal_text"])
        st.divider()
        st.download_button(
            "📥 Baixar Proposta (.md)",
            data=draft["proposal_text"],
            file_name=f"proposta_{edital.get('source', 'edital')}_{datetime.now():%Y%m%d}.md",
            mime="text/markdown",
            use_container_width=True
        )
    elif draft and isinstance(draft, dict) and not draft.get("success"):
        st.error(f"Erro ao gerar proposta: {draft.get('error')}")


# =============================================================================
# MAIN
# =============================================================================

def main():
    init_session_state()

    engine = get_matching_engine()
    service = get_rag_service()
    analyzer = get_analyzer()
    drafter = get_drafter()

    render_sidebar()

    tab_dashboard, tab_matching, tab_chat, tab_analysis, tab_proposal = st.tabs([
        "📊 Dashboard", "🎯 Matching", "💬 Chat", "📋 Análise", "✍️ Proposta"
    ])

    with tab_dashboard:
        render_tab_dashboard(engine)

    with tab_matching:
        render_tab_matching(engine)

    with tab_chat:
        render_tab_chat(service)

    with tab_analysis:
        render_tab_analysis(analyzer)

    with tab_proposal:
        render_tab_proposal(drafter)


if __name__ == "__main__":
    main()
