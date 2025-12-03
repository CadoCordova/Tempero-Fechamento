import math
from collections import defaultdict
from pathlib import Path
from io import BytesIO
from datetime import datetime
import json

import pandas as pd
import streamlit as st

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.auth.transport.requests import Request
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError


# ========================
#  Configurações e paths
# ========================

RULES_PATH = Path("regras_categorias.json")
CATEGORIAS_PATH = Path("categorias_personalizadas.json")

PRIMARY_COLOR = "#ec4899"  # Rosa principal
SECONDARY_COLOR = "#f9a8d4"  # Rosa secundário
BACKGROUND_SOFT = "#fff7fb"  # Fundo suave
TEXT_DARK = "#111827"

REGRAS_CATEGORIA = {}


# ========================
#  Estilo (CSS)
# ========================


def inject_css():
    st.markdown(
        f"""
        <style>
        /* Layout geral */
        .block-container {{
            max-width: 1200px;
            padding-top: 2.5rem;
            padding-bottom: 2rem;
        }}
        body {{
            background-color: {BACKGROUND_SOFT};
        }}

        /* Títulos gerais */
        .tempero-title {{
            font-size: 1.8rem;
            font-weight: 800;
            color: {PRIMARY_COLOR};
            letter-spacing: 0.02em;
            text-align: center;
            margin-bottom: 0.15rem;
        }}
        .tempero-subtitle {{
            font-size: 0.95rem;
            color: #666666;
            text-align: center;
            margin-bottom: 1.2rem;
        }}

        /* Seções */
        .tempero-section-title {{
            font-size: 1.15rem;
            font-weight: 700;
            color: {TEXT_DARK};
            margin-top: 1.4rem;
            margin-bottom: 0.25rem;
        }}
        .tempero-section-sub {{
            font-size: 0.85rem;
            color: #6b7280;
            margin-bottom: 0.6rem;
        }}

        /* Cards */
        .tempero-card {{
            border-radius: 14px;
            padding: 1rem 1.2rem;
            background-color: #ffffff;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
            border: 1px solid rgba(148, 163, 184, 0.20);
        }}

        /* Cards de métrica */
        .tempero-metric-card {{
            border-radius: 14px;
            padding: 0.9rem 1rem;
            background: linear-gradient(135deg, {PRIMARY_COLOR}, #f97316);
            color: #ffffff;
            box-shadow: 0 10px 25px rgba(249, 115, 22, 0.35);
        }}
        .tempero-metric-label {{
            font-size: 0.8rem;
            opacity: 0.95;
        }}
        .tempero-metric-value {{
            font-size: 1.25rem;
            font-weight: 700;
            margin-top: 0.1rem;
        }}

        /* Tab Bar */
        .stTabs [role="tab"] {{
            padding: 0.55rem 1rem;
            border-radius: 999px;
            color: #555555 !important;
            font-weight: 500;
        }}
        .stTabs [role="tab"][aria-selected="true"] {{
            background-color: {PRIMARY_COLOR}20 !important;
            color: {PRIMARY_COLOR} !important;
            border-bottom-color: transparent !Important;
        }}

        /* Tabelas */
        .tempero-table table {{
            font-size: 0.85rem;
        }}

        /* Logo do login — sobrescreve estilo padrão do Streamlit */
        .login-logo img {{
             max-width: 120px;
             display: block;
             margin: 0 auto 0.4rem auto;
        }}

        /* Rodapé do login */
        .login-footer {{
            margin-top: 0.9rem;
            font-size: 0.78rem;
            color: #9ca3af;
            text-align: center;
        }}

        /* =========
           Card de login (isolado)
           ===== */
        .login-card-wrapper {{
            display: flex;
            justify-content: center;
            margin-top: 0.8rem;
            margin-bottom: 0.2rem;
        }}

        .login-card {{
            max-width: 420px;
            width: 100%;
            padding: 1.3rem 1.6rem 1.4rem 1.6rem;
            background-color: #ffffff;
            border-radius: 14px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.10);
            border: 1px solid rgba(148, 163, 184, 0.35);
        }}

        .login-card h2 {{
            font-size: 1.15rem;
            font-weight: 700;
            color: #111827;
            margin-bottom: 0.8rem;
            text-align: center;
        }}

        .login-card .small-label {{
            font-size: 0.8rem;
            color: #6b7280;
            margin-bottom: 0.25rem;
        }}

        .login-card .stTextInput>div>div>input {{
            border-radius: 10px;
            border: 1px solid #d1d5db;
            padding: 0.50rem 0.75rem;
            font-size: 0.9rem;
        }}

        .login-card .stTextInput>div>div>input:focus {{
            border-color: {PRIMARY_COLOR};
            box-shadow: 0 0 0 1px {PRIMARY_COLOR}55;
        }}

        .login-card .stCheckbox>label {{
            font-size: 0.8rem;
            color: #4b5563;
        }}

        .login-card .stButton>button {{
            width: 100%;
            border-radius: 999px;
            padding: 0.50rem 0.75rem !important;
            font-size: 0.92rem !important;
        }}

        /* Botão 'Entrar' mais destacado */
        .stButton>button {{
            width: 100%;
            border-radius: 999px;
            padding: 0.55rem 1.2rem;
            font-weight: 600;
            border: none;
            background-color: {PRIMARY_COLOR} !important;
            color: #ffffff !important;
        }}
        
        /* SIDEBAR - Tempero das Gurias Rosa Premium ---------------------- */
        section[data-testid="stSidebar"] {{
            background-color: #fdf2f7;
            border-right: 1px solid #f6c6dd;
        }}

        .tg-sidebar-wrapper {{
            padding: 0.75rem 0.9rem 1.5rem 0.9rem;
        }}

        .tg-sidebar-header {{
            display: flex;
            align-items: center;
            gap: 0.5rem;
            margin-bottom: 0.6rem;
        }}

        .tg-sidebar-title {{
            font-size: 0.95rem;
            font-weight: 700;
            color: {PRIMARY_COLOR};
        }}

        .tg-sidebar-subtitle {{
            font-size: 0.78rem;
            color: #777;
            margin-bottom: 0.8rem;
        }}

        .tg-sidebar-card {{
            background-color: #ffffff;
            border-radius: 0.9rem;
            padding: 0.75rem 0.9rem;
            box-shadow: 0 2px 6px rgba(0,0,0,0.04);
            border: 1px solid #f7d5e7;
            margin-bottom: 0.7rem;
        }}

        .tg-sidebar-card h4 {{
            font-size: 0.82rem;
            font-weight: 600;
            margin: 0 0 0.3rem 0;
            color: #444;
        }}

        .tg-sidebar-card p {{
            font-size: 0.74rem;
            color: #777;
            margin: 0 0 0.4rem 0;
        }}

        .tg-sidebar-footer {{
            font-size: 0.75rem;
            color: #666;
            margin-top: 0.8rem;
            border-top: 1px solid #f2c4dd;
            padding-top: 0.6rem;
        }}

        .tg-sidebar-user {{
            font-size: 0.78rem;
            color: #555;
            margin-top: 0.2rem;
        }}

        .tg-sidebar-logout {{
            margin-top: 0.5rem;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ========================
#  Formatação Excel
# ========================


def formatar_tabela_excel(ws, df, start_row=1):
    """
    Aplica estilo básico:
    - Cabeçalho em negrito, fundo cinza, centralizado
    - Largura das colunas ajustada
    - Colunas de valor com formato de moeda (R$)
    """
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    if df.empty:
        return

    header_fill = PatternFill(start_color="E5E7EB", end_color="E5E7EB", fill_type="solid")
    header_font = Font(bold=True, color="111827")
    center_align = Alignment(horizontal="center", vertical="center")

    thin_border = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    # Cabeçalho
    for col_num, col_name in enumerate(df.columns, start=1):
        cell = ws.cell(row=start_row, column=col_num, value=col_name)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = center_align
        cell.border = thin_border

    # Dados
    for row_idx, (_, row) in enumerate(df.iterrows(), start=start_row + 1):
        for col_idx, col_name in enumerate(df.columns, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row[col_name])
            cell.border = thin_border
            if isinstance(row[col_name], (int, float)):
                cell.number_format = "R$ #,##0.00"

    # Ajusta largura das colunas
    for col_idx, col_name in enumerate(df.columns, start=1):
        max_len = max(len(str(col_name)), 10)
        for row_idx in range(start_row + 1, start_row + 1 + len(df)):
            val = ws.cell(row=row_idx, column=col_idx).value
            if val is not None:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[chr(64 + col_idx)].width = max_len + 1


# ========================
#  Helpers de autenticação (usuário / senha / perfil)
# ========================


def _load_users_from_secrets():
    """
    Carrega configuração de usuários a partir de st.secrets["auth_users"].

    Estrutura esperada em .streamlit/secrets.toml:

    [auth_users.ricardo]
    password = "senha"
    role = "admin"

    [auth_users.operador]
    password = "senha2"
    role = "operador"
    """
    try:
        auth_users = st.secrets.get("auth_users", {})
        return {k: dict(v) for k, v in auth_users.items()}
    except Exception:
        return {}


def current_user():
    return st.session_state.get("user", "desconhecido")


def current_role():
    return st.session_state.get("role", "operador")


def has_role(*roles):
    """
    Retorna True se o papel do usuário atual estiver em roles.
    """
    role = current_role()
    roles_norm = [str(r).strip().lower() for r in roles]
    return role in roles_norm


def require_role(*roles):
    """
    Interrompe a execução da aba se o usuário não tiver um dos perfis exigidos.
    """
    if not has_role(*roles):
        st.warning("Você não tem permissão para acessar esta área.")
        st.stop()


def check_auth():
    """
    Autenticação com usuário + senha + perfil.
    Se já estiver autenticado, apenas retorna.
    Caso contrário, exibe a tela de login.
    """
    if st.session_state.get("auth_ok"):
        return

    inject_css()

    # Logo (se existir)
    logo_path = Path("logo_tempero.png")
    if logo_path.exists():
        st.markdown('<div class="login-logo">', unsafe_allow_html=True)
        st.image(str(logo_path))
        st.markdown("</div>", unsafe_allow_html=True)

    # Títulos
    st.markdown(
        '<div class="tempero-title">Tempero das Gurias</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-subtitle">Painel de fechamento financeiro</div>',
        unsafe_allow_html=True,
    )

    users = _load_users_from_secrets()

    # === Card de login centralizado ===
    st.markdown('<div class="login-card-wrapper">', unsafe_allow_html=True)
    st.markdown('<div class="login-card">', unsafe_allow_html=True)

    with st.form("login_form"):
        st.markdown(
            '<div class="small-label">Informe usuário e senha</div>',
            unsafe_allow_html=True,
        )
        username = st.text_input("Usuário", key="login_username")

        col_senha, col_mostrar = st.columns([3, 1])
        with col_senha:
            senha = st.text_input("Senha", type="password", key="login_password")
        with col_mostrar:
            mostrar = st.checkbox("Mostrar senha", value=False, key="mostrar_senha")
        if mostrar:
            st.text_input(
                "Senha em texto puro (apenas para conferência)",
                value=senha,
                key="senha_visivel",
            )

        entrar = st.form_submit_button("Entrar")

    st.markdown("</div>", unsafe_allow_html=True)  # .login-card
    st.markdown("</div>", unsafe_allow_html=True)  # .login-card-wrapper

    if entrar:
        if users:
            user_cfg = users.get(username)
            if not user_cfg:
                st.error("Usuário não encontrado ou não configurado.")
            elif senha == user_cfg.get("password"):
                st.session_state["auth_ok"] = True
                st.session_state["user"] = username
                st.session_state["role"] = user_cfg.get("role", "operador")
                st.rerun()
            else:
                st.error("Senha incorreta. Tente novamente.")
        else:
            senha_correta = st.secrets.get("APP_PASSWORD")
            if senha_correta is None:
                st.error(
                    "Nenhum usuário configurado (auth_users) e APP_PASSWORD não definido nos secrets."
                )
            elif senha == senha_correta:
                st.session_state["auth_ok"] = True
                st.session_state["user"] = username or "admin"
                st.session_state["role"] = "admin"
                st.rerun()
            else:
                st.error("Senha incorreta. Tente novamente.")

    st.markdown(
        """
        <div class="login-footer">
          Dica: configure usuários em <code>[auth_users]</code> no <code>secrets.toml</code>
          ou use <code>APP_PASSWORD</code> como senha única.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.stop()


# ========================
#  Utilitários gerais
# ========================


def parse_numero_br(valor_str):
    """
    Converte string em formato brasileiro (1.234,56) para float.
    """
    if valor_str is None:
        return 0.0
    if isinstance(valor_str, (int, float)):
        return float(valor_str)

    s = str(valor_str).strip()
    if not s:
        return 0.0

    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def formatar_moeda(valor):
    """
    Formata um número float para moeda brasileira: R$ X.XXX,XX.
    """
    if pd.isna(valor):
        valor = 0.0
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def carregar_regras():
    """
    Carrega as regras de categorização a partir do arquivo JSON.
    Se não existir, retorna um dicionário vazio.
    """
    global REGRAS_CATEGORIA
    if RULES_PATH.exists():
        try:
            with RULES_PATH.open("r", encoding="utf-8") as f:
                REGRAS_CATEGORIA = json.load(f)
        except Exception:
            REGRAS_CATEGORIA = {}
    else:
        REGRAS_CATEGORIA = {}


def salvar_regras():
    """
    Salva as regras de categorização em arquivo JSON.
    """
    with RULES_PATH.open("w", encoding="utf-8") as f:
        json.dump(REGRAS_CATEGORIA, f, ensure_ascii=False, indent=2)


def normalizar_texto(txt: str) -> str:
    if not isinstance(txt, str):
        txt = str(txt or "")
    return " ".join(txt.strip().upper().split())


def aplicar_regras_tempero(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aplica regras simples de categorização:
    - Olha para REGRAS_CATEGORIA, que é um dict:
      { "TEXTO NORMALIZADO DA DESCRIÇÃO": "Categoria" }
    - Se descrição normalizada estiver no dicionário, atribui a categoria.
    - Senão, coloca "A Classificar".
    """
    if df.empty:
        return df

    if "Categoria" not in df.columns:
        df["Categoria"] = "A Classificar"

    if not REGRAS_CATEGORIA:
        df["Categoria"] = df["Categoria"].fillna("A Classificar")
        return df

    df["__desc_norm__"] = df["Descrição"].apply(normalizar_texto)
    df["Categoria"] = df["__desc_norm__"].map(REGRAS_CATEGORIA).fillna(
        df["Categoria"].fillna("A Classificar")
    )
    df.drop(columns=["__desc_norm__"], inplace=True, errors="ignore")
    return df


def resumo_por_categoria_tempero(df: pd.DataFrame) -> pd.DataFrame:
    """
    Gera um resumo de Entradas, Saídas e Resultado por categoria.
    Usa a coluna "Categoria" já preenchida.
    """
    if df.empty:
        return pd.DataFrame(columns=["Categoria", "Entradas", "Saídas", "Resultado"])

    if "Categoria" not in df.columns:
        df["Categoria"] = "A Classificar"

    df_tmp = df.copy()
    df_tmp["Entradas"] = df_tmp["Valor"].where(df_tmp["Valor"] > 0, 0.0)
    df_tmp["Saídas"] = df_tmp["Valor"].where(df_tmp["Valor"] < 0, 0.0)

    g = df_tmp.groupby("Categoria")[["Entradas", "Saídas"]].sum().reset_index()
    g["Resultado"] = g["Entradas"] + g["Saídas"]
    g = g.sort_values("Resultado")
    return g


# ========================
#  Leitura de extratos Itaú e PagSeguro
# ========================


def ler_extrato_itau(arquivo) -> pd.DataFrame:
    """
    Lê o extrato do Itaú (CSV/Excel) e retorna DataFrame padronizado.
    Assume estrutura típica com colunas:
      - Data Lançamento
      - Histórico
      - Valor (positivo/negativo)
    """
    if arquivo.name.lower().endswith(".csv"):
        df = pd.read_csv(arquivo, sep=";", encoding="latin1")
    else:
        df = pd.read_excel(arquivo)

    df.columns = [c.strip() for c in df.columns]

    # Tentativa de localizar colunas principais
    col_data = None
    for c in df.columns:
        if "data" in c.lower():
            col_data = c
            break

    col_hist = None
    for c in df.columns:
        if "hist" in c.lower() or "descr" in c.lower():
            col_hist = c
            break

    col_valor = None
    for c in df.columns:
        if "valor" in c.lower():
            col_valor = c
            break

    if col_data is None or col_hist is None or col_valor is None:
        st.error(
            "Não foi possível identificar as colunas de Data, Histórico e Valor no extrato do Itaú."
        )
        return pd.DataFrame(columns=["Data", "Descrição", "Origem", "Valor"])

    df_out = pd.DataFrame()
    df_out["Data"] = pd.to_datetime(
        df[col_data], dayfirst=True, errors="coerce"
    ).dt.date
    df_out["Descrição"] = df[col_hist].astype(str)
    df_out["Origem"] = "Itaú"

    valores = (
        df[col_valor]
        .astype(str)
        .str.replace(".", "", regex=False)
        .str.replace(",", ".", regex=False)
    )
    df_out["Valor"] = pd.to_numeric(valores, errors="coerce").fillna(0.0)

    return df_out


def ler_extrato_pagseguro(arquivo) -> pd.DataFrame:
    """
    Lê o extrato do PagSeguro (CSV/Excel) e retorna DataFrame padronizado.
    Considera colunas:
      - Data
      - Tipo
      - Descrição
      - Entradas
      - Saídas
    """
    if arquivo.name.lower().endswith(".csv"):
        df = pd.read_csv(arquivo, sep=";", encoding="latin1")
    else:
        df = pd.read_excel(arquivo)

    df.columns = [c.strip() for c in df.columns]

    col_data = None
    for c in df.columns:
        if "data" in c.lower():
            col_data = c
            break

    col_tipo = None
    for c in df.columns:
        if "tipo" in c.lower():
            col_tipo = c
            break

    col_descr = None
    for c in df.columns:
        if "descr" in c.lower():
            col_descr = c
            break

    col_entradas = None
    col_saidas = None

    for c in df.columns:
        lc = c.lower()
        if "entrad" in lc:
            col_entradas = c
        if "saida" in lc or "saída" in lc:
            col_saidas = c

    if col_data is None or col_descr is None:
        st.error(
            "Não foi possível identificar as colunas de Data e Descrição no extrato do PagSeguro."
        )
        return pd.DataFrame(columns=["Data", "Descrição", "Origem", "Valor"])

    df_out = pd.DataFrame()
    df_out["Data"] = pd.to_datetime(
        df[col_data], dayfirst=True, errors="coerce"
    ).dt.date
    df_out["Descrição"] = df[col_descr].astype(str)
    df_out["Origem"] = "PagSeguro"

    valores = pd.Series(0.0, index=df.index)

    if col_entradas:
        v_ent = (
            df[col_entradas]
            .astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        valores = valores + pd.to_numeric(v_ent, errors="coerce").fillna(0.0)

    if col_saidas:
        v_sai = (
            df[col_saidas]
            .astype(str)
            .str.replace(".", "", regex=False)
            .str.replace(",", ".", regex=False)
        )
        valores = valores - pd.to_numeric(v_sai, errors="coerce").fillna(0.0)

    df_out["Valor"] = valores
    return df_out


def consolidar_itau_pagseguro(df_itau: pd.DataFrame, df_pag: pd.DataFrame) -> pd.DataFrame:
    """
    Consolida extratos do Itaú e do PagSeguro num único DataFrame.
    """
    frames = []
    if df_itau is not None and not df_itau.empty:
        frames.append(df_itau)
    if df_pag is not None and not df_pag.empty:
        frames.append(df_pag)

    if not frames:
        return pd.DataFrame(columns=["Data", "Descrição", "Origem", "Valor"])

    df = pd.concat(frames, ignore_index=True)
    df = df.sort_values(["Data", "Origem"]).reset_index(drop=True)
    return df


def gerar_resumo(df_consolidado: pd.DataFrame) -> pd.DataFrame:
    """
    Gera resumo de entradas, saídas e resultado por origem (Itaú / PagSeguro).
    """
    if df_consolidado.empty:
        return pd.DataFrame(columns=["Origem", "Entradas", "Saídas", "Resultado"])

    df_tmp = df_consolidado.copy()
    df_tmp["Entradas"] = df_tmp["Valor"].where(df_tmp["Valor"] > 0, 0.0)
    df_tmp["Saídas"] = df_tmp["Valor"].where(df_tmp["Valor"] < 0, 0.0)

    g = df_tmp.groupby("Origem")[["Entradas", "Saídas"]].sum().reset_index()
    g["Resultado"] = g["Entradas"] + g["Saídas"]
    return g


def gerar_caixa_diario(df_consolidado: pd.DataFrame, saldo_inicial: float) -> pd.DataFrame:
    """
    Gera o caixa diário consolidado (Itaú + PagSeguro) com saldo acumulado.
    """
    if df_consolidado.empty:
        return pd.DataFrame(
            columns=["Data", "Entradas", "Saídas", "Resultado", "Saldo Acumulado"]
        )

    df = (
        df_consolidado.groupby("Data")[["Entradas", "Saídas"]]
        .sum()
        .reset_index()
        .sort_values("Data")
    )
    df["Resultado"] = df["Entradas"] - df["Saídas"]
    df["Saldo Acumulado"] = saldo_inicial + df["Resultado"].cumsum()
    return df


def gerar_planilha_excel(
    df_consolidado,
    df_resumo_origem,
    df_resumo_categoria,
    df_caixa_diario,
    periodo_nome,
):
    """
    Gera um arquivo Excel em memória com as abas:
      - Consolidado
      - Resumo por Origem
      - Resumo por Categoria
      - Caixa Diário
    """
    from openpyxl import Workbook

    wb = Workbook()

    # Aba 1: Consolidado
    ws1 = wb.active
    ws1.title = "Consolidado"
    df_consolidado.to_excel(ws1, index=False, startrow=1, header=True)
    formatar_tabela_excel(ws1, df_consolidado, start_row=1)

    # Aba 2: Resumo por Origem
    ws2 = wb.create_sheet("Resumo_Origem")
    df_resumo_origem.to_excel(ws2, index=False, startrow=1, header=True)
    formatar_tabela_excel(ws2, df_resumo_origem, start_row=1)

    # Aba 3: Resumo por Categoria
    ws3 = wb.create_sheet("Resumo_Categoria")
    df_resumo_categoria.to_excel(ws3, index=False, startrow=1, header=True)
    formatar_tabela_excel(ws3, df_resumo_categoria, start_row=1)

    # Aba 4: Caixa Diário
    ws4 = wb.create_sheet("Caixa_Diario")
    df_caixa_diario.to_excel(ws4, index=False, startrow=1, header=True)
    formatar_tabela_excel(ws4, df_caixa_diario, start_row=1)

    # Aba 5: Resumo do período
    ws5 = wb.create_sheet("Resumo")
    resumo_df = pd.DataFrame(
        [
            {
                "Nome do período": periodo_nome,
                "Entradas totais": df_resumo_origem["Entradas"].sum(),
                "Saídas totais": df_resumo_origem["Saídas"].sum(),
                "Resultado do período": df_resumo_origem["Resultado"].sum(),
            }
        ]
    )
    resumo_df.to_excel(ws5, index=False, startrow=1, header=True)
    formatar_tabela_excel(ws5, resumo_df, start_row=1)

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio.getvalue()


# ========================
#  Integração Google Drive
# ========================


def get_gdrive_service():
    """
    Retorna serviço autenticado do Google Drive usando secrets.
    """
    try:
        token_info = st.secrets["gdrive_oauth"]["token"]
    except Exception:
        st.error(
            "Configuração de OAuth do Google Drive não encontrada em st.secrets['gdrive_oauth']['token']."
        )
        st.stop()

    try:
        token_data = json.loads(token_info)
        creds = Credentials.from_authorized_user_info(token_data)
    except Exception as e:
        st.error(f"Erro ao carregar token do Google Drive: {e}")
        st.stop()

    try:
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                st.error("Credenciais do Google Drive inválidas ou expiradas.")
                st.stop()

        service = build("drive", "v3", credentials=creds)
        return service
    except RefreshError as e:
        msg = str(e)
        if "invalid_grant" in msg:
            st.error(
                "Erro de autenticação com o Google Drive: o token foi expirado ou revogado.\n\n"
                "Para voltar a usar o histórico, gere um novo arquivo token.json "
                "(rodando o script gerar_token.py) e atualize a seção [gdrive_oauth] "
                "do secrets do Streamlit."
            )
        else:
            st.error(f"Erro ao renovar o token do Google Drive: {e}")
        st.stop()

    except HttpError as e:
        st.error(f"Erro ao acessar a API do Google Drive: {e}")
        st.stop()

    except Exception as e:
        st.error(f"Erro inesperado ao inicializar o Google Drive: {e}")
        st.stop()


def get_or_create_folder(service, folder_name):
    try:
        resp = (
            service.files()
            .list(
                q=f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
                spaces="drive",
                fields="files(id, name)",
            )
            .execute()
        )
        files = resp.get("files", [])
        if files:
            return files[0]["id"]
        metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = service.files().create(body=metadata, fields="id").execute()
        return folder["id"]
    except Exception as e:
        st.error(f"Erro ao localizar/criar pasta no Google Drive: {e}")
        st.stop()


def upload_history_to_gdrive(excel_bytes, filename):
    service = get_gdrive_service()
    folder_name = st.secrets["gdrive_oauth"]["GDRIVE_FOLDER_NAME"]
    folder_id = get_or_create_folder(service, folder_name)

    media = MediaIoBaseUpload(BytesIO(excel_bytes), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", resumable=True)
    file_metadata = {"name": filename, "parents": [folder_id]}

    try:
        service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    except Exception as e:
        st.error(f"Erro ao enviar arquivo de histórico para o Google Drive: {e}")
        raise


def list_history_from_gdrive():
    service = get_gdrive_service()
    folder_name = st.secrets["gdrive_oauth"]["GDRIVE_FOLDER_NAME"]
    folder_id = get_or_create_folder(service, folder_name)

    try:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and trashed = false and mimeType = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'",
                spaces="drive",
                fields="files(id, name, createdTime, modifiedTime)",
                orderBy="createdTime desc",
            )
            .execute()
        )
        return resp.get("files", [])
    except Exception as e:
        st.error(f"Erro ao listar arquivos de histórico no Google Drive: {e}")
        return []


def download_history_file(file_id):
    service = get_gdrive_service()
    try:
        request = service.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        return BytesIO(fh.read())
    except Exception as e:
        st.error(f"Erro ao baixar arquivo de histórico: {e}")
        return None


def delete_history_file(file_id):
    service = get_gdrive_service()
    try:
        service.files().delete(fileId=file_id).execute()
    except Exception as e:
        st.error(f"Erro ao excluir arquivo de histórico: {e}")
        raise


def load_cash_from_gdrive():
    """
    Carrega o caixa_global.json (dinheiro) do Google Drive, se existir.
    """
    service = get_gdrive_service()
    folder_name = st.secrets["gdrive_oauth"]["GDRIVE_FOLDER_NAME"]
    folder_id = get_or_create_folder(service, folder_name)

    try:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and name = 'caixa_global.json' and trashed = false",
                spaces="drive",
                fields="files(id, name, createdTime)",
                orderBy="createdTime desc",
            )
            .execute()
        )
        files = resp.get("files", [])
        if not files:
            return pd.DataFrame(columns=["Data", "Descrição", "Tipo", "Valor"])

        file_id = files[0]["id"]
        raw = download_history_file(file_id)
        if raw is None:
            return pd.DataFrame(columns=["Data", "Descrição", "Tipo", "Valor"])

        data = json.loads(raw.decode("utf-8"))
        df = pd.DataFrame(data)
        if not df.empty:
            df["Data"] = pd.to_datetime(df["Data"], dayfirst=True, errors="coerce")
        return df
    except Exception as e:
        st.error(f"Erro ao carregar caixa global do Google Drive: {e}")
        return pd.DataFrame(columns=["Data", "Descrição", "Tipo", "Valor"])


def save_cash_to_gdrive(df_caixa):
    service = get_gdrive_service()
    folder_name = st.secrets["gdrive_oauth"]["GDRIVE_FOLDER_NAME"]
    folder_id = get_or_create_folder(service, folder_name)

    try:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and name = 'caixa_global.json' and trashed = false",
                spaces="drive",
                fields="files(id, name, createdTime)",
                orderBy="createdTime desc",
            )
            .execute()
        )
        files = resp.get("files", [])
        for f in files:
            try:
                service.files().delete(fileId=f["id"]).execute()
            except Exception:
                pass

        data = df_caixa.to_dict(orient="records")
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")

        media = MediaIoBaseUpload(BytesIO(raw), mimetype="application/json", resumable=True)
        metadata = {"name": "caixa_global.json", "parents": [folder_id]}
        service.files().create(body=metadata, media_body=media, fields="id").execute()
    except Exception as e:
        st.error(f"Erro ao salvar caixa global no Google Drive: {e}")
        raise


# ========================
#  Configuração principal
# ========================

st.set_page_config(page_title="Fechamento Tempero das Gurias", layout="wide")
inject_css()
check_auth()

st.markdown(
    '<div class="tempero-title">💗 Tempero das Gurias — Painel Financeiro</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="tempero-subtitle">'
    "Fechamento mensal consolidado (Itaú + PagSeguro) com histórico no Google Drive."
    "</div>",
    unsafe_allow_html=True,
)

carregar_regras()

# ========================
#  Upload de arquivos e parâmetros
# ========================

st.markdown(
    '<div class="tempero-section-title">1. Importar extratos e definir período</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="tempero-section-sub">'
    "Faça o upload dos extratos do Itaú e do PagSeguro e informe o nome do período."
    "</div>",
    unsafe_allow_html=True,
)

col_upload1, col_upload2 = st.columns(2)

with col_upload1:
    arquivo_itau = st.file_uploader(
        "Extrato Itaú (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="itau_main"
    )

with col_upload2:
    arquivo_pag = st.file_uploader(
        "Extrato PagSeguro (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="pag_main"
    )

saldo_inicial_input = st.text_input(
    "Saldo inicial consolidado do período (R$)", value="0"
)

default_periodo = datetime.today().strftime("%Y-%m") + " - período"
nome_periodo = st.text_input(
    "Nome do período (para histórico)",
    value=default_periodo,
    help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
)

st.markdown("---")

# ========================
#  Barra lateral
# ========================

with st.sidebar:
    st.markdown('<div class="tg-sidebar-wrapper">', unsafe_allow_html=True)

    st.markdown(
        '<div class="tg-sidebar-header">'
        '<div class="tg-sidebar-title">Configurações do período</div>'
        '</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tg-sidebar-subtitle">'
        'Selecione os extratos e parâmetros para o fechamento do período.'
        '</div>',
        unsafe_allow_html=True,
    )

    # Card 1 - Arquivos
    st.markdown('<div class="tg-sidebar-card">', unsafe_allow_html=True)
    st.markdown("<h4>Arquivos do período</h4>", unsafe_allow_html=True)
    arquivo_itau_sidebar = st.file_uploader(
        "Extrato Itaú (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="itau"
    )
    arquivo_pag_sidebar = st.file_uploader(
        "Extrato PagSeguro (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="pagseguro"
    )
    st.markdown("</div>", unsafe_allow_html=True)

    # Card 2 - Parâmetros
    st.markdown('<div class="tg-sidebar-card">', unsafe_allow_html=True)
    st.markdown("<h4>Parâmetros do período</h4>", unsafe_allow_html=True)

    saldo_inicial_input_sidebar = st.text_input(
        "Saldo inicial consolidado do período (R$)", value="0"
    )

    default_periodo_sidebar = datetime.today().strftime("%Y-%m") + " - período"
    nome_periodo_sidebar = st.text_input(
        "Nome do período (para histórico)",
        value=default_periodo_sidebar,
        help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
    )
    st.markdown("</div>", unsafe_allow_html=True)

    st.markdown(
        '<div class="tg-sidebar-footer">'
        'Feito para a <strong>Tempero das Gurias</strong> 💕'
        '</div>',
        unsafe_allow_html=True,
    )

# Info do usuário logado e botão de sair
if st.session_state.get("auth_ok"):
    with st.sidebar:
        st.markdown(
            f'<div class="tg-sidebar-user"><strong>Usuário:</strong> {current_user()}<br>'
            f'<strong>Perfil:</strong> {current_role()}</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="tg-sidebar-logout">', unsafe_allow_html=True)
        if st.button("Sair"):
            for k in ["auth_ok", "user", "role"]:
                st.session_state.pop(k, None)
            st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

# ========================
#  Carrega livro-caixa global de dinheiro
# ========================

if "df_caixa_global" not in st.session_state:
    try:
        st.session_state["df_caixa_global"] = load_cash_from_gdrive()
    except Exception:
        st.session_state["df_caixa_global"] = pd.DataFrame(
            columns=["Data", "Descrição", "Tipo", "Valor"]
        )

df_caixa_global = st.session_state["df_caixa_global"]

# Filtra dinheiro do período (considerando ano/mês do nome do período)
try:
    ano_mes_ref = None
    if nome_periodo_sidebar:
        partes = nome_periodo_sidebar.split()
        for p in partes:
            if len(p) == 7 and p[4] == "-":
                ano_mes_ref = p
                break
except Exception:
    ano_mes_ref = None

if df_caixa_global.empty:
    df_dinheiro_periodo = pd.DataFrame(
        columns=["Data", "Descrição", "Tipo", "Valor"]
    )
else:
    if ano_mes_ref:
        datas = pd.to_datetime(df_caixa_global["Data"], errors="coerce")
        mask = datas.dt.strftime("%Y-%m") == ano_mes_ref
        df_dinheiro_periodo = df_caixa_global[mask].copy()
    else:
        df_dinheiro_periodo = df_caixa_global.copy()

# ========================
#  Processamento principal dos extratos
# ========================

dados_carregados = False
mensagem_erro = ""

df_consolidado = pd.DataFrame()
df_resumo_origem = pd.DataFrame()
df_resumo_categoria = pd.DataFrame()
df_caixa_diario = pd.DataFrame()

if arquivo_itau_sidebar and arquivo_pag_sidebar:
    try:
        df_itau = ler_extrato_itau(arquivo_itau_sidebar)
        df_pag = ler_extrato_pagseguro(arquivo_pag_sidebar)

        df_consolidado = consolidar_itau_pagseguro(df_itau, df_pag)
        saldo_inicial = parse_numero_br(saldo_inicial_input_sidebar)
        df_caixa_diario = gerar_caixa_diario(df_consolidado, saldo_inicial)

        df_consolidado = aplicar_regras_tempero(df_consolidado)

        df_resumo_origem = gerar_resumo(df_consolidado)
        df_resumo_categoria = resumo_por_categoria_tempero(df_consolidado)

        dados_carregados = True
    except Exception as e:
        mensagem_erro = f"Erro ao processar os arquivos: {e}"

# ========================
#  Abas (ordem: Caixa, Fechamento, Categorias, Histórico)
# ========================

tab1, tab2, tab3, tab4 = st.tabs(
    [
        "💵 Caixa Diário",
        "💗 Fechamento Mensal",
        "🧾 Conferência & Categorias",
        "📊 Histórico & Comparativos",
    ]
)

# ---------- ABA 1: Caixa Diário ----------

with tab1:
    st.markdown(
        '<div class="tempero-section-title">💵 Caixa diário em dinheiro</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-section-sub">'
        "Registre aqui as entradas e saídas em dinheiro. "
        "Esses lançamentos são salvos no Google Drive e usados nos fechamentos mensais."
        "</div>",
        unsafe_allow_html=True,
    )

    if df_dinheiro_periodo.empty:
        df_dinheiro_periodo = pd.DataFrame(
            [
                {
                    "Data": datetime.today().date(),
                    "Descrição": "",
                    "Tipo": "Entrada",
                    "Valor": 0.0,
                }
            ],
            columns=["Data", "Descrição", "Tipo", "Valor"],
        )

    df_dinheiro_ui = st.data_editor(
        df_dinheiro_periodo,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Data": st.column_config.DateColumn("Data"),
            "Descrição": st.column_config.TextColumn("Descrição"),
            "Tipo": st.column_config.SelectboxColumn(
                "Tipo", options=["Entrada", "Saída"], required=True
            ),
            "Valor": st.column_config.NumberColumn(
                "Valor (R$)", step=0.01, min_value=0.0
            ),
        },
        key=f"editor_dinheiro_{ano_mes_ref or 'padrao'}",
    )

    # Limpa linhas sem valor e sem descrição
    df_din_limpo = df_dinheiro_ui.copy()
    if not df_din_limpo.empty:
        df_din_limpo = df_din_limpo[
            ~(
                (df_din_limpo["Valor"].fillna(0) == 0)
                & (df_din_limpo["Descrição"].fillna("").str.strip() == "")
            )
        ]

    col_btn1, col_btn2 = st.columns([1, 3])
    with col_btn1:
        salvar_caixa = st.button("Salvar lançamentos de dinheiro")

    if salvar_caixa:
        try:
            df_global = df_caixa_global.copy()

            if ano_mes_ref:
                datas = pd.to_datetime(df_global["Data"], errors="coerce")
                mask = datas.dt.strftime("%Y-%m") == ano_mes_ref
                df_outros_meses = df_global[~mask]
            else:
                df_outros_meses = df_global.iloc[0:0]

            df_novo_global = pd.concat(
                [df_outros_meses, df_din_limpo], ignore_index=True
            )

            st.session_state["df_caixa_global"] = df_novo_global
            save_cash_to_gdrive(df_novo_global)
            st.success("Lançamentos de dinheiro salvos com sucesso no Google Drive!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao salvar caixa diário no Drive: {e}")

    # Totais do mês (caixa) apenas para exibição na aba
    df_din_calc = df_din_limpo.copy()
    if not df_din_calc.empty and "Valor" in df_din_calc.columns:
        df_din_calc = df_din_calc[df_din_calc["Valor"] > 0]

    entradas_d = df_din_calc.loc[
        df_din_calc["Tipo"] == "Entrada", "Valor"
    ].sum()
    saidas_d = df_din_calc.loc[
        df_din_calc["Tipo"] == "Saída", "Valor"
    ].sum()
    saldo_d = entradas_d - saidas_d

    st.markdown("---")
    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        st.write("Entradas em dinheiro no período:", formatar_moeda(entradas_d))
    with col_c2:
        st.write(
            "Saídas em dinheiro no período:",
            formatar_moeda(-saidas_d) if saidas_d else "R$ 0,00",
        )
    with col_c3:
        st.write("Saldo do dinheiro no período:", formatar_moeda(saldo_d))

# ---------- ABA 2: Fechamento Mensal ----------

with tab2:
    require_role("admin")
    st.markdown(
        '<div class="tempero-section-title">💗 Fechamento mensal consolidado</div>',
        unsafe_allow_html=True,
    )

    if mensagem_erro:
        st.error(mensagem_erro)
    elif not dados_carregados:
        st.info(
            "Envie os arquivos do Itaú e PagSeguro na barra lateral para ver o fechamento."
        )
    else:
        entradas_totais = df_resumo_origem["Entradas"].sum()
        saidas_totais = df_resumo_origem["Saídas"].sum()
        resultado_consolidado = df_resumo_origem["Resultado"].sum()

        ent_itau = (
            df_resumo_origem.loc[df_resumo_origem["Origem"] == "Itaú", "Entradas"]
            .sum()
        )
        sai_itau = (
            df_resumo_origem.loc[df_resumo_origem["Origem"] == "Itaú", "Saídas"].sum()
        )
        res_itau = ent_itau + sai_itau

        ent_pag = (
            df_resumo_origem.loc[df_resumo_origem["Origem"] == "PagSeguro", "Entradas"]
            .sum()
        )
        sai_pag = (
            df_resumo_origem.loc[df_resumo_origem["Origem"] == "PagSeguro", "Saídas"]
            .sum()
        )
        res_pag = ent_pag + sai_pag

        entradas_dinheiro_periodo = df_din_limpo.loc[
            df_din_limpo["Tipo"] == "Entrada", "Valor"
        ].sum()
        saidas_dinheiro_periodo = df_din_limpo.loc[
            df_din_limpo["Tipo"] == "Saída", "Valor"
        ].sum()
        saldo_dinheiro_periodo = entradas_dinheiro_periodo - saidas_dinheiro_periodo

        saldo_inicial = parse_numero_br(saldo_inicial_input_sidebar)
        saldo_final = saldo_inicial + resultado_consolidado + saldo_dinheiro_periodo

        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown(
                f"""
                <div class="tempero-metric-card">
                  <div class="tempero-metric-label">Entradas totais</div>
                  <div class="tempero-metric-value">{formatar_moeda(entradas_totais)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown(
                f"""
                <div class="tempero-metric-card">
                  <div class="tempero-metric-label">Saídas totais</div>
                  <div class="tempero-metric-value">{formatar_moeda(saidas_totais)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown(
                f"""
                <div class="tempero-metric-card">
                  <div class="tempero-metric-label">Resultado do período</div>
                  <div class="tempero-metric-value">{formatar_moeda(resultado_consolidado)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("---")

        st.markdown(
            '<div class="tempero-section-title">📑 Resumo por conta</div>',
            unsafe_allow_html=True,
        )
        with st.container():
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.markdown("**Itaú**")
                st.write("Entradas:", formatar_moeda(ent_itau))
                st.write("Saídas  :", formatar_moeda(sai_itau))
                st.write("Resultado:", formatar_moeda(res_itau))
                st.markdown("</div>", unsafe_allow_html=True)

            with col_b:
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.markdown("**PagSeguro**")
                st.write("Entradas:", formatar_moeda(ent_pag))
                st.write("Saídas  :", formatar_moeda(sai_pag))
                st.write("Resultado:", formatar_moeda(res_pag))
                st.markdown("</div>", unsafe_allow_html=True)

            with col_c:
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.markdown("**Dinheiro (caixa físico)**")
                st.write(
                    "Entradas:", formatar_moeda(entradas_dinheiro_periodo)
                )
                st.write(
                    "Saídas  :",
                    formatar_moeda(-saidas_dinheiro_periodo)
                    if saidas_dinheiro_periodo
                    else "R$ 0,00",
                )
                st.write("Resultado:", formatar_moeda(saldo_dinheiro_periodo))
                st.caption("Edite os lançamentos na aba 💵 Caixa Diário.")
                st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("---")

        st.markdown(
            '<div class="tempero-section-title">🏁 Consolidado da loja</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
        st.write("Saldo inicial:", formatar_moeda(saldo_inicial))
        st.write("Saldo final  :", formatar_moeda(saldo_final))
        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown(
            '<div class="tempero-section-title">📌 Resumo por categoria</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tempero-section-sub">Baseado nas categorias atuais (já considera regras salvas anteriormente).</div>',
            unsafe_allow_html=True,
        )
        df_cat_display = df_resumo_categoria.copy()
        if not df_cat_display.empty:
            df_cat_display["Entradas"] = df_cat_display["Entradas"].map(formatar_moeda)
            df_cat_display["Saídas"] = df_cat_display["Saídas"].map(formatar_moeda)
            df_cat_display["Resultado"] = df_cat_display["Resultado"].map(
                formatar_moeda
            )
        st.dataframe(df_cat_display, use_container_width=True)

        st.markdown(
            '<div class="tempero-section-title">📥 Relatório do período atual</div>',
            unsafe_allow_html=True,
        )
        excel_buffer = gerar_planilha_excel(
            df_consolidado,
            df_resumo_origem,
            df_resumo_categoria,
            df_caixa_diario,
            nome_periodo_sidebar,
        )

        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
        col_dl1, col_dl2 = st.columns(2)
        with col_dl1:
            st.download_button(
                label="Baixar relatório Excel (período atual)",
                data=excel_buffer,
                file_name="fechamento_tempero.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        with col_dl2:
            salvar = st.button("Salvar no histórico")

        if salvar:
            slug = nome_periodo_sidebar.replace(" ", "_").replace("/", "-")
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"fechamento_tempero_{slug}_{timestamp}.xlsx"
            try:
                upload_history_to_gdrive(excel_buffer, filename)
                st.success(
                    f"Relatório salvo no histórico (Google Drive) como: {filename}"
                )
            except Exception as e:
                st.error(f"Erro ao salvar no Google Drive: {e}")
        st.markdown("</div>", unsafe_allow_html=True)

# ---------- ABA 3: Conferência & Categorias ----------

with tab3:
    require_role("admin")
    st.markdown(
        '<div class="tempero-section-title">🧾 Conferência de lançamentos e categorias</div>',
        unsafe_allow_html=True,
    )

    if not dados_carregados:
        st.info(
            "Carregue os extratos na barra lateral para conferir os lançamentos."
        )
    else:
        st.write(
            "Aqui você pode listar lançamentos por categoria, ajustar regras, "
            "etc. (lógica detalhada pode ser inserida conforme evolução do sistema)."
        )

# ---------- ABA 4: Histórico & Comparativos ----------

with tab4:
    require_role("admin")
    st.markdown(
        '<div class="tempero-section-title">📊 Histórico de fechamentos e comparativo</div>',
        unsafe_allow_html=True,
    )

    try:
        arquivos = list_history_from_gdrive()
        if not arquivos:
            st.info("Nenhum histórico de fechamento encontrado no Google Drive.")
        else:
            st.write("Listagem de históricos (apenas exemplo, pode ser refinada):")
            for f in arquivos:
                st.write(f"{f['name']} — criado em {f['createdTime']}")
    except Exception as e:
        st.error(f"Erro ao carregar histórico: {e}")
