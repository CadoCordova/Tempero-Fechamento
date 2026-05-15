import streamlit as st

from modules.ui import inject_css


def _load_users_from_secrets() -> dict:
    """
    Lê usuários e perfis de st.secrets["auth_users"].

    Estrutura esperada no secrets:
        [auth_users.ricardo]
        password = "..."
        role = "admin"
    """
    try:
        users_section = st.secrets["auth_users"]
    except Exception:
        return {}

    users = {}
    for username, cfg in users_section.items():
        users[username] = {
            "password": cfg.get("password"),
            "role": str(cfg.get("role", "operador")).strip().lower(),
        }
    return users


def current_user() -> str | None:
    return st.session_state.get("user")


def current_role() -> str:
    return st.session_state.get("role", "operador")


def has_role(*roles) -> bool:
    role = current_role()
    return role in [str(r).strip().lower() for r in roles]


def require_role(*roles):
    if not has_role(*roles):
        st.warning("Você não tem permissão para acessar esta área.")
        st.stop()


def check_auth():
    """
    Mostra tela de login se o usuário ainda não estiver autenticado.
    Interrompe o fluxo com st.stop() enquanto não autenticado.
    """
    if st.session_state.get("auth_ok"):
        return

    inject_css()
    st.markdown(
        '<div class="tempero-title">Tempero das Gurias - Acesso Restrito</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-subtitle">Área interna para fechamento financeiro da loja.</div>',
        unsafe_allow_html=True,
    )

    username = st.text_input("Usuário:")
    senha = st.text_input("Senha:", type="password")

    col1, _ = st.columns(2)
    with col1:
        ok = st.button("Entrar")

    users = _load_users_from_secrets()

    if ok:
        if users:
            user_cfg = users.get(username)
            if not user_cfg:
                st.error("Usuário não encontrado ou não configurado.")
                st.stop()

            if senha == user_cfg.get("password"):
                st.session_state["auth_ok"] = True
                st.session_state["user"] = username
                st.session_state["role"] = user_cfg.get("role", "operador")
                st.rerun()
            else:
                st.error("Senha incorreta. Tente novamente.")
                st.stop()
        else:
            senha_correta = st.secrets.get("APP_PASSWORD")
            if senha_correta is None:
                st.error(
                    "Nenhum usuário configurado (auth_users) e APP_PASSWORD não definido nos secrets."
                )
                st.stop()

            if senha == senha_correta:
                st.session_state["auth_ok"] = True
                st.session_state["user"] = username or "admin"
                st.session_state["role"] = "admin"
                st.rerun()
            else:
                st.error("Senha incorreta. Tente novamente.")
                st.stop()

    st.stop()
