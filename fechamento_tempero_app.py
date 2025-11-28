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

PRIMARY_COLOR = "#F06BAA"     # rosa médio
BACKGROUND_SOFT = "#FDF2F7"   # rosinha de fundo
TEXT_DARK = "#333333"

# dicionário global de regras (carregado em runtime)
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
            margin: 0.5rem 0 0.25rem 0;
        }}
        .tempero-section-sub {{
            font-size: 0.85rem;
            color: #777777;
            margin-bottom: 0.6rem;
        }}

        /* Cards de métricas */
        .tempero-metric-card {{
            border-radius: 14px;
            padding: 0.9rem 1.1rem;
            background-color: #ffffff;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.08);
            border: 1px solid rgba(148, 163, 184, 0.35);
        }}
        .tempero-metric-label {{
            font-size: 0.8rem;
            color: #6b7280;
        }}
        .tempero-metric-value {{
            font-size: 1.1rem;
            font-weight: 700;
            color: {TEXT_DARK};
        }}

        /* Card genérico */
        .tempero-card {{
            border-radius: 14px;
            padding: 1rem 1.2rem;
            background-color: #ffffff;
            box-shadow: 0 6px 18px rgba(15, 23, 42, 0.06);
            border: 1px solid rgba(148, 163, 184, 0.20);
        }}

        /* Tabs */
        .stTabs [role="tab"] {{
            padding: 0.55rem 1rem;
            border-radius: 999px;
            color: #555555 !important;
            font-weight: 500;
        }}
        .stTabs [role="tab"][aria-selected="true"] {{
            background-color: {PRIMARY_COLOR}20 !important;
            color: {PRIMARY_COLOR} !important;
            border-bottom-color: transparent !important;
        }}

        /* Tabelas */
        .tempero-table table {{
            font-size: 0.85rem;
        }}

         /* Logo do login — sobrescreve estilo padrão do Streamlit */
         .login-logo img {
             width: 120px !important;
             max-width: 120px !important;
             height: auto !important;
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
           Card de login (form)
           ========= */
        [data-testid="stForm"] {{
            max-width: 480px;              /* largura do card */
            margin: 0 auto 0 auto;         /* centraliza */
            padding: 1.3rem 1.6rem 1.4rem 1.6rem;
            background-color: #ffffff;
            border-radius: 14px;
            box-shadow: 0 10px 30px rgba(15, 23, 42, 0.10);
            border: 1px solid rgba(148, 163, 184, 0.35);
        }}

        /* Inputs do login um pouco mais compactos */
        input[type="text"], input[type="password"] {{
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
    header_row = start_row
    n_rows = len(df)
    n_cols = len(df.columns)

    # Cabeçalho
    for col_idx in range(1, n_cols + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="center")

    # Congela linha de cabeçalho
    ws.freeze_panes = ws[f"A{header_row + 1}"]

    # Ajusta largura das colunas
    for col_idx, _ in enumerate(df.columns, start=1):
        max_len = 0
        for row_idx in range(header_row, header_row + 1 + n_rows):
            cell = ws.cell(row=row_idx, column=col_idx)
            if cell.value is not None:
                value = cell.value
                if isinstance(value, (int, float)):
                    value = f"{value:.2f}"
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    # Aplica formato de moeda para colunas de valor
    col_names_lower = [str(c).lower() for c in df.columns]
    for col_idx, col_name in enumerate(col_names_lower, start=1):
        if any(
            col_name.startswith(prefix)
            for prefix in ("entradas", "saídas", "saidas", "resultado", "saldo", "valor")
        ):
            for row_idx in range(header_row + 1, header_row + 1 + n_rows):
                cell = ws.cell(row=row_idx, column=col_idx)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '"R$" #,##0.00'


# ========================
#  Autenticação com usuários e perfis
# ========================

def _load_users_from_secrets():
    """
    Lê usuários e perfis definidos em st.secrets["auth_users"].

    Estrutura esperada no secrets:

    [auth_users.ricardo]
    password = "..."
    role = "admin"
    """
    try:
        users_section = st.secrets["auth_users"]
    except Exception:
        users_section = {}

    users = {}
    for username, cfg in users_section.items():
        role_raw = cfg.get("role", "operador")
        users[username] = {
            "password": cfg.get("password"),
            "role": str(role_raw).strip().lower(),
        }
    return users


def current_user():
    return st.session_state.get("user")


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

    with st.form("login_form"):
        username = st.text_input("Usuário", key="login_username")
        col_senha, col_toggle = st.columns([3, 1])
        with col_senha:
            mostrar = st.checkbox("Mostrar senha", value=False)
        tipo = "text" if mostrar else "password"
        senha = st.text_input("Senha", type=tipo, key="login_password")

        entrar = st.form_submit_button("Entrar")

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
        <div class="login-footer">
            Acesso exclusivo à equipe interna da Tempero das Gurias.<br/>
            Ações são registradas por usuário.
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.stop()


# ========================
#  Funções auxiliares
# ========================

def parse_numero_br(valor_str):
    """
    Converte string no formato brasileiro (1.234,56) para float.
    Se não for possível, retorna 0.0.
    """
    if pd.isna(valor_str):
        return 0.0

    if isinstance(valor_str, (int, float)):
        return float(valor_str)

    s = str(valor_str).strip()
    if s == "":
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
    Salva as regras de categorização no arquivo JSON.
    """
    with RULES_PATH.open("w", encoding="utf-8") as f:
        json.dump(REGRAS_CATEGORIA, f, ensure_ascii=False, indent=2)


def carregar_categorias_personalizadas():
    """
    Carrega categorias personalizadas de arquivo JSON.
    """
    if CATEGORIAS_PATH.exists():
        try:
            with CATEGORIAS_PATH.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def salvar_categorias_personalizadas(categorias_dict):
    """
    Salva categorias personalizadas em arquivo JSON.
    """
    with CATEGORIAS_PATH.open("w", encoding="utf-8") as f:
        json.dump(categorias_dict, f, ensure_ascii=False, indent=2)


def aplicar_regras_categorias(df, col_descr="Descrição"):
    """
    Recebe um DataFrame e retorna uma Series com as categorias
    segundo as REGRAS_CATEGORIA.
    """
    categorias = []
    for _, row in df.iterrows():
        descricao = str(row.get(col_descr, "")).upper()
        categoria = "Outros"
        for cat, regras in REGRAS_CATEGORIA.items():
            for regra in regras:
                if regra.upper() in descricao:
                    categoria = cat
                    break
            if categoria != "Outros":
                break
        categorias.append(categoria)

    return pd.Series(categorias, index=df.index)


def ler_extrato_itau(arquivo):
    """
    Lê o extrato do Itaú (CSV/Excel), faz parsing das colunas principais
    e retorna um DataFrame padronizado.
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

    col_lancto = None
    for c in df.columns:
        if "lançamento" in c.lower() or "lancamento" in c.lower():
            col_lancto = c
            break

    col_valor = None
    for c in df.columns:
        if "valor" in c.lower():
            col_valor = c
            break

    if col_data is None or col_lancto is None or col_valor is None:
        raise ValueError(
            "Não foi possível identificar automaticamente as colunas de Data, "
            "Lançamento e Valor no extrato do Itaú."
        )

    df["Data"] = pd.to_datetime(df[col_data], dayfirst=True, errors="coerce")
    df["Descrição"] = df[col_lancto].astype(str).str.strip()
    df["Valor"] = df[col_valor].apply(parse_numero_br)

    if "Débito" in df.columns and "Crédito" in df.columns:
        debitos = df["Débito"].apply(parse_numero_br)
        creditos = df["Crédito"].apply(parse_numero_br)
        df["Valor"] = creditos - debitos

    df = df.dropna(subset=["Data"]).copy()
    df = df[["Data", "Descrição", "Valor"]]

    df["Tipo"] = df["Valor"].apply(lambda x: "Entrada" if x > 0 else "Saída")

    df["Categoria"] = aplicar_regras_categorias(df, col_descr="Descrição")

    return df


def ler_extrato_pagseguro(arquivo):
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
        if "descrição" in c.lower() or "descricao" in c.lower():
            col_descr = c
            break

    col_entradas = None
    col_saidas = None
    for c in df.columns:
        cl = c.lower()
        if "entrada" in cl:
            col_entradas = c
        elif "saída" in cl or "saida" in cl:
            col_saidas = c

    if col_data is None or col_tipo is None or col_descr is None:
        raise ValueError(
            "Não foi possível identificar automaticamente as colunas de Data, "
            "Tipo e Descrição no extrato do PagSeguro."
        )

    df["Data"] = pd.to_datetime(df[col_data], dayfirst=True, errors="coerce")
    df["Tipo"] = df[col_tipo].astype(str).str.strip()
    df["Descrição"] = df[col_descr].astype(str).str.strip()

    df["Entradas"] = df[col_entradas].apply(parse_numero_br) if col_entradas else 0.0
    df["Saídas"] = df[col_saidas].apply(parse_numero_br) if col_saidas else 0.0

    df["Valor"] = df["Entradas"] - df["Saídas"]

    df = df.dropna(subset=["Data"]).copy()
    df = df[["Data", "Descrição", "Tipo", "Valor"]]

    df["Categoria"] = aplicar_regras_categorias(df, col_descr="Descrição")

    return df


def consolidar_itau_pagseguro(df_itau, df_pag):
    """
    Recebe dois DataFrames (Itaú e PagSeguro) já padronizados
    e retorna um consolidado.
    """
    df_itau["Origem"] = "Itaú"
    df_pag["Origem"] = "PagSeguro"
    df = pd.concat([df_itau, df_pag], ignore_index=True)

    df["Data"] = pd.to_datetime(df["Data"])
    df = df.sort_values("Data")

    df["Entradas"] = df["Valor"].apply(lambda x: x if x > 0 else 0)
    df["Saídas"] = df["Valor"].apply(lambda x: -x if x < 0 else 0)

    return df


def filtrar_periodo(df, data_inicio, data_fim):
    """
    Filtra o DataFrame para o intervalo [data_inicio, data_fim].
    """
    mask = (df["Data"] >= data_inicio) & (df["Data"] <= data_fim)
    return df.loc[mask].copy()


def gerar_resumo(df_consolidado):
    """
    Gera um resumo (entradas, saídas, resultado) por origem e total.
    """
    if df_consolidado.empty:
        return pd.DataFrame(
            columns=["Origem", "Entradas", "Saídas", "Resultado"]
        )

    resumo = (
        df_consolidado.groupby("Origem")[["Entradas", "Saídas"]]
        .sum()
        .reset_index()
    )
    resumo["Resultado"] = resumo["Entradas"] - resumo["Saídas"]

    total_row = {
        "Origem": "Consolidado",
        "Entradas": resumo["Entradas"].sum(),
        "Saídas": resumo["Saídas"].sum(),
        "Resultado": resumo["Entradas"].sum() - resumo["Saídas"].sum(),
    }
    resumo = pd.concat([resumo, pd.DataFrame([total_row])], ignore_index=True)

    return resumo


def gerar_resumo_por_categoria(df_consolidado):
    """
    Gera um resumo de entradas/saídas por categoria.
    """
    if df_consolidado.empty:
        return pd.DataFrame(columns=["Categoria", "Entradas", "Saídas", "Resultado"])

    resumo = (
        df_consolidado.groupby("Categoria")[["Entradas", "Saídas"]]
        .sum()
        .reset_index()
    )
    resumo["Resultado"] = resumo["Entradas"] - resumo["Saídas"]
    resumo = resumo.sort_values("Resultado", ascending=False)

    return resumo


def gerar_caixa_diario(df_consolidado, saldo_inicial=0.0):
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
    ws_consolidado = wb.active
    ws_consolidado.title = "Consolidado"

    for r_idx, row in enumerate(
        pd.concat(
            [
                pd.DataFrame(
                    [[None, "Consolidado do período", None, None]],
                    columns=["Data", "Descrição", "Entrada", "Saída"],
                ),
                df_consolidado[
                    ["Data", "Descrição", "Entradas", "Saídas", "Origem", "Categoria"]
                ],
            ]
        ).itertuples(index=False),
        start=1,
    ):
        for c_idx, value in enumerate(row, start=1):
            cell = ws_consolidado.cell(row=r_idx, column=c_idx)
            if isinstance(value, pd.Timestamp):
                cell.value = value.date()
            else:
                cell.value = value

    formatar_tabela_excel(ws_consolidado, df_consolidado, start_row=2)

    ws_origem = wb.create_sheet("Resumo por Origem")
    for r_idx, row in enumerate(df_resumo_origem.itertuples(index=False), start=1):
        for c_idx, value in enumerate(row, start=1):
            ws_origem.cell(row=r_idx, column=c_idx).value = value
    formatar_tabela_excel(ws_origem, df_resumo_origem, start_row=1)

    ws_cat = wb.create_sheet("Resumo por Categoria")
    for r_idx, row in enumerate(df_resumo_categoria.itertuples(index=False), start=1):
        for c_idx, value in enumerate(row, start=1):
            ws_cat.cell(row=r_idx, column=c_idx).value = value
    formatar_tabela_excel(ws_cat, df_resumo_categoria, start_row=1)

    ws_caixa = wb.create_sheet("Caixa Diário")
    for r_idx, row in enumerate(df_caixa_diario.itertuples(index=False), start=1):
        for c_idx, value in enumerate(row, start=1):
            ws_caixa.cell(row=r_idx, column=c_idx).value = value
    formatar_tabela_excel(ws_caixa, df_caixa_diario, start_row=1)

    for ws in [ws_consolidado, ws_origem, ws_cat, ws_caixa]:
        ws.page_setup.orientation = "landscape"

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# ========================
#  Google Drive (OAuth)
# ========================

def get_gdrive_service():
    """
    Cria o cliente da API do Google Drive usando OAuth (token em st.secrets["gdrive_oauth"]).
    Faz refresh explícito do token, e trata erros de autenticação (invalid_grant).
    """
    info = st.secrets["gdrive_oauth"]

    scopes = info.get("scopes", ["https://www.googleapis.com/auth/drive"])
    if isinstance(scopes, str):
        scopes = [scopes]

    creds = Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=scopes,
    )

    try:
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())

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

        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = service.files().create(body=file_metadata, fields="id").execute()
        return folder["id"]
    except Exception as e:
        st.error(f"Erro ao localizar/criar pasta no Google Drive: {e}")
        st.stop()


def list_history_from_gdrive():
    service = get_gdrive_service()
    folder_name = st.secrets["gdrive_oauth"]["GDRIVE_FOLDER_NAME"]
    folder_id = get_or_create_folder(service, folder_name)

    try:
        resp = (
            service.files()
            .list(
                q=f"'{folder_id}' in parents and mimeType='application/json' and trashed = false",
                spaces="drive",
                fields="files(id, name, createdTime)",
                orderBy="createdTime desc",
            )
            .execute()
        )
        return resp.get("files", [])
    except Exception as e:
        st.error(f"Erro ao listar arquivos no Google Drive: {e}")
        return []


def download_history_file(file_id):
    service = get_gdrive_service()
    try:
        request = service.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        fh.seek(0)
        return fh.read()
    except Exception as e:
        st.error(f"Erro ao baixar arquivo do histórico: {e}")
        return None


def upload_history_file(content: bytes, filename: str):
    service = get_gdrive_service()
    folder_name = st.secrets["gdrive_oauth"]["GDRIVE_FOLDER_NAME"]
    folder_id = get_or_create_folder(service, folder_name)

    try:
        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaIoBaseUpload(BytesIO(content), mimetype="application/json")
        file = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id")
            .execute()
        )
        return file.get("id")
    except Exception as e:
        st.error(f"Erro ao fazer upload do histórico no Google Drive: {e}")
        return None


def load_cash_from_gdrive():
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

        data = df_caixa.copy()
        data["Data"] = data["Data"].dt.strftime("%Y-%m-%d")
        content_bytes = data.to_json(orient="records", force_ascii=False).encode("utf-8")

        upload_history_file(content_bytes, "caixa_global.json")
    except Exception as e:
        st.error(f"Erro ao salvar caixa global no Google Drive: {e}")


# ========================
#  Funções específicas da Tempero
# ========================

def aplicar_regras_tempero(df_consolidado):
    """
    Esta função aplica a categorização conforme as regras
    específicas da Tempero das Gurias.
    """
    carregar_regras()
    df_consolidado["Categoria"] = aplicar_regras_categorias(df_consolidado)
    return df_consolidado


def resumo_por_categoria_tempero(df_consolidado):
    df_temp = df_consolidado.copy()
    return gerar_resumo_por_categoria(df_temp)


def atualizar_caixa_diario_global(df_caixa_global, data, descricao, tipo, valor):
    nova_linha = {
        "Data": data,
        "Descrição": descricao,
        "Tipo": tipo,
        "Valor": valor,
    }
    df_caixa_global = pd.concat(
        [df_caixa_global, pd.DataFrame([nova_linha])], ignore_index=True
    )
    df_caixa_global = df_caixa_global.sort_values("Data")
    return df_caixa_global


def exibir_kpis_resumo(df_resumo):
    consolidado = df_resumo[df_resumo["Origem"] == "Consolidado"]
    if consolidado.empty:
        entradas = 0.0
        saidas = 0.0
        resultado = 0.0
    else:
        row = consolidado.iloc[0]
        entradas = row["Entradas"]
        saidas = row["Saídas"]
        resultado = row["Resultado"]

    col1, col2, col3 = st.columns(3)

    with col1:
        st.markdown('<div class="tempero-metric-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="tempero-metric-label">Entradas consolidadas</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="tempero-metric-value">{formatar_moeda(entradas)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col2:
        st.markdown('<div class="tempero-metric-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="tempero-metric-label">Saídas consolidadas</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="tempero-metric-value">{formatar_moeda(saidas)}</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)

    with col3:
        st.markdown('<div class="tempero-metric-card">', unsafe_allow_html=True)
        st.markdown(
            '<div class="tempero-metric-label">Resultado consolidado</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="tempero-metric-value">{formatar_moeda(resultado)}</div>',
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
st.sidebar.markdown(
    "Feito para a **Tempero das Gurias** 💕\n\n"
)

# Info do usuário logado e botão de sair
if st.session_state.get("auth_ok"):
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Usuário:** {current_user()}")
    st.sidebar.markdown(f"**Perfil:** {current_role()}")
    if st.sidebar.button("Sair"):
        for k in ["auth_ok", "user", "role"]:
            st.session_state.pop(k, None)
        st.rerun()

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

df_caixa_global = st.session_state["df_caixa_global"].copy()

ano_mes_ref = datetime.today().strftime("%Y-%m")
if not df_caixa_global.empty:
    df_caixa_global["Data"] = pd.to_datetime(
        df_caixa_global["Data"], errors="coerce"
    )

# ========================
#  Processamento principal
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
        "Registro manual do caixa em dinheiro (fora dos extratos bancários)."
        "</div>",
        unsafe_allow_html=True,
    )

    st.write("Funcionalidade do caixa diário em dinheiro ainda pode ser detalhada aqui.")
    # Aqui você pode manter/colocar a lógica já existente para o caixa diário.


# ---------- ABA 2: Fechamento Mensal ----------

with tab2:
    require_role("admin")
    st.markdown(
        '<div class="tempero-section-title">Resumo do período</div>',
        unsafe_allow_html=True,
    )

    if mensagem_erro:
        st.error(mensagem_erro)
    elif not dados_carregados:
        st.info(
            "Faça o upload dos extratos do Itaú e PagSeguro na barra lateral "
            "para ver o resumo consolidado."
        )
    else:
        exibir_kpis_resumo(df_resumo_origem)

        st.markdown(
            '<div class="tempero-section-title">Tabela consolidada</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tempero-section-sub">Movimentações do período, unindo Itaú e PagSeguro.</div>',
            unsafe_allow_html=True,
        )

        df_view = df_consolidado.copy()
        df_view["Data"] = df_view["Data"].dt.strftime("%d/%m/%Y")
        df_view["Entradas"] = df_view["Entradas"].apply(formatar_moeda)
        df_view["Saídas"] = df_view["Saídas"].apply(formatar_moeda)
        df_view["Valor"] = df_view["Valor"].apply(formatar_moeda)

        st.dataframe(df_view, use_container_width=True)

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
                nome_periodo_sidebar,
            )
            st.download_button(
                label="Download Excel",
                data=output,
                file_name=f"fechamento_tempero_{nome_periodo_sidebar}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


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
