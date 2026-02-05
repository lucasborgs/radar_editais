"""
Interface Conversacional - Radar de Editais

Aplicação Streamlit para interação com o RAG Service.

Autor: Pipeline Radar Editais
"""

import streamlit as st
from datetime import datetime

from rag_service import RAGService

# =============================================================================
# CONFIGURAÇÃO DA PÁGINA
# =============================================================================

st.set_page_config(
    page_title="Radar de Editais",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# CSS customizado
st.markdown("""
<style>
    .stApp {
        max-width: 1200px;
        margin: 0 auto;
    }
    .source-card {
        background-color: #f0f2f6;
        border-radius: 8px;
        padding: 10px;
        margin: 5px 0;
        font-size: 0.85em;
    }
    .task-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.75em;
        font-weight: bold;
    }
    .badge-general { background-color: #e3f2fd; color: #1565c0; }
    .badge-analysis { background-color: #fff3e0; color: #e65100; }
    .badge-summary { background-color: #e8f5e9; color: #2e7d32; }
    .badge-draft { background-color: #f3e5f5; color: #7b1fa2; }
</style>
""", unsafe_allow_html=True)


# =============================================================================
# INICIALIZAÇÃO
# =============================================================================

@st.cache_resource
def get_rag_service():
    """Inicializa o RAG Service (cached)."""
    return RAGService()


def init_session_state():
    """Inicializa variáveis de sessão."""
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "task_type" not in st.session_state:
        st.session_state.task_type = "general"
    if "filters" not in st.session_state:
        st.session_state.filters = {}


# =============================================================================
# SIDEBAR
# =============================================================================

def render_sidebar():
    """Renderiza a barra lateral com configurações."""
    with st.sidebar:
        st.title("📡 Radar de Editais")
        st.caption("Assistente de Fomento com IA")

        st.divider()

        # Tipo de tarefa
        st.subheader("🎯 Modo de Resposta")
        task_options = {
            "general": "💬 Geral - Respostas informativas",
            "analysis": "📊 Análise - Aderência empresa/edital",
            "summary": "📋 Resumo - Resumo executivo",
            "draft": "✍️ Rascunho - Proposta inicial"
        }

        selected_task = st.radio(
            "Selecione o modo:",
            options=list(task_options.keys()),
            format_func=lambda x: task_options[x],
            index=list(task_options.keys()).index(st.session_state.task_type),
            label_visibility="collapsed"
        )
        st.session_state.task_type = selected_task

        st.divider()

        # Filtros
        st.subheader("🔍 Filtros")

        # Fonte
        sources = ["Todas", "FAPESP", "CNPQ", "FINEP", "BNDES", "PNCP",
                   "LEMANN", "SERRAPILHEIRA", "ARAPYAU", "ITAU_SOCIAL"]
        selected_source = st.selectbox(
            "Fonte:",
            sources,
            index=0
        )

        # Status
        status_options = ["Todos", "ABERTA", "ENCERRADA", "FLUXO_CONTINUO"]
        selected_status = st.selectbox(
            "Status:",
            status_options,
            index=0
        )

        # Atualizar filtros
        filters = {}
        if selected_source != "Todas":
            filters["source"] = [selected_source]
        if selected_status != "Todos":
            filters["status"] = [selected_status]
        st.session_state.filters = filters if filters else None

        st.divider()

        # Ações
        if st.button("🗑️ Limpar Conversa", use_container_width=True):
            st.session_state.messages = []
            st.rerun()

        st.divider()

        # Info
        st.caption("**Exemplos de perguntas:**")
        examples = [
            "Quais editais de IA estão abertos?",
            "Qual o prazo do SPRINT FAPESP?",
            "Resuma os requisitos do edital PIPE",
            "Minha startup faz drones. Qual edital serve?"
        ]
        for ex in examples:
            if st.button(f"💡 {ex[:35]}...", key=f"ex_{hash(ex)}", use_container_width=True):
                return ex

        st.divider()
        st.caption(f"Modelo: Ollama/llama3.2")

    return None


# =============================================================================
# CHAT
# =============================================================================

def format_sources(sources: list) -> str:
    """Formata as fontes para exibição."""
    if not sources:
        return ""

    output = "\n\n---\n**📚 Fontes consultadas:**\n"
    for i, src in enumerate(sources[:5], 1):
        title = src.get("title", "Sem título")[:60]
        source = src.get("source", "N/A")
        url = src.get("url", "")
        score = src.get("score", 0)

        output += f"\n{i}. **[{source}]** {title}"
        if url:
            output += f"\n   🔗 [{url[:50]}...]({url})"
        output += f"\n   📈 Relevância: {score:.2%}\n"

    return output


def get_task_badge(task_type: str) -> str:
    """Retorna o badge HTML para o tipo de tarefa."""
    badges = {
        "general": ("💬 Geral", "badge-general"),
        "analysis": ("📊 Análise", "badge-analysis"),
        "summary": ("📋 Resumo", "badge-summary"),
        "draft": ("✍️ Rascunho", "badge-draft")
    }
    label, css_class = badges.get(task_type, ("💬 Geral", "badge-general"))
    return f'<span class="task-badge {css_class}">{label}</span>'


def render_chat():
    """Renderiza a interface de chat."""
    st.title("💬 Chat com Editais")

    # Mostrar modo atual
    task_badge = get_task_badge(st.session_state.task_type)
    filter_info = ""
    if st.session_state.filters:
        filter_parts = []
        if "source" in st.session_state.filters:
            filter_parts.append(f"Fonte: {st.session_state.filters['source'][0]}")
        if "status" in st.session_state.filters:
            filter_parts.append(f"Status: {st.session_state.filters['status'][0]}")
        filter_info = " | " + " | ".join(filter_parts)

    st.markdown(f"Modo: {task_badge}{filter_info}", unsafe_allow_html=True)
    st.divider()

    # Container de mensagens
    chat_container = st.container()

    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if msg["role"] == "assistant" and "sources" in msg:
                    with st.expander("📚 Ver fontes consultadas"):
                        for src in msg["sources"][:5]:
                            st.markdown(f"""
                            **{src.get('title', 'N/A')[:60]}**
                            Fonte: `{src.get('source', 'N/A')}` | Score: `{src.get('score', 0):.2%}`
                            🔗 {src.get('url', 'N/A')}
                            """)

    return chat_container


def process_query(query: str, service: RAGService):
    """Processa uma pergunta e retorna a resposta."""
    with st.spinner("🔍 Buscando editais relevantes..."):
        result = service.generate(
            query=query,
            task_type=st.session_state.task_type,
            filters=st.session_state.filters,
            top_k=5
        )
    return result


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Função principal da aplicação."""
    init_session_state()

    # Carregar serviço RAG
    service = get_rag_service()

    # Renderizar sidebar (pode retornar um exemplo clicado)
    example_query = render_sidebar()

    # Renderizar chat
    render_chat()

    # Input de chat
    if prompt := st.chat_input("Digite sua pergunta sobre editais..."):
        query = prompt
    elif example_query:
        query = example_query
    else:
        query = None

    if query:
        # Adicionar mensagem do usuário
        st.session_state.messages.append({
            "role": "user",
            "content": query
        })

        with st.chat_message("user"):
            st.markdown(query)

        # Gerar resposta
        with st.chat_message("assistant"):
            result = process_query(query, service)

            # Mostrar resposta
            st.markdown(result["answer"])

            # Mostrar fontes em expander
            if result["sources"]:
                with st.expander("📚 Ver fontes consultadas"):
                    for src in result["sources"][:5]:
                        st.markdown(f"""
                        **{src.get('title', 'N/A')[:60]}**
                        Fonte: `{src.get('source', 'N/A')}` | Score: `{src.get('score', 0):.2%}`
                        🔗 {src.get('url', 'N/A')}
                        """)

            # Salvar na sessão
            st.session_state.messages.append({
                "role": "assistant",
                "content": result["answer"],
                "sources": result["sources"],
                "task_type": result["task_type"]
            })


if __name__ == "__main__":
    main()
