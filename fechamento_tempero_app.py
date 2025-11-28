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


from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


# ========================
#  Configurações e paths
# ========================

RULES_PATH = Path("regras_categorias.json")
CATEGORIAS_PATH = Path("categorias_personalizadas.json")

PRIMARY_COLOR = "#FF4B8B"
SECONDARY_COLOR = "#FFE4EF"
BACKGROUND_COLOR = "#FFF7FB"
CARD_BACKGROUND = "#FFFFFF"


# ========================
#  Utilitários gerais
# ========================


def inject_css():
    """
    CSS customizado para o painel Tempero das Gurias.
    """
    st.markdown(
        f"""
        <style>
        /* Fundo geral */
        .stApp {{
            background-color: {BACKGROUND_COLOR};
        }}

        /* Título principal */
        .tempero-title {{
            font-size: 2.2rem;
            font-weight: 800;
            color: {PRIMARY_COLOR};
            text-align: center;
            margin-bottom: 0.2rem;
        }}

        .tempero-subtitle {{
            font-size: 1rem;
            color: #555;
            text-align: center;
            margin-bottom: 2rem;
        }}

        /* Seções principais */
        .tempero-section-title {{
            font-size: 1.3rem;
            font-weight: 700;
            color: {PRIMARY_COLOR};
            margin-top: 1.5rem;
            margin-bottom: 0.3rem;
        }}

        .tempero-section-sub {{
            font-size: 0.9rem;
            color: #666;
            margin-bottom: 1.0rem;
        }}

        /* Cards de KPIs */
        .tempero-kpi-card {{
            background-color: {CARD_BACKGROUND};
            border-radius: 0.9rem;
            padding: 1rem 1.2rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
            border: 1px solid #F3D0E0;
        }}

        .tempero-kpi-label {{
            font-size: 0.85rem;
            color: #777;
        }}

        .tempero-kpi-value {{
            font-size: 1.3rem;
            font-weight: 700;
            color: {PRIMARY_COLOR};
        }}

        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 0.25rem;
        }}

        .stTabs [data-baseweb="tab"] {{
            background-color: #FFEAF4;
            border-radius: 999px;
            padding: 0.25rem 0.9rem;
        }}

        .stTabs [aria-selected="true"] {{
            background-color: {PRIMARY_COLOR};
            color: white;
        }}

        /* Login */
        .login-logo {{
            text-align: center;
            margin-top: 2rem;
            margin-bottom: 0.5rem;
        }}

        .login-card-wrapper {{
            display: flex;
            justify-content: center;
            margin-top: 1rem;
        }}

        .login-card {{
            background-color: {CARD_BACKGROUND};
            border-radius: 1rem;
            padding: 2rem;
            box-shadow: 0 2px 10px rgba(0,0,0,0.06);
            width: 100%;
            max-width: 420px;
            border: 1px solid #F3D0E0;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


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


# ========================
#  Autenticação (usuário / senha / perfil)
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
        # st.secrets pode devolver um tipo especial; convertemos em dict normal
        return {k: dict(v) for k, v in auth_users.items()}
    except Exception:
        return {}


def current_user():
    return st.session_state.get("user", "desconhecido")


def current_role():
    return st.session_state.get("role", "operador")


def has_role(*roles):
    """
    Retorna True se o papel (role) do usuário atual estiver em roles.
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

    # Fecha divs do card
    st.markdown("</div>", unsafe_allow_html=True)  # .login-card
    st.markdown("</div>", unsafe_allow_html=True)  # .login-card-wrapper

    # Validação de credenciais
    if entrar:
        # Se houver seção auth_users, usamos sempre ela
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
        # Fallback: APP_PASSWORD
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
        <div style="text-align:center; margin-top: 1rem; color: #888; font-size:0.85rem;">
        Dica: configure usuários em <code>[auth_users]</code> no <code>secrets.toml</code> 
        ou use <code>APP_PASSWORD</code> como senha única.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.stop()


# ========================
#  Integração Google Drive
# ========================


def get_drive_service():
    """
    Obtém um client autenticado do Google Drive usando as credenciais armazenadas no secrets.
    """
    try:
        token_info = st.secrets.get("gdrive_oauth", None)
    except Exception:
        token_info = None

    if not token_info:
        st.warning(
            "Configuração de OAuth do Google Drive não encontrada em st.secrets['gdrive_oauth']."
        )
        return None

    creds = Credentials.from_authorized_user_info(token_info)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except RefreshError as e:
                st.error(f"Erro ao renovar credenciais do Google Drive: {e}")
                return None

    try:
        service = build("drive", "v3", credentials=creds)
        return service
    except Exception as e:
        st.error(f"Erro ao criar serviço do Google Drive: {e}")
        return None


def upload_to_drive(file_bytes, file_name, folder_id=None, mime_type=None):
    """
    Faz upload de um arquivo binário para o Google Drive e retorna o ID.
    """
    service = get_drive_service()
    if not service:
        return None

    file_metadata = {"name": file_name}
    if folder_id:
        file_metadata["parents"] = [folder_id]

    media = MediaIoBaseUpload(BytesIO(file_bytes), mimetype=mime_type, resumable=True)

    try:
        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        return file.get("id")
    except HttpError as e:
        st.error(f"Erro ao enviar arquivo para o Google Drive: {e}")
        return None


def download_from_drive(file_id):
    """
    Faz o download de um arquivo do Google Drive e retorna bytes.
    """
    service = get_drive_service()
    if not service:
        return None

    try:
        request = service.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)

        done = False
        while not done:
            _, done = downloader.next_chunk()

        fh.seek(0)
        return fh.read()
    except HttpError as e:
        st.error(f"Erro ao baixar arquivo do Google Drive: {e}")
        return None


def find_or_create_folder(folder_name, parent_id=None):
    """
    Busca uma pasta pelo nome no Google Drive. Se não existir, cria.
    """
    service = get_drive_service()
    if not service:
        return None

    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"

    try:
        results = (
            service.files()
            .list(q=query, spaces="drive", fields="files(id, name)")
            .execute()
        )
        items = results.get("files", [])
        if items:
            return items[0]["id"]

        # Cria a pasta se não existir
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            file_metadata["parents"] = [parent_id]

        file = service.files().create(body=file_metadata, fields="id").execute()
        return file.get("id")
    except HttpError as e:
        st.error(f"Erro ao buscar/criar pasta no Google Drive: {e}")
        return None


# ========================
#  Leitura e tratamento dos extratos
# ========================


def ler_extrato_itau(arquivo):
    """
    Lê o extrato do Itaú (CSV ou Excel) e retorna DataFrame padronizado.
    """
    if arquivo.name.lower().endswith(".csv"):
        df = pd.read_csv(arquivo, sep=";", encoding="latin-1")
    else:
        df = pd.read_excel(arquivo)

    cols = [c.strip().lower() for c in df.columns]

    # Tentar mapear colunas comuns
    mapa = {}
    for c in df.columns:
        cl = c.strip().lower()
        if "data" in cl and "lan" in cl:
            mapa["Data"] = c
        elif "hist" in cl or "descri" in cl:
            mapa["Descrição"] = c
        elif "valor" in cl:
            mapa["Valor"] = c
        elif "saldo" in cl:
            mapa["Saldo"] = c

    df_pad = pd.DataFrame()
    df_pad["Data"] = pd.to_datetime(df[mapa["Data"]], dayfirst=True, errors="coerce")
    df_pad["Descrição"] = df[mapa["Descrição"]].astype(str)

    valor = df[mapa["Valor"]].astype(str)
    valor = valor.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    df_pad["Valor"] = pd.to_numeric(valor, errors="coerce").fillna(0.0)

    # Se tiver saldo
    if "Saldo" in mapa:
        saldo = df[mapa["Saldo"]].astype(str)
        saldo = saldo.str.replace(".", "", regex=False).str.replace(
            ",", ".", regex=False
        )
        df_pad["Saldo"] = pd.to_numeric(saldo, errors="coerce")

    df_pad["Origem"] = "Itaú"
    return df_pad


def ler_extrato_pagseguro(arquivo):
    """
    Lê o extrato do PagSeguro (CSV ou Excel) e retorna DataFrame padronizado.
    """
    if arquivo.name.lower().endswith(".csv"):
        df = pd.read_csv(arquivo, sep=";", encoding="latin-1")
    else:
        df = pd.read_excel(arquivo)

    cols = [c.strip().lower() for c in df.columns]

    mapa = {}
    for c in df.columns:
        cl = c.strip().lower()
        if "data" in cl:
            mapa["Data"] = c
        elif "descri" in cl:
            mapa["Descrição"] = c
        elif "valor" in cl:
            mapa["Valor"] = c

    df_pad = pd.DataFrame()
    df_pad["Data"] = pd.to_datetime(df[mapa["Data"]], dayfirst=True, errors="coerce")
    df_pad["Descrição"] = df[mapa["Descrição"]].astype(str)

    valor = df[mapa["Valor"]].astype(str)
    valor = valor.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    df_pad["Valor"] = pd.to_numeric(valor, errors="coerce").fillna(0.0)

    df_pad["Saldo"] = pd.NA
    df_pad["Origem"] = "PagSeguro"
    return df_pad


def consolidar_itau_pagseguro(df_itau, df_pag):
    """
    Concatena os extratos padronizados e ordena por data.
    """
    df = pd.concat([df_itau, df_pag], ignore_index=True)
    df = df.sort_values(by=["Data"]).reset_index(drop=True)
    return df


def gerar_resumo(df_consolidado):
    """
    Gera resumo de entradas/saídas por origem (Itaú / PagSeguro).
    """
    df_resumo = (
        df_consolidado.groupby("Origem")["Valor"].agg(Entradas=lambda x: x[x > 0].sum(),
                                                      Saídas=lambda x: x[x < 0].sum())
    )
    df_resumo = df_resumo.reset_index()
    df_resumo["Resultado"] = df_resumo["Entradas"] + df_resumo["Saídas"]
    return df_resumo


# ========================
#  Regras de categorização
# ========================

REGRAS_CATEGORIA = {}
CATEGORIAS_PERSONALIZADAS = []


def carregar_regras():
    """
    Carrega as regras de categorização a partir do arquivo JSON.
    Se não existir, retorna um dicionário vazio.
    """
    global REGRAS_CATEGORIA
    if RULES_PATH.exists():
        try:
            with open(RULES_PATH, "r", encoding="utf-8") as f:
                REGRAS_CATEGORIA = json.load(f)
        except Exception:
            REGRAS_CATEGORIA = {}
    else:
        REGRAS_CATEGORIA = {}


def salvar_regras():
    """
    Salva as regras de categorização em arquivo JSON.
    """
    with open(RULES_PATH, "w", encoding="utf-8") as f:
        json.dump(REGRAS_CATEGORIA, f, ensure_ascii=False, indent=2)


def carregar_categorias_personalizadas():
    """
    Carrega lista de categorias personalizadas, se existir.
    """
    global CATEGORIAS_PERSONALIZADAS
    if CATEGORIAS_PATH.exists():
        try:
            with open(CATEGORIAS_PATH, "r", encoding="utf-8") as f:
                CATEGORIAS_PERSONALIZADAS = json.load(f)
        except Exception:
            CATEGORIAS_PERSONALIZADAS = []
    else:
        CATEGORIAS_PERSONALIZADAS = []


def salvar_categorias_personalizadas():
    """
    Salva lista de categorias personalizadas em arquivo JSON.
    """
    with open(CATEGORIAS_PATH, "w", encoding="utf-8") as f:
        json.dump(CATEGORIAS_PERSONALIZADAS, f, ensure_ascii=False, indent=2)


def aplicar_regras_tempero(df):
    """
    Aplica regras de categorização nas descrições das movimentações.
    REGRAS_CATEGORIA é um dicionário:
    {
      "Fornecedores e Insumos": ["MERCADO X", "PADARIA Y"],
      "Folha de Pagamento": ["CAROLINE", "VERÔNICA"],
      ...
    }
    """
    if "Categoria" not in df.columns:
        df["Categoria"] = "Outros"

    if not REGRAS_CATEGORIA:
        return df

    for categoria, padroes in REGRAS_CATEGORIA.items():
        for padrao in padroes:
            mask = df["Descrição"].str.contains(padrao, case=False, na=False)
            df.loc[mask, "Categoria"] = categoria

    return df


def resumo_por_categoria_tempero(df):
    """
    Gera resumo por categoria, considerando entradas e saídas.
    """
    if "Categoria" not in df.columns:
        df["Categoria"] = "Outros"

    grouped = df.groupby("Categoria")["Valor"].agg(
        Entradas=lambda x: x[x > 0].sum(), Saídas=lambda x: x[x < 0].sum()
    )
    df_cat = grouped.reset_index()
    df_cat["Resultado"] = df_cat["Entradas"] + df_cat["Saídas"]
    df_cat = df_cat.sort_values(by="Resultado", ascending=True)
    return df_cat


# ========================
#  Caixa diário
# ========================


def carregar_caixa_global():
    """
    Carrega o histórico de caixa diário em um único DataFrame.
    Arquivo salvo no Google Drive (ou local) consolidando dias.
    """
    try:
        with open("caixa_global.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        df = pd.DataFrame(data)
        if not df.empty:
            df["Data"] = pd.to_datetime(df["Data"], errors="coerce")
        return df
    except FileNotFoundError:
        return pd.DataFrame(columns=["Data", "Descrição", "Valor"])


def salvar_caixa_global(df_caixa):
    """
    Salva o DataFrame de caixa diário em JSON local (poderia ir para o Drive).
    """
    data = df_caixa.to_dict(orient="records")
    with open("caixa_global.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def gerar_caixa_diario(df_consolidado, saldo_inicial=0.0):
    """
    Gera um DataFrame de caixa diário com base no consolidado de Itaú + PagSeguro.
    """
    df = df_consolidado.copy()
    df = df.sort_values(by="Data")
    df["Saldo Diário"] = saldo_inicial + df["Valor"].cumsum()
    return df


# ========================
#  Geração de Excel formatado
# ========================


def gerar_planilha_excel(
    df_consolidado, df_resumo_origem, df_resumo_categoria, df_caixa_diario, nome_periodo
):
    """
    Gera um arquivo Excel em memória com múltiplas abas.
    """
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        # Aba 1: Consolidado
        df_consolidado.to_excel(writer, index=False, sheet_name="Consolidado")

        # Aba 2: Resumo por origem (Itaú / PagSeguro)
        df_resumo_origem.to_excel(writer, index=False, sheet_name="Resumo_Origem")

        # Aba 3: Resumo por categoria
        df_resumo_categoria.to_excel(writer, index=False, sheet_name="Resumo_Categoria")

        # Aba 4: Caixa diário
        df_caixa_diario.to_excel(writer, index=False, sheet_name="Caixa_Diario")

        # Formatação simples
        workbook = writer.book
        for sheet_name in [
            "Consolidado",
            "Resumo_Origem",
            "Resumo_Categoria",
            "Caixa_Diario",
        ]:
            ws = workbook[sheet_name]
            ajustar_colunas_excel(ws)

    output.seek(0)
    return output


def ajustar_colunas_excel(ws):
    """
    Ajusta largura de colunas e formata cabeçalhos em uma planilha openpyxl.
    """
    max_col = ws.max_column
    max_row = ws.max_row

    if max_row < 1 or max_col < 1:
        return

    header_row = 1
    n_cols = max_col

    # Cabeçalho
    from openpyxl.styles import Font, PatternFill, Alignment

    for col_idx in range(1, n_cols + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="center")

    # Congela linha de cabeçalho
    ws.freeze_panes = ws[f"A{header_row + 1}"]

    # Ajusta largura das colunas
    from openpyxl.utils import get_column_letter

    for col_idx, _ in enumerate(ws.iter_cols(1, n_cols), start=1):
        max_len = 0
        for row_idx in range(1, max_row + 1):
            cell_value = ws.cell(row=row_idx, column=col_idx).value
            if cell_value is not None:
                max_len = max(max_len, len(str(cell_value)))
        adjusted_width = max_len + 2
        ws.column_dimensions[get_column_letter(col_idx)].width = adjusted_width


# ========================
#  Componentes visuais auxiliares
# ========================


def exibir_kpis_resumo(df_resumo):
    """
    Exibe KPIs simples de resumo: total de entradas, saídas e resultado.
    """
    total_entradas = df_resumo["Entradas"].sum()
    total_saidas = df_resumo["Saídas"].sum()
    resultado = total_entradas + total_saidas

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown('<div class="tempero-kpi-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="tempero-kpi-label">Entradas totais</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="tempero-kpi-value">{formatar_moeda(total_entradas)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="tempero-kpi-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="tempero-kpi-label">Saídas totais</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="tempero-kpi-value">{formatar_moeda(total_saidas)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col3:
        st.markdown('<div class="tempero-kpi-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="tempero-kpi-label">Resultado do período</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="tempero-kpi-value">{formatar_moeda(resultado)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)


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
carregar_categorias_personalizadas()

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

st.sidebar.header("Configurações do período")

arquivo_itau_sidebar = st.sidebar.file_uploader(
    "Extrato Itaú (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="itau"
)
arquivo_pag_sidebar = st.sidebar.file_uploader(
    "Extrato PagSeguro (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="pagseguro"
)

saldo_inicial_input_sidebar = st.sidebar.text_input(
    "Saldo inicial consolidado do período (R$)", value="0"
)

default_periodo_sidebar = datetime.today().strftime("%Y-%m") + " - período"
nome_periodo_sidebar = st.sidebar.text_input(
    "Nome do período (para histórico)",
    value=default_periodo_sidebar,
    help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
)

st.sidebar.markdown("---")
st.sidebar.markdown("Feito para a **Tempero das Gurias** 💕\n\n")

# Info do usuário logado e botão de sair
if st.session_state.get("auth_ok"):
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Usuário:** {current_user()}")
    st.sidebar.markdown(f"**Perfil:** {current_role()}")
    if st.sidebar.button("Sair"):
        st.session_state.clear()
        st.experimental_rerun()

# ========================
#  Processamento principal
# ========================

# Unificação dos arquivos e parâmetros: prioriza campos da área central;
# se estiverem vazios, usa os valores da barra lateral.
arquivo_itau_ativo = arquivo_itau or arquivo_itau_sidebar
arquivo_pag_ativo = arquivo_pag or arquivo_pag_sidebar

saldo_inicial_str = saldo_inicial_input_sidebar or saldo_inicial_input
nome_periodo_ativo = nome_periodo_sidebar or nome_periodo

dados_carregados = False
mensagem_erro = ""

df_consolidado = pd.DataFrame()
df_resumo_origem = pd.DataFrame()
df_resumo_categoria = pd.DataFrame()
df_caixa_diario = pd.DataFrame()

if arquivo_itau_ativo and arquivo_pag_ativo:
    try:
        df_itau = ler_extrato_itau(arquivo_itau_ativo)
        df_pag = ler_extrato_pagseguro(arquivo_pag_ativo)

        df_consolidado = consolidar_itau_pagseguro(df_itau, df_pag)
        saldo_inicial = parse_numero_br(saldo_inicial_str)
        df_caixa_diario = gerar_caixa_diario(df_consolidado, saldo_inicial)

        df_consolidado = aplicar_regras_tempero(df_consolidado)
        df_resumo_origem = gerar_resumo(df_consolidado)
        df_resumo_categoria = resumo_por_categoria_tempero(df_consolidado)

        dados_carregados = True
    except Exception as e:
        mensagem_erro = f"Erro ao processar os arquivos: {e}"

# ========================
#  Abas principais
# ========================

tab1, tab2, tab3, tab4 = st.tabs(
    ["💵 Caixa Diário", "📊 Fechamento Mensal", "🧾 Conferência & Categorias", "📚 Histórico & Comparativos"]
)

# ---------- ABA 1: Caixa Diário ----------

with tab1:
    require_role("admin", "operador")

    st.markdown(
        '<div class="tempero-section-title">💵 Caixa diário em dinheiro</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-section-sub">'
        "Registro manual do caixa em dinheiro (fora dos extratos bancários). "
        "Funcionalidade do caixa diário em dinheiro ainda pode ser detalhada aqui."
        "</div>",
        unsafe_allow_html=True,
    )

    df_caixa_global = carregar_caixa_global()

    st.write("Em breve: tela detalhada para lançamentos diários de caixa em dinheiro.")
    st.dataframe(df_caixa_global, use_container_width=True)

# ---------- ABA 2: Fechamento Mensal ----------

with tab2:
    require_role("admin", "operador")

    st.markdown(
        '<div class="tempero-section-title">📊 Fechamento Mensal</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-section-sub">'
        "Resumo consolidado do período com base nos extratos bancários."
        "</div>",
        unsafe_allow_html=True,
    )

    if mensagem_erro:
        st.error(mensagem_erro)
    elif not dados_carregados:
        st.info(
            "Faça o upload dos extratos do Itaú e PagSeguro (acima ou na barra lateral) "
            "para ver o resumo consolidado."
        )
    else:
        exibir_kpis_resumo(df_resumo_origem)

        st.markdown(
            '<div class="tempero-section-title">Tabela consolidada</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tempero-section-sub">'
            "Movimentações do período, unindo Itaú e PagSeguro."
            "</div>",
            unsafe_allow_html=True,
        )

        df_display = df_consolidado.copy()
        df_display["Data"] = df_display["Data"].dt.strftime("%d/%m/%Y")
        df_display["Valor"] = df_display["Valor"].apply(formatar_moeda)
        st.dataframe(df_display, use_container_width=True)

        st.markdown(
            '<div class="tempero-section-title">Resumo por origem</div>',
            unsafe_allow_html=True,
        )
        df_resumo_display = df_resumo_origem.copy()
        for col in ["Entradas", "Saídas", "Resultado"]:
            df_resumo_display[col] = df_resumo_display[col].apply(formatar_moeda)
        st.dataframe(df_resumo_display, use_container_width=True)

        st.markdown(
            '<div class="tempero-section-title">Resumo por categoria</div>',
            unsafe_allow_html=True,
        )
        df_cat_display = df_resumo_categoria.copy()
        for col in ["Entradas", "Saídas", "Resultado"]:
            df_cat_display[col] = df_cat_display[col].apply(formatar_moeda)
        st.dataframe(df_cat_display, use_container_width=True)

        if st.button("Baixar Excel do fechamento"):
            output = gerar_planilha_excel(
                df_consolidado,
                df_resumo_origem,
                df_resumo_categoria,
                df_caixa_diario,
                nome_periodo_ativo,
            )
            st.download_button(
                label="Download Excel",
                data=output,
                file_name=f"fechamento_tempero_{nome_periodo_ativo}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

# ---------- ABA 3: Conferência & Categorias ----------

with tab3:
    require_role("admin")
    st.markdown(
        '<div class="tempero-section-title">🧾 Conferência de lançamentos e categorias</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-section-sub">'
        "Ajuste manual de categorias e criação de regras automáticas."
        "</div>",
        unsafe_allow_html=True,
    )

    st.write("Em breve: tela para gerenciar regras de categorização e conferir lançamentos.")

# ---------- ABA 4: Histórico & Comparativos ----------

with tab4:
    require_role("admin")
    st.markdown(
        '<div class="tempero-section-title">📚 Histórico & Comparativos</div>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div class="tempero-section-sub">'
        "Consulta de fechamentos anteriores e comparativos de resultado."
        "</div>",
        unsafe_allow_html=True,
    )

    st.write("Em breve: integração com Google Drive para carregar histórico consolidado.")
