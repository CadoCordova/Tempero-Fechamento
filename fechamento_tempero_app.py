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
        """
        <style>
        /* Fundo geral da aplicação */
        .main {
            background: radial-gradient(circle at top left, #ffe9f0 0, #ffffff 45%, #f4f7ff 100%);
        }

        /* Container principal do Streamlit */
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }

        /* Títulos gerais do app */
        .tempero-title {
            font-size: 2rem;
            font-weight: 700;
            text-align: center;
            color: #2b2d42;
            margin-bottom: 0.25rem;
        }

        .tempero-subtitle {
            font-size: 0.95rem;
            text-align: center;
            color: #6c757d;
            margin-bottom: 2.0rem;
        }

        /* Layout da tela de login */
        .login-wrapper {
           /* Em vez de tentar centralizar com flex vertical,
              usamos um padding-top fixo que funciona melhor no Streamlit */
           display: flex;
           justify-content: center;
           padding-top: 12vh;   /* sobe o card na tela */
           padding-bottom: 6vh;
        }

        /* Opcional: garante que o card não “cole” nas laterais em telas menores */
        .login-card {
           background-color: #ffffff;
           padding: 2.5rem 3rem;
           border-radius: 18px;
           box-shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
           max-width: 420px;
           width: 100%;
           border: 1px solid rgba(148, 163, 184, 0.25);
           margin: 0 1rem;  /* novo: respiro lateral em telas pequenas */
        }

        .login-card {
            background-color: #ffffff;
            padding: 2.5rem 3rem;
            border-radius: 18px;
            box-shadow: 0 18px 40px rgba(15, 23, 42, 0.12);
            max-width: 420px;
            width: 100%;
            border: 1px solid rgba(148, 163, 184, 0.25);
        }

        .login-header {
            text-align: center;
            margin-bottom: 1.8rem;
        }

        .login-logo {
            font-size: 1.3rem;
            font-weight: 700;
            color: #e07a5f;
            margin-bottom: 0.25rem;
        }

        .login-sistema {
            font-size: 0.85rem;
            color: #6b7280;
        }

        .login-fields label {
            font-weight: 600 !important;
            color: #4b5563 !important;
        }

        .login-footer {
            margin-top: 1.6rem;
            font-size: 0.78rem;
            color: #9ca3af;
            text-align: center;
        }

        /* Botão de entrar mais destacado */
        .stButton>button {
            width: 100%;
            border-radius: 999px;
            padding: 0.55rem 1.4rem;
            font-weight: 600;
            border: none;
        }

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
            value = ws.cell(row=row_idx, column=col_idx).value
            if value is None:
                continue
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
    Lê usuários e perfis de st.secrets["auth_users"].

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
        # cfg é um objeto tipo Secrets; acessamos como dict
        role_raw = cfg.get("role", "operador")
        users[username] = {
            "password": cfg.get("password"),
            # Normalizamos o papel em minúsculas
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
    Exemplo: has_role("admin", "financeiro")
    """
    role = current_role()
    # Normaliza roles recebidos para minúsculas
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
    - Se auth_ok já estiver na sessão, apenas retorna.
    - Caso contrário, mostra tela de login em formato de card centralizado.
    """
    if st.session_state.get("auth_ok"):
        return

    inject_css()

    # Wrapper centralizado para o card de login
    st.markdown('<div class="login-wrapper">', unsafe_allow_html=True)
    with st.container():
        st.markdown('<div class="login-card">', unsafe_allow_html=True)

        # Cabeçalho do login
        st.markdown(
            """
            <div class="login-header">
                <div class="login-logo">Tempero das Gurias</div>
                <div class="login-sistema">Painel de fechamento financeiro</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        # Campos de login
        users = _load_users_from_secrets()

        with st.form("login_form"):
            st.markdown('<div class="login-fields">', unsafe_allow_html=True)

            username = st.text_input("Usuário", key="login_username")

            col_senha, col_toggle = st.columns([3, 1])
            with col_senha:
                mostrar = st.checkbox("Mostrar senha", value=False)
            tipo = "text" if mostrar else "password"
            senha = st.text_input("Senha", type=tipo, key="login_password")

            st.markdown('</div>', unsafe_allow_html=True)

            entrar = st.form_submit_button("Entrar")

        if entrar:
            # 1) Se existirem usuários configurados em auth_users, usamos SEMPRE isso
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
            # 2) Fallback: APP_PASSWORD
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

        # Rodapé / info
        st.markdown(
            """
            <div class="login-footer">
                Acesso exclusivo à equipe interna da Tempero das Gurias.<br/>
                Use seu usuário pessoal. Ações são registradas por usuário.
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown('</div>', unsafe_allow_html=True)  # fecha login-card

    st.markdown('</div>', unsafe_allow_html=True)  # fecha login-wrapper

    # Se chegou aqui, ainda não autenticou
    st.stop()

# ========================
#  Funções auxiliares
# ========================

def parse_numero_br(valor):
    if valor is None:
        return 0.0

    if isinstance(valor, (int, float)):
        if isinstance(valor, float) and math.isnan(valor):
            return 0.0
        return float(valor)

    s = str(valor)
    s = s.replace("R$", "").strip()
    if s == "" or s == "-":
        return 0.0

    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def normalizar_texto(txt):
    if txt is None:
        return ""
    s = str(txt).upper()
    substituicoes = [
        ("Ó", "O"), ("Ô", "O"), ("Õ", "O"),
        ("Í", "I"),
        ("Á", "A"), ("À", "A"), ("Ã", "A"),
        ("É", "E"), ("Ê", "E"),
        ("Ú", "U"),
        ("Ç", "C"),
    ]
    for ac, sem in substituicoes:
        s = s.replace(ac, sem)
    return s


def extrair_descricao_linha(linha: dict):
    if "descricao" in linha and linha["descricao"] not in (None, ""):
        return linha["descricao"]

    partes = []

    for k, v in linha.items():
        if not isinstance(k, str):
            continue
        if v is None:
            continue
        kl = normalizar_texto(k.strip())
        vs = str(v).strip()
        if vs == "":
            continue
        if "HIST" in kl or "DESCR" in kl:
            partes.append(vs)

    candidatos_ignorados = [
        "DATA",
        "VALOR",
        "VALORES",
        "DEBITO",
        "DEBITO(-)",
        "DEBITO (+)",
        "DEBITO (-)",
        "CREDITO",
        "CREDITO(+)",
        "CREDITO (+)",
        "CREDITO (-)",
        "ENTRADA",
        "ENTRADAS",
        "SAIDA",
        "SAIDAS",
        "SALDO",
    ]

    for k, v in linha.items():
        if not isinstance(k, str):
            continue
        if v is None:
            continue
        kl = normalizar_texto(k.strip())
        vs = str(v).strip()
        if vs == "":
            continue
        if kl in candidatos_ignorados:
            continue
        if vs not in partes:
            partes.append(vs)

    if not partes:
        return None

    return " | ".join(partes)


def ler_arquivo_tabela_upload(uploaded_file):
    """
    Lê CSV/XLSX de bancos aceitando o extrato ORIGINAL, mesmo com cabeçalho e
    informações antes da tabela.
    """
    suffix = Path(uploaded_file.name).suffix.lower()

    if suffix in (".csv", ".txt"):
        df = pd.read_csv(uploaded_file, sep=";")
    elif suffix in (".xlsx", ".xls"):
        raw = pd.read_excel(uploaded_file, header=None)

        header_idx = None
        for i, row in raw.iterrows():
            valores = [
                str(x).strip().upper()
                for x in row.tolist()
                if not pd.isna(x)
            ]
            if not valores:
                continue

            if "DATA" in valores and any(
                col in valores
                for col in [
                    "LANÇAMENTO",
                    "LANCAMENTO",
                    "LANÇAMENTOS",
                    "DESCRIÇÃO",
                    "DESCRICAO",
                    "TIPO",
                ]
            ):
                header_idx = i
                break

        if header_idx is not None:
            header_row = raw.iloc[header_idx].tolist()
            cols = []
            for v in header_row:
                if isinstance(v, str):
                    cols.append(v.strip())
                elif pd.isna(v):
                    cols.append("")
                else:
                    cols.append(str(v))

            df = raw.iloc[header_idx + 1 :].copy()
            df.columns = cols
            df = df.dropna(how="all").reset_index(drop=True)
        else:
            df = pd.read_excel(uploaded_file)
    else:
        raise RuntimeError(f"Formato não suportado: {suffix}. Use .csv ou .xlsx.")

    df = df.rename(columns=lambda c: str(c).strip())

    records = df.to_dict(orient="records")
    linhas = []
    for rec in records:
        nova_linha = {}
        for k, v in rec.items():
            if isinstance(k, str):
                k = k.strip()
            nova_linha[k] = v
        linhas.append(nova_linha)

    return linhas


def carregar_extrato_itau_upload(uploaded_file):
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    linhas = ler_arquivo_tabela_upload(uploaded_file)

    for linha in linhas:
        descricao = extrair_descricao_linha(linha)
        desc_norm = normalizar_texto(descricao)

        if (
            "SALDO ANTERIOR" in desc_norm
            or "SALDO TOTAL DISPONIVEL DIA" in desc_norm
            or "SALDO TOTAL DISPONÍVEL DIA" in desc_norm
            or "SALDO DO DIA" in desc_norm
        ):
            continue

        valor = parse_numero_br(
            linha.get("Valor")
            or linha.get("VALOR")
            or linha.get("valor")
            or linha.get("Valor (R$)")
            or 0
        )

        if valor == 0.0:
            debito = 0.0
            credito = 0.0
            for k, v in linha.items():
                if not isinstance(k, str):
                    continue
                kl = normalizar_texto(k.strip())
                if "DEBITO" in kl:
                    debito = parse_numero_br(v)
                if "CREDITO" in kl:
                    credito = parse_numero_br(v)
            if debito != 0.0 or credito != 0.0:
                valor = credito - debito

        if valor > 0:
            entradas += valor
        elif valor < 0:
            saidas += valor

        movimentos.append(
            {
                "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
                "descricao": descricao,
                "valor": valor,
                "conta": "Itau",
            }
        )

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


def carregar_extrato_pagseguro_upload(uploaded_file):
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    linhas = ler_arquivo_tabela_upload(uploaded_file)

    for linha in linhas:
        descricao = extrair_descricao_linha(linha)
        desc_norm = normalizar_texto(descricao)

        if "SALDO DO DIA" in desc_norm or "SALDO DIA" in desc_norm:
            continue

        entrada = parse_numero_br(
            linha.get("Entradas") or linha.get("ENTRADAS") or linha.get("entradas") or 0
        )
        saida = parse_numero_br(
            linha.get("Saidas")
            or linha.get("SAIDAS")
            or linha.get("Saídas")
            or linha.get("saídas")
            or 0
        )

        valor = 0.0
        if entrada != 0:
            ent = abs(entrada)
            valor += ent
            entradas += ent
        if saida != 0:
            sai = abs(saida)
            valor -= sai
            saidas -= sai

        movimentos.append(
            {
                "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
                "descricao": descricao,
                "valor": valor,
                "conta": "PagSeguro",
            }
        )

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


# ========================
#  Regras de categorização
# ========================

def carregar_regras():
    if RULES_PATH.exists():
        try:
            with RULES_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def salvar_regras(regras: dict):
    with RULES_PATH.open("w", encoding="utf-8") as f:
        json.dump(regras, f, ensure_ascii=False, indent=2)


def carregar_categorias_personalizadas():
    if CATEGORIAS_PATH.exists():
        try:
            with CATEGORIAS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def salvar_categorias_personalizadas(lista):
    with CATEGORIAS_PATH.open("w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


def classificar_categoria(mov):
    desc_orig = mov.get("descricao")
    desc_norm = normalizar_texto(desc_orig)
    valor = mov.get("valor", 0.0)

    if REGRAS_CATEGORIA:
        for padrao, categoria in REGRAS_CATEGORIA.items():
            if padrao in desc_norm:
                return categoria

    if "ANTINSECT" in desc_norm:
        return "Dedetização / Controle de Pragas"

    if "CIA ESTADUAL DE DIST" in desc_norm or "CEEE" in desc_norm or "ENERGIA ELETRICA" in desc_norm:
        return "Energia Elétrica"

    if "RECH CONTABILIDADE" in desc_norm or "RECH CONT" in desc_norm:
        return "Contabilidade e RH"

    if (
        "BUSINESS      0503-2852" in desc_norm
        or "BUSINESS 0503-2852" in desc_norm
        or "ITAU UNIBANCO HOLDING S.A." in desc_norm
        or "CARTAO" in desc_norm
        or "CARTÃO" in desc_norm
    ):
        return "Fatura Cartão"

    if "APLICACAO" in desc_norm or "APLICAÇÃO" in desc_norm or "CDB" in desc_norm or "CREDBANCRF" in desc_norm:
        return "Investimentos (Aplicações)"

    if (
        "REND PAGO APLIC" in desc_norm
        or "RENDIMENTO APLIC" in desc_norm
        or "REND APLIC" in desc_norm
        or "RENDIMENTO" in desc_norm
    ):
        return "Rendimentos de Aplicações"

    if "ZOOP" in desc_norm or "ALUGUEL" in desc_norm:
        return "Aluguel Comercial"

    if "MOTOBOY" in desc_norm or "ENTREGA" in desc_norm:
        return "Motoboy / Entregas"

    if (
        "CAROLINE" in desc_norm
        or "VERONICA" in desc_norm
        or "VERONICA DA SILVA CARDOSO" in desc_norm
        or "EVELLYN" in desc_norm
        or "SALARIO" in desc_norm
        or "SALÁRIO" in desc_norm
        or "FOLHA" in desc_norm
    ):
        return "Folha de Pagamento"

    if "ANA PAULA" in desc_norm or "NUTRICIONISTA" in desc_norm:
        return "Nutricionista"

    if (
        "DARF" in desc_norm
        or "GPS" in desc_norm
        or "FGTS" in desc_norm
        or "INSS" in desc_norm
        or "SIMPLES NACIONAL" in desc_norm
        or "IMPOSTO" in desc_norm
    ):
        return "Impostos e Encargos"

    if (
        ("TRANSFERENCIA" in desc_norm or "TRANSFERÊNCIA" in desc_norm or "PIX" in desc_norm)
        and ("RICARDO" in desc_norm or "LIZIANI" in desc_norm or "LIZI" in desc_norm)
    ):
        return "Transferência Interna / Sócios"

    if valor > 0:
        return "Vendas / Receitas"
    if valor < 0:
        return "Fornecedores e Insumos"

    return "A Classificar"


def format_currency(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def slugify(texto: str) -> str:
    s = texto.strip().lower()
    repl = {
        "á": "a", "à": "a", "ã": "a", "â": "a",
        "é": "e", "ê": "e",
        "í": "i",
        "ó": "o", "ô": "o", "õ": "o",
        "ú": "u",
        "ç": "c",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    for ch in [" ", "/", "\\", "|", ";", ","]:
        s = s.replace(ch, "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "periodo"


def get_ano_mes(nome_periodo: str):
    """
    Extrai "YYYY-MM" do início do nome do período, se válido.
    (Usado só para filtrar a aba de Caixa Diário na UI.)
    """
    if not nome_periodo:
        return None
    parte = nome_periodo.strip()[:7]
    try:
        datetime.strptime(parte, "%Y-%m")
        return parte
    except Exception:
        return None


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
        # Se o token estiver expirado mas o refresh_token ainda for válido,
        # isso renova o access token em memória.
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())

        service = build("drive", "v3", credentials=creds)
        return service

    except RefreshError as e:
        # Aqui entra justamente o cenário do erro que você viu: invalid_grant
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
        # fallback genérico para algo inesperado
        st.error(f"Erro inesperado ao inicializar o Google Drive: {e}")
        st.stop()


def get_history_folder_id(service):
    """
    Obtém (ou cria) a pasta de históricos no Google Drive.
    Usa o nome definido em GDRIVE_FOLDER_NAME nos secrets (padrão: Tempero_Fechamentos).
    """
    if "gdrive_history_folder_id" in st.session_state:
        return st.session_state["gdrive_history_folder_id"]

    folder_name = st.secrets.get("GDRIVE_FOLDER_NAME", "Tempero_Fechamentos")

    query = (
        f"mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{folder_name}' and trashed = false"
    )

    results = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=10,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        folder_id = files[0]["id"]
    else:
        file_metadata = {
            "name": folder_name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        folder = service.files().create(body=file_metadata, fields="id").execute()
        folder_id = folder["id"]

    st.session_state["gdrive_history_folder_id"] = folder_id
    return folder_id


def upload_history_to_gdrive(buffer: BytesIO, filename: str):
    """
    Envia o arquivo Excel do fechamento para a pasta de históricos no Google Drive.
    """
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)

    buffer.seek(0)
    media = MediaIoBaseUpload(
        buffer,
        mimetype=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        resumable=False,
    )
    file_metadata = {"name": filename, "parents": [folder_id]}
    file = (
        service.files()
        .create(body=file_metadata, media_body=media, fields="id, name")
        .execute()
    )
    return file["id"]


def list_history_from_gdrive():
    """
    Lista os arquivos de fechamento salvos na pasta de históricos no Google Drive.
    Retorna uma lista de dicts com: id, name, modifiedTime.
    """
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)

    query = f"'{folder_id}' in parents and trashed = false"
    results = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=100,
        )
        .execute()
    )
    return results.get("files", [])


def download_history_file(file_id: str) -> BytesIO:
    """
    Faz download de um arquivo de histórico do Google Drive e retorna um BytesIO.
    """
    service = get_gdrive_service()
    request = service.files().get_media(fileId=file_id)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    buf.seek(0)
    return buf


def delete_history_file(file_id: str):
    """
    Exclui um arquivo de histórico do Google Drive.
    """
    service = get_gdrive_service()
    service.files().delete(fileId=file_id).execute()


# ========================
#  Livro-caixa de dinheiro no Drive
# ========================

def get_cash_file_name():
    return st.secrets.get("GDRIVE_CASH_FILE_NAME", "caixa_dinheiro.xlsx")


def get_cash_file_id(service, folder_id):
    """
    Procura o arquivo de caixa de dinheiro dentro da pasta de históricos.
    """
    filename = get_cash_file_name()
    query = (
        f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
    )
    results = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name)",
            pageSize=10,
        )
        .execute()
    )
    files = results.get("files", [])
    if files:
        return files[0]["id"]
    return None


def load_cash_from_gdrive():
    """
    Lê o livro-caixa de dinheiro (caixa_dinheiro.xlsx) do Drive.
    Se não existir, retorna DataFrame vazio com colunas padrão.
    """
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)
    file_id = get_cash_file_id(service, folder_id)

    if not file_id:
        return pd.DataFrame(columns=["Data", "Descrição", "Tipo", "Valor"])

    request = service.files().get_media(fileId=file_id)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        status, done = downloader.next_chunk()
    buf.seek(0)

    df = pd.read_excel(buf)

    # Normaliza colunas
    cols = [str(c).strip() for c in df.columns]
    df.columns = cols

    for col in ["Data", "Descrição", "Tipo", "Valor"]:
        if col not in df.columns:
            df[col] = None

    df = df[["Data", "Descrição", "Tipo", "Valor"]]
    return df


def save_cash_to_gdrive(df: pd.DataFrame):
    """
    Salva (ou atualiza) o livro-caixa de dinheiro no Drive.
    """
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)
    file_id = get_cash_file_id(service, folder_id)
    filename = get_cash_file_name()

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="CaixaDinheiro", index=False)
    buffer.seek(0)

    media = MediaIoBaseUpload(
        buffer,
        mimetype=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        resumable=False,
    )

    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        file_metadata = {"name": filename, "parents": [folder_id]}
        service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()


# ========================
#  Config Streamlit
# ========================

st.set_page_config(page_title="Fechamento Tempero das Gurias", layout="wide")
inject_css()
check_auth()

st.markdown(
    '<div class="tempero-title">💗 Tempero das Gurias — Painel Financeiro</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="tempero-subtitle">Fechamento mensal, caixa diário e histórico da loja em um único lugar.</div>',
    unsafe_allow_html=True,
)

# Barra lateral
st.sidebar.header("Configurações do período")

arquivo_itau = st.sidebar.file_uploader(
    "Extrato Itaú (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="itau"
)
arquivo_pag = st.sidebar.file_uploader(
    "Extrato PagSeguro (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="pagseguro"
)

saldo_inicial_input = st.sidebar.text_input(
    "Saldo inicial consolidado do período (R$)", value="0"
)

default_periodo = datetime.today().strftime("%Y-%m") + " - período"
nome_periodo = st.sidebar.text_input(
    "Nome do período (para histórico)",
    value=default_periodo,
    help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
)

st.sidebar.markdown("---")
st.sidebar.markdown(
    "Feito para a **Tempero das Gurias** 💕\n\n"
)

# Informações do usuário logado + botão de sair
if st.session_state.get("auth_ok"):
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Usuário:** {current_user()}  ")
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

ano_mes_ref = get_ano_mes(nome_periodo)

# Filtra para o período (YYYY-MM) selecionado NA UI da aba Caixa Diário
if not df_caixa_global.empty and ano_mes_ref:
    datas = pd.to_datetime(df_caixa_global["Data"], errors="coerce")
    mask = datas.dt.strftime("%Y-%m") == ano_mes_ref
    df_dinheiro_periodo = df_caixa_global[mask].copy()
else:
    df_dinheiro_periodo = pd.DataFrame(
        columns=["Data", "Descrição", "Tipo", "Valor"]
    )

# ========================
#  Cálculos principais
# ========================

dados_carregados = False
mensagem_erro = None

entradas_totais = saidas_totais = resultado_consolidado = 0.0
saldo_final = 0.0
ent_itau = sai_itau = res_itau = 0.0
ent_pag = sai_pag = res_pag = 0.0
df_mov = pd.DataFrame()
df_cat_export = pd.DataFrame()
df_resumo_contas = pd.DataFrame()
df_consolidado = pd.DataFrame()
excel_buffer = None

# Totais de dinheiro para fechamento (serão calculados com base nas datas dos extratos)
entradas_dinheiro_periodo = 0.0
saidas_dinheiro_periodo = 0.0
saldo_dinheiro_periodo = 0.0

if arquivo_itau and arquivo_pag:
    try:
        saldo_inicial = parse_numero_br(saldo_inicial_input)
    except Exception:
        mensagem_erro = "Saldo inicial inválido. Use formato 1234,56 ou 1234.56."
    else:
        try:
            REGRAS_CATEGORIA = carregar_regras()

            # Carrega extratos
            ent_itau, sai_itau, res_itau, mov_itau = carregar_extrato_itau_upload(
                arquivo_itau
            )
            ent_pag, sai_pag, res_pag, mov_pag = carregar_extrato_pagseguro_upload(
                arquivo_pag
            )

            # ----------------------------------------
            # Descobre os meses presentes nos extratos
            # ----------------------------------------
            movimentos_extratos = mov_itau + mov_pag

            meses_extratos = set()
            datas_extratos = []
            for mov in movimentos_extratos:
                d = mov.get("data")
                if not d:
                    continue
                dt = pd.to_datetime(d, dayfirst=True, errors="coerce")
                if pd.isna(dt):
                    continue
                datas_extratos.append(dt)
                meses_extratos.add(dt.strftime("%Y-%m"))

            if datas_extratos:
                meses_extratos = sorted(meses_extratos)
            else:
                meses_extratos = []

            # Filtra o livro-caixa global de dinheiro pelos mesmos meses
            if not df_caixa_global.empty and meses_extratos:
                datas_cash = pd.to_datetime(df_caixa_global["Data"], errors="coerce")
                mask_cash = datas_cash.dt.strftime("%Y-%m").isin(meses_extratos)
                df_dinheiro_periodo_fechar = df_caixa_global[mask_cash].copy()
            else:
                df_dinheiro_periodo_fechar = pd.DataFrame(
                    columns=["Data", "Descrição", "Tipo", "Valor"]
                )

            # Totais de dinheiro para o(s) mesmo(s) mês(es) dos extratos
            df_din_validos_calc = df_dinheiro_periodo_fechar.copy()
            if not df_din_validos_calc.empty and "Valor" in df_din_validos_calc.columns:
                df_din_validos_calc = df_din_validos_calc[
                    df_din_validos_calc["Valor"] > 0
                ]

            entradas_dinheiro_periodo = df_din_validos_calc.loc[
                df_din_validos_calc["Tipo"] == "Entrada", "Valor"
            ].sum()
            saidas_dinheiro_periodo = df_din_validos_calc.loc[
                df_din_validos_calc["Tipo"] == "Saída", "Valor"
            ].sum()
            saldo_dinheiro_periodo = entradas_dinheiro_periodo - saidas_dinheiro_periodo

            # ----------------------------------------
            # Consolidado: Itaú + PagSeguro + Dinheiro
            # ----------------------------------------
            entradas_totais = ent_itau + ent_pag + entradas_dinheiro_periodo
            # lembre: saídas do banco já vêm negativas; dinheiro (Saída) foi somado positivo em saidas_dinheiro_periodo
            saidas_totais = sai_itau + sai_pag - saidas_dinheiro_periodo
            resultado_consolidado = entradas_totais + saidas_totais
            saldo_final = saldo_inicial + resultado_consolidado

            # ----------------------------------------
            # Monta movimentos (Itaú + PagSeguro + Dinheiro)
            # ----------------------------------------
            movimentos = mov_itau + mov_pag

            if not df_din_validos_calc.empty:
                for _, linha in df_din_validos_calc.iterrows():
                    valor = float(linha.get("Valor", 0.0) or 0.0)
                    tipo = str(linha.get("Tipo", "Entrada"))
                    if tipo == "Saída":
                        valor = -valor
                    movimentos.append(
                        {
                            "data": linha.get("Data"),
                            "descricao": linha.get("Descrição"),
                            "valor": valor,
                            "conta": "Dinheiro",
                        }
                    )

            movimentos_cat = []
            for mov in movimentos:
                cat = classificar_categoria(mov)
                v = mov.get("valor", 0.0)
                movimentos_cat.append(
                    {
                        "Data": mov.get("data"),
                        "Conta": mov.get("conta"),
                        "Descrição": mov.get("descricao"),
                        "Categoria": cat,
                        "Valor": v,
                    }
                )

            df_mov = pd.DataFrame(movimentos_cat)

            entradas_cat = defaultdict(float)
            saidas_cat = defaultdict(float)
            for _, row in df_mov.iterrows():
                cat = row["Categoria"]
                v = row["Valor"]
                if v > 0:
                    entradas_cat[cat] += v
                elif v < 0:
                    saidas_cat[cat] += v

            categorias_calc = sorted(
                set(list(entradas_cat.keys()) + list(saidas_cat.keys()))
            )
            dados_cat = []
            for cat in categorias_calc:
                dados_cat.append(
                    {
                        "Categoria": cat,
                        "Entradas": entradas_cat.get(cat, 0.0),
                        "Saídas": saidas_cat.get(cat, 0.0),
                    }
                )

            df_cat_export = pd.DataFrame(dados_cat)

            df_resumo_contas = pd.DataFrame(
                [
                    {
                        "Conta": "Itaú",
                        "Entradas": ent_itau,
                        "Saídas": sai_itau,
                        "Resultado": res_itau,
                    },
                    {
                        "Conta": "PagSeguro",
                        "Entradas": ent_pag,
                        "Saídas": sai_pag,
                        "Resultado": res_pag,
                    },
                    {
                        "Conta": "Dinheiro",
                        "Entradas": entradas_dinheiro_periodo,
                        "Saídas": -saidas_dinheiro_periodo,
                        "Resultado": saldo_dinheiro_periodo,
                    },
                ]
            )
            df_consolidado = pd.DataFrame(
                [
                    {
                        "Nome do período": nome_periodo,
                        "Entradas totais": entradas_totais,
                        "Saídas totais": saidas_totais,
                        "Resultado do período": resultado_consolidado,
                        "Saldo inicial": saldo_inicial,
                        "Saldo final": saldo_final,
                    }
                ]
            )

            # ----------------------------------------
            # Excel (Resumo, Categorias, Movimentos, Dinheiro)
            # ----------------------------------------
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                start_row_resumo = 3
                df_resumo_contas.to_excel(
                    writer, sheet_name="Resumo", index=False, startrow=start_row_resumo
                )

                start_row_consol = start_row_resumo + len(df_resumo_contas) + 3
                df_consolidado.to_excel(
                    writer, sheet_name="Resumo", index=False, startrow=start_row_consol
                )

                # Aba técnica
                df_consolidado.to_excel(writer, sheet_name="ResumoDados", index=False)

                # Categorias
                df_cat_export.to_excel(
                    writer, sheet_name="Categorias", index=False, startrow=1
                )

                # Movimentos
                df_mov.to_excel(writer, sheet_name="Movimentos", index=False, startrow=1)

                # Aba Dinheiro (somente meses dos extratos)
                df_dinheiro_periodo_fechar.to_excel(
                    writer, sheet_name="Dinheiro", index=False, startrow=1
                )

                wb = writer.book
                ws_res = writer.sheets["Resumo"]
                ws_cat = writer.sheets["Categorias"]
                ws_mov = writer.sheets["Movimentos"]
                ws_din = writer.sheets["Dinheiro"]

                titulo = f"Fechamento Tempero das Gurias - {nome_periodo}"
                ws_res["A1"] = titulo
                ws_res["A1"].font = Font(bold=True, size=14)
                ws_res["A1"].alignment = Alignment(horizontal="left")

                formatar_tabela_excel(ws_res, df_resumo_contas, start_row=start_row_resumo)
                formatar_tabela_excel(ws_res, df_consolidado, start_row=start_row_consol)
                if not df_cat_export.empty:
                    formatar_tabela_excel(ws_cat, df_cat_export, start_row=1)
                if not df_mov.empty:
                    formatar_tabela_excel(ws_mov, df_mov, start_row=1)
                if not df_dinheiro_periodo_fechar.empty:
                    formatar_tabela_excel(ws_din, df_dinheiro_periodo_fechar, start_row=1)

            buffer.seek(0)
            excel_buffer = buffer

            dados_carregados = True

        except RuntimeError as e:
            mensagem_erro = str(e)


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
        st.write("Entradas em dinheiro no período:", format_currency(entradas_d))
    with col_c2:
        st.write(
            "Saídas em dinheiro no período:",
            format_currency(-saidas_d) if saidas_d else "R$ 0,00",
        )
    with col_c3:
        st.write("Saldo do dinheiro no período:", format_currency(saldo_d))


# ---------- ABA 2: Fechamento Mensal ----------

with tab2:
    require_role("admin")  # só admin (ricardo, lizi)

    st.markdown(
        '<div class="tempero-section-title">Resumo do período</div>',
        unsafe_allow_html=True,
    )

    if mensagem_erro:
        st.error(mensagem_erro)
    elif not dados_carregados:
        st.info(
            "Envie os arquivos do Itaú e PagSeguro na barra lateral para ver o fechamento."
        )
    else:
        m1, m2, m3 = st.columns(3)
        with m1:
            st.markdown(
                f"""
                <div class="tempero-metric-card">
                  <div class="tempero-metric-label">Entradas totais</div>
                  <div class="tempero-metric-value">{format_currency(entradas_totais)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with m2:
            st.markdown(
                f"""
                <div class="tempero-metric-card">
                  <div class="tempero-metric-label">Saídas totais</div>
                  <div class="tempero-metric-value">{format_currency(saidas_totais)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        with m3:
            st.markdown(
                f"""
                <div class="tempero-metric-card">
                  <div class="tempero-metric-label">Resultado do período</div>
                  <div class="tempero-metric-value">{format_currency(resultado_consolidado)}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )

        st.markdown("---")

        # Resumo por conta
        st.markdown(
            '<div class="tempero-section-title">📑 Resumo por conta</div>',
            unsafe_allow_html=True,
        )
        with st.container():
            col_a, col_b, col_c = st.columns(3)
            with col_a:
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.markdown("**Itaú**")
                st.write("Entradas:", format_currency(ent_itau))
                st.write("Saídas  :", format_currency(sai_itau))
                st.write("Resultado:", format_currency(res_itau))
                st.markdown("</div>", unsafe_allow_html=True)

            with col_b:
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.markdown("**PagSeguro**")
                st.write("Entradas:", format_currency(ent_pag))
                st.write("Saídas  :", format_currency(sai_pag))
                st.write("Resultado:", format_currency(res_pag))
                st.markdown("</div>", unsafe_allow_html=True)

            with col_c:
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.markdown("**Dinheiro (caixa físico)**")
                st.write("Entradas:", format_currency(entradas_dinheiro_periodo))
                st.write(
                    "Saídas  :",
                    format_currency(-saidas_dinheiro_periodo)
                    if saidas_dinheiro_periodo
                    else "R$ 0,00",
                )
                st.write("Resultado:", format_currency(saldo_dinheiro_periodo))
                st.caption("Edite os lançamentos na aba 💵 Caixa Diário.")
                st.markdown("</div>", unsafe_allow_html=True)

        st.markdown("---")

        # Consolidado
        st.markdown(
            '<div class="tempero-section-title">🏁 Consolidado da loja</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
        st.write("Saldo inicial:", format_currency(saldo_inicial))
        st.write("Saldo final  :", format_currency(saldo_final))
        st.markdown("</div>", unsafe_allow_html=True)

        # Resumo por categoria
        st.markdown(
            '<div class="tempero-section-title">📌 Resumo por categoria</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            '<div class="tempero-section-sub">Baseado nas categorias atuais (já considera regras salvas anteriormente).</div>',
            unsafe_allow_html=True,
        )
        df_cat_display = df_cat_export.copy()
        if not df_cat_display.empty:
            df_cat_display["Entradas"] = df_cat_display["Entradas"].map(format_currency)
            df_cat_display["Saídas"] = df_cat_display["Saídas"].map(format_currency)
        st.dataframe(df_cat_display, use_container_width=True)

        # Relatório
        st.markdown(
            '<div class="tempero-section-title">📥 Relatório do período atual</div>',
            unsafe_allow_html=True,
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
            slug = slugify(nome_periodo)
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
    require_role("admin")  # só admin (ricardo, lizi)

    st.markdown(
        '<div class="tempero-section-title">🧾 Conferência de lançamentos e categorias</div>',
        unsafe_allow_html=True,
    )

    if not dados_carregados:
        st.info(
            "Envie os arquivos do Itaú e PagSeguro na barra lateral para conferir as categorias."
        )
    else:
        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
        st.markdown("**Gerenciar categorias**")

        categorias_padrao = [
            "Vendas / Receitas",
            "Fornecedores e Insumos",
            "Folha de Pagamento",
            "Aluguel Comercial",
            "Contabilidade e RH",
            "Dedetização / Controle de Pragas",
            "Energia Elétrica",
            "Motoboy / Entregas",
            "Nutricionista",
            "Impostos e Encargos",
            "Investimentos (Aplicações)",
            "Rendimentos de Aplicações",
            "Fatura Cartão",
            "Transferência Interna / Sócios",
            "A Classificar",
        ]

        categorias_custom = carregar_categorias_personalizadas()
        categorias_possiveis = categorias_padrao + categorias_custom

        col_nc1, col_nc2 = st.columns([2, 1])
        with col_nc1:
            nova_cat = st.text_input("Criar nova categoria:")
        with col_nc2:
            if st.button("Adicionar categoria"):
                if nova_cat.strip() != "":
                    if nova_cat not in categorias_possiveis:
                        categorias_custom.append(nova_cat)
                        salvar_categorias_personalizadas(categorias_custom)
                        st.success(f"Categoria '{nova_cat}' criada com sucesso!")
                        st.rerun()
                    else:
                        st.warning("Essa categoria já existe.")

        st.markdown("</div>", unsafe_allow_html=True)

        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
        st.markdown("**Conferência de lançamentos**")
        st.markdown(
            '<div class="tempero-section-sub">Ajuste as categorias linha a linha, se necessário. '
            "Ao salvar as regras, o sistema aprende para os próximos fechamentos.</div>",
            unsafe_allow_html=True,
        )

        edited_df = st.data_editor(
            df_mov,
            key="editor_movimentos",
            use_container_width=True,
            num_rows="fixed",
            column_config={
                "Categoria": st.column_config.SelectboxColumn(
                    "Categoria",
                    options=categorias_possiveis,
                    help="Ajuste a categoria se necessário.",
                )
            },
        )

        if st.button("Salvar regras de categorização"):
            regras = carregar_regras()
            alteracoes = 0
            for _, row in edited_df.iterrows():
                desc = row.get("Descrição")
                cat = row.get("Categoria")
                if not desc or not cat:
                    continue
                desc_norm = normalizar_texto(desc)
                if regras.get(desc_norm) != cat:
                    regras[desc_norm] = cat
                    alteracoes += 1
            salvar_regras(regras)
            st.success(
                f"{alteracoes} regra(s) de categorização salva(s). "
                "Os próximos fechamentos já virão com essas categorias aplicadas."
            )
            st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)


# ---------- ABA 4: Histórico & Comparativos ----------

with tab4:
    require_role("admin")  # só admin (ricardo, lizi)
    st.markdown(
        '<div class="tempero-section-title">📊 Histórico de fechamentos e comparativo</div>',
        unsafe_allow_html=True,
    )

    try:
        arquivos = list_history_from_gdrive()
    except Exception as e:
        st.error(f"Erro ao acessar Google Drive: {e}")
        arquivos = []

    if not arquivos:
        st.write("Nenhum fechamento salvo ainda.")
    else:
        st.markdown("**Comparativo entre períodos (Histórico Analítico)**")
        st.markdown(
            '<div class="tempero-section-sub">Baseado nos relatórios salvos no histórico (Google Drive).</div>',
            unsafe_allow_html=True,
        )

        resumos = []
        for file_info in arquivos:
            file_id = file_info["id"]
            nome = file_info["name"]

            try:
                buf = download_history_file(file_id)

                try:
                    df_consol = pd.read_excel(buf, sheet_name="ResumoDados")
                except Exception:
                    buf.seek(0)
                    df_res = pd.read_excel(buf, sheet_name="Resumo")
                    if "Nome do período" not in df_res.columns:
                        continue
                    df_consol = df_res[df_res["Nome do período"].notna()]
                    if df_consol.empty:
                        continue

                linha = df_consol.iloc[0]
                periodo = str(linha.get("Nome do período", nome))
                entradas = float(linha.get("Entradas totais", 0.0))
                saidas = float(linha.get("Saídas totais", 0.0))
                resultado = float(linha.get("Resultado do período", 0.0))
                saldo_final_val = linha.get("Saldo final", None)
                saldo_final_hist = (
                    float(saldo_final_val) if saldo_final_val is not None else None
                )

                resumos.append(
                    {
                        "Período": periodo,
                        "Entradas": entradas,
                        "Saídas": saidas,
                        "Resultado": resultado,
                        "Saldo final": saldo_final_hist,
                    }
                )
            except Exception:
                continue

        if not resumos:
            st.info(
                "Ainda não foi possível montar o comparativo. "
                "Gere e salve alguns fechamentos no novo formato."
            )
        else:
            df_hist = pd.DataFrame(resumos)
            df_hist = df_hist.iloc[::-1].reset_index(drop=True)

            df_display = df_hist.copy()
            for col in ["Entradas", "Saídas", "Resultado", "Saldo final"]:
                if col in df_display.columns:
                    df_display[col] = df_display[col].apply(
                        lambda x: format_currency(x) if pd.notna(x) else "-"
                    )

            st.dataframe(df_display, use_container_width=True)

            st.markdown("**Resultado por período:**")
            chart_df = df_hist.set_index("Período")[["Resultado"]]
            st.bar_chart(chart_df)

        st.markdown("---")

        st.markdown("**Fechamentos salvos**")
        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)

        for file_info in arquivos:
            file_id = file_info["id"]
            nome = file_info["name"]
            mod_raw = file_info.get("modifiedTime")

            try:
                dt = datetime.fromisoformat(mod_raw.replace("Z", "+00:00"))
                data_mod = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                data_mod = mod_raw

            col_a, col_b, col_c = st.columns([5, 1, 1])

            with col_a:
                st.write(f"📄 **{nome}**")
                st.caption(f"salvo em {data_mod}")

            with col_b:
                try:
                    buf = download_history_file(file_id)
                    data_bin = buf.getvalue()
                    st.download_button(
                        label="Baixar",
                        data=data_bin,
                        file_name=nome,
                        mime=(
                            "application/vnd.openxmlformats-officedocument."
                            "spreadsheetml.sheet"
                        ),
                        key=f"baixar_{file_id}",
                    )
                except Exception as e:
                    st.error(f"Erro ao baixar {nome}: {e}")

            with col_c:
                if st.button("Excluir", key=f"excluir_{file_id}"):
                    try:
                        delete_history_file(file_id)
                        st.success(f"Arquivo **{nome}** excluído com sucesso!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro ao excluir {nome}: {e}")

        st.markdown("</div>", unsafe_allow_html=True)
