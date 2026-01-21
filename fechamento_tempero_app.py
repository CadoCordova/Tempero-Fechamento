import math
import json
import logging
from collections import defaultdict
from pathlib import Path
from io import BytesIO
from datetime import datetime

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
#  Configuração de Logs
# ========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# ========================
#  Configurações e paths
# ========================

RULES_PATH = Path("regras_categorias.json")
CATEGORIAS_PATH = Path("categorias_personalizadas.json")

PRIMARY_COLOR = "#F06BAA"     # rosa médio
BACKGROUND_SOFT = "#FDF2F7"   # rosinha de fundo
TEXT_DARK = "#333333"

# Dicionário global de regras (carregado em runtime)
REGRAS_CATEGORIA = {}

# ========================
#  Estilo (CSS)
# ========================

def inject_css():
    st.markdown(
        f"""
        <style>
        .block-container {{ max-width: 1200px; padding-top: 3.5rem; padding-bottom: 2.5rem; }}
        body {{ background-color: {BACKGROUND_SOFT}; }}
        .tempero-title {{ font-size: 1.8rem; font-weight: 800; color: {PRIMARY_COLOR}; margin-bottom: 0.3rem; text-align: center; }}
        .tempero-subtitle {{ font-size: 0.95rem; color: #666666; margin-bottom: 1.2rem; text-align: center; }}
        .tempero-card {{ background-color: #FFFFFF; padding: 1.1rem 1.3rem; border-radius: 0.8rem; box-shadow: 0 2px 6px rgba(0,0,0,0.05); margin-bottom: 0.8rem; }}
        .tempero-metric-card {{ background: linear-gradient(135deg, {PRIMARY_COLOR}, #e04592); color: white !important; padding: 0.9rem 1.1rem; border-radius: 0.8rem; box-shadow: 0 2px 8px rgba(0,0,0,0.18); }}
        .tempero-metric-label {{ font-size: 0.85rem; opacity: 0.9; }}
        .tempero-metric-value {{ font-size: 1.4rem; font-weight: 700; }}
        .tempero-section-title {{ font-weight: 700; color: {TEXT_DARK}; margin-bottom: 0.4rem; }}
        .tempero-section-sub {{ font-size: 0.85rem; color: #777777; margin-bottom: 0.6rem; }}
        .stTabs [role="tab"] {{ padding: 0.6rem 1rem; border-radius: 999px; color: #555 !important; }}
        .stTabs [role="tab"][aria-selected="true"] {{ background-color: {PRIMARY_COLOR}20 !important; color: {PRIMARY_COLOR} !important; border-bottom-color: transparent !important; }}
        </style>
        """,
        unsafe_allow_html=True,
    )

# ========================
#  Formatação Excel
# ========================

def formatar_tabela_excel(ws, df, start_row=1):
    header_row = start_row
    n_rows = len(df)
    n_cols = len(df.columns)

    for col_idx in range(1, n_cols + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = ws[f"A{header_row + 1}"]

    for col_idx, _ in enumerate(df.columns, start=1):
        max_len = 0
        for row_idx in range(header_row, header_row + 1 + n_rows):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    col_names_lower = [str(c).lower() for c in df.columns]
    for col_idx, col_name in enumerate(col_names_lower, start=1):
        if any(prefix in col_name for prefix in ("entradas", "saídas", "saidas", "resultado", "saldo", "valor")):
            for row_idx in range(header_row + 1, header_row + 1 + n_rows):
                cell = ws.cell(row=row_idx, column=col_idx)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '"R$" #,##0.00'

# ========================
#  Google Drive (OAuth)
# ========================

def get_gdrive_service():
    info = st.secrets["gdrive_oauth"]
    creds = Credentials(
        token=info.get("token"),
        refresh_token=info.get("refresh_token"),
        token_uri=info.get("token_uri"),
        client_id=info.get("client_id"),
        client_secret=info.get("client_secret"),
        scopes=info.get("scopes", ["https://www.googleapis.com/auth/drive"]),
    )
    try:
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
        return build("drive", "v3", credentials=creds)
    except RefreshError as e:
        logger.error(f"Erro de autenticação no Drive: {e}")
        st.error("Token do Google Drive expirado. Re-gere o token.")
        st.stop()
    except Exception as e:
        logger.error(f"Erro inesperado no Drive: {e}")
        st.stop()

def get_history_folder_id(service):
    if "gdrive_history_folder_id" in st.session_state:
        return st.session_state["gdrive_history_folder_id"]
    folder_name = st.secrets.get("GDRIVE_FOLDER_NAME", "Tempero_Fechamentos")
    query = f"mimeType = 'application/vnd.google-apps.folder' and name = '{folder_name}' and trashed = false"
    results = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
    files = results.get("files", [])
    if files:
        folder_id = files[0]["id"]
    else:
        file_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = service.files().create(body=file_metadata, fields="id").execute()
        folder_id = folder["id"]
    st.session_state["gdrive_history_folder_id"] = folder_id
    return folder_id

# ========================
#  Sincronização de JSONs no Drive
# ========================

def _load_json_from_drive(filename, local_path, default_factory):
    """Lógica genérica para carregar JSON do Drive com fallback local."""
    try:
        service = get_gdrive_service()
        folder_id = get_history_folder_id(service)
        query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            request = service.files().get_media(fileId=files[0]["id"])
            fh = BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
            data = json.loads(fh.getvalue().decode("utf-8"))
            logger.info(f"Arquivo {filename} carregado do Drive.")
            return data
    except Exception as e:
        logger.warning(f"Erro ao carregar {filename} do Drive: {e}. Tentando local...")
    
    if local_path.exists():
        try:
            with local_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except: pass
    return default_factory()

def _save_json_to_drive(data, filename, local_path):
    """Lógica genérica para salvar JSON localmente e no Drive."""
    with local_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    try:
        service = get_gdrive_service()
        folder_id = get_history_folder_id(service)
        buffer = BytesIO(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))
        media = MediaIoBaseUpload(buffer, mimetype="application/json")
        query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
        results = service.files().list(q=query, fields="files(id)").execute()
        files = results.get("files", [])
        if files:
            service.files().update(fileId=files[0]["id"], media_body=media).execute()
        else:
            file_metadata = {"name": filename, "parents": [folder_id]}
            service.files().create(body=file_metadata, media_body=media).execute()
        logger.info(f"Arquivo {filename} sincronizado no Drive.")
    except Exception as e:
        logger.error(f"Falha ao sincronizar {filename} no Drive: {e}")

def carregar_regras(): return _load_json_from_drive("regras_categorias.json", RULES_PATH, dict)
def salvar_regras(regras): _save_json_to_drive(regras, "regras_categorias.json", RULES_PATH)
def carregar_categorias_personalizadas(): return _load_json_from_drive("categorias_personalizadas.json", CATEGORIAS_PATH, list)
def salvar_categorias_personalizadas(lista): _save_json_to_drive(lista, "categorias_personalizadas.json", CATEGORIAS_PATH)

# ========================
#  Funções de Negócio (Parse, Limpeza, Upload)
# ========================

def parse_numero_br(valor):
    if pd.isna(valor) or valor in (None, "", "-"): return 0.0
    if isinstance(valor, (int, float)): return float(valor)
    s = str(valor).replace("R$", "").strip()
    try:
        if "." in s and "," in s: s = s.replace(".", "").replace(",", ".")
        elif "," in s: s = s.replace(",", ".")
        return float(s)
    except: return 0.0

def normalizar_texto(txt):
    if txt is None: return ""
    s = str(txt).upper()
    replacements = [("Ó","O"),("Ô","O"),("Õ","O"),("Í","I"),("Á","A"),("À","A"),("Ã","A"),("É","E"),("Ê","E"),("Ú","U"),("Ç","C")]
    for ac, sem in replacements: s = s.replace(ac, sem)
    return s

def extrair_descricao_linha(linha):
    if linha.get("descricao"): return linha["descricao"]
    partes = []
    for k, v in linha.items():
        if not v or not isinstance(k, str): continue
        kl = normalizar_texto(k)
        if "HIST" in kl or "DESCR" in kl: partes.append(str(v).strip())
    return " | ".join(partes) if partes else "Sem Descrição"

def ler_arquivo_tabela_upload(uploaded_file):
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix in (".csv", ".txt"):
        df = pd.read_csv(uploaded_file, sep=";")
    else:
        raw = pd.read_excel(uploaded_file, header=None)
        header_idx = 0
        for i, row in raw.iterrows():
            valores = [str(x).upper() for x in row.tolist() if not pd.isna(x)]
            if "DATA" in valores and any(c in valores for c in ["LANÇAMENTO", "DESCRIÇÃO", "VALOR"]):
                header_idx = i
                break
        df = raw.iloc[header_idx + 1:].copy()
        df.columns = [str(c).strip() for c in raw.iloc[header_idx]]
        df = df.dropna(how="all").reset_index(drop=True)
    return df.to_dict(orient="records")

def carregar_extrato_itau_upload(file):
    linhas = ler_arquivo_tabela_upload(file)
    movs = []
    ent = sai = 0.0
    for l in linhas:
        desc = extrair_descricao_linha(l)
        if any(x in normalizar_texto(desc) for x in ["SALDO ANTERIOR", "SALDO DO DIA"]): continue
        v = parse_numero_br(l.get("Valor") or l.get("VALOR") or 0)
        if v == 0:
            v = parse_numero_br(l.get("CREDITO", 0)) - parse_numero_br(l.get("DEBITO", 0))
        if v > 0: ent += v
        else: sai += v
        movs.append({"data": l.get("Data") or l.get("DATA"), "descricao": desc, "valor": v, "conta": "Itau"})
    return ent, sai, ent+sai, movs

def carregar_extrato_pagseguro_upload(file):
    linhas = ler_arquivo_tabela_upload(file)
    movs = []
    ent = sai = 0.0
    for l in linhas:
        desc = extrair_descricao_linha(l)
        e = abs(parse_numero_br(l.get("Entradas") or l.get("ENTRADAS") or 0))
        s = abs(parse_numero_br(l.get("Saidas") or l.get("SAIDAS") or 0))
        v = e - s
        ent += e; sai -= s
        movs.append({"data": l.get("Data") or l.get("DATA"), "descricao": desc, "valor": v, "conta": "PagSeguro"})
    return ent, -sai, ent-sai, movs

# ========================
#  Lógica de Categorização
# ========================

def classificar_categoria(mov, regras):
    desc_norm = normalizar_texto(mov.get("descricao"))
    for padrao, cat in regras.items():
        if padrao in desc_norm: return cat
    
    # Regras Hardcoded
    if "CEEE" in desc_norm or "ENERGIA" in desc_norm: return "Energia Elétrica"
    if "RECH CONTABILIDADE" in desc_norm: return "Contabilidade e RH"
    if any(x in desc_norm for x in ["RICARDO", "LIZI"]): return "Transferência Interna / Sócios"
    
    return "Vendas / Receitas" if mov.get("valor", 0) > 0 else "Fornecedores e Insumos"

# ========================
#  Livro-caixa no Drive
# ========================

def load_cash_from_gdrive(periodo_ref):
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)
    filename = f"caixa_dinheiro_{periodo_ref}.xlsx"
    query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
    res = service.files().list(q=query).execute().get("files", [])
    if not res: return pd.DataFrame(columns=["Data", "Descrição", "Tipo", "Valor"])
    request = service.files().get_media(fileId=res[0]["id"])
    fh = BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done: _, done = downloader.next_chunk()
    fh.seek(0)
    return pd.read_excel(fh)

def save_cash_to_gdrive(periodo_ref, df):
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)
    filename = f"caixa_dinheiro_{periodo_ref}.xlsx"
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False)
    buffer.seek(0)
    media = MediaIoBaseUpload(buffer, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
    res = service.files().list(q=query).execute().get("files", [])
    if res: service.files().update(fileId=res[0]["id"], media_body=media).execute()
    else: service.files().create(body={"name": filename, "parents": [folder_id]}, media_body=media).execute()

# ========================
#  Autenticação e Helpers UI
# ========================

def check_auth():
    if st.session_state.get("auth_ok"): return
    inject_css()
    st.markdown('<div class="tempero-title">Tempero das Gurias - Acesso</div>', unsafe_allow_html=True)
    u = st.text_input("Usuário:")
    p = st.text_input("Senha:", type="password")
    if st.button("Entrar"):
        users = st.secrets.get("auth_users", {})
        if u in users and p == users[u]["password"]:
            st.session_state.update({"auth_ok": True, "user": u, "role": users[u].get("role", "operador")})
            logger.info(f"Login: {u}")
            st.rerun()
        else: st.error("Incorreto.")
    st.stop()

def slugify(t): return t.strip().lower().replace(" ", "_")
def format_currency(v): return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# ========================
#  INTERFACE PRINCIPAL
# ========================

st.set_page_config(page_title="Fechamento Tempero", layout="wide")
inject_css()
check_auth()

# Barra Lateral
st.sidebar.header("Configuração")
f_itau = st.sidebar.file_uploader("Itaú", type=["csv", "xlsx"])
f_pag = st.sidebar.file_uploader("PagSeguro", type=["csv", "xlsx"])
s_ini = parse_numero_br(st.sidebar.text_input("Saldo Inicial", "0"))
n_per = st.sidebar.text_input("Nome do Período", datetime.today().strftime("%Y-%m"))

if st.sidebar.button("Sair"):
    st.session_state.clear()
    st.rerun()

# Abas
is_admin = st.session_state.get("role") == "admin"
tabs = st.tabs(["💵 Caixa Diário", "💗 Fechamento", "🧾 Categorias", "📊 Histórico"]) if is_admin else st.tabs(["💵 Caixa Diário"])

# --- TAB 1: CAIXA ---
with tabs[0]:
    ref_mes = n_per[:7]
    if "df_dinheiro" not in st.session_state:
        st.session_state.df_dinheiro = load_cash_from_gdrive(ref_mes)
    
    df_ed = st.data_editor(st.session_state.df_dinheiro, num_rows="dynamic", use_container_width=True)
    if st.button("Salvar Caixa no Drive"):
        save_cash_to_gdrive(ref_mes, df_ed)
        st.session_state.df_dinheiro = df_ed
        st.success("Salvo!")

# --- TAB 2, 3, 4: ADMIN ---
if is_admin and f_itau and f_pag:
    regras = carregar_regras()
    ent_i, sai_i, res_i, mov_i = carregar_extrato_itau_upload(f_itau)
    ent_p, sai_p, res_p, mov_p = carregar_extrato_pagseguro_upload(f_pag)
    
    # Dinheiro
    df_din = st.session_state.df_dinheiro
    ent_d = df_din[df_ed["Tipo"]=="Entrada"]["Valor"].sum()
    sai_d = df_din[df_ed["Tipo"]=="Saída"]["Valor"].sum()
    
    ent_t = ent_i + ent_p + ent_d
    sai_t = sai_i + sai_p - sai_d
    res_t = ent_t + sai_t
    
    with tabs[1]:
        c1, c2, c3 = st.columns(3)
        c1.metric("Entradas", format_currency(ent_t))
        c2.metric("Saídas", format_currency(sai_t))
        c3.metric("Resultado", format_currency(res_t))
        
        if st.button("Salvar Fechamento no Histórico"):
            logger.info(f"Fechamento {n_per} salvo por {st.session_state.user}")
            st.success("Histórico atualizado!")

    with tabs[2]:
        st.subheader("Regras de Categorização")
        # Mostra movimentos para conferência
        all_movs = mov_i + mov_p
        df_movs = pd.DataFrame(all_movs)
        df_movs["Categoria"] = df_movs.apply(lambda x: classificar_categoria(x, regras), axis=1)
        
        ed_regras = st.data_editor(df_movs, use_container_width=True)
        if st.button("Sincronizar Novas Regras"):
            for _, row in ed_regras.iterrows():
                regras[normalizar_texto(row['descricao'])] = row['Categoria']
            salvar_regras(regras)
            st.rerun()

with tabs[-1] if is_admin else st.empty():
    st.write("Histórico de arquivos no Drive...")
    # Aqui entraria a lista de arquivos list_history_from_gdrive()