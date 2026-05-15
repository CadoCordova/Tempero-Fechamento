import json
from io import BytesIO

import pandas as pd
import streamlit as st
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload


# ---------------------------------------------------------------------------
# Serviço / autenticação
# ---------------------------------------------------------------------------

def get_gdrive_service():
    """
    Cria o cliente da API do Google Drive usando OAuth (token em st.secrets["gdrive_oauth"]).
    Faz refresh explícito do token e trata erros de autenticação (invalid_grant).
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
        return build("drive", "v3", credentials=creds)

    except RefreshError as e:
        if "invalid_grant" in str(e):
            st.error(
                "Erro de autenticação com o Google Drive: o token foi expirado ou revogado.\n\n"
                "Gere um novo arquivo token.json (rodando gerar_token.py) e atualize "
                "a seção [gdrive_oauth] do secrets do Streamlit."
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


# ---------------------------------------------------------------------------
# Pasta de históricos
# ---------------------------------------------------------------------------

def get_history_folder_id(service) -> str:
    """Obtém (ou cria) a pasta de históricos no Google Drive."""
    if "gdrive_history_folder_id" in st.session_state:
        return st.session_state["gdrive_history_folder_id"]

    folder_name = st.secrets.get("GDRIVE_FOLDER_NAME", "Tempero_Fechamentos")
    query = (
        f"mimeType = 'application/vnd.google-apps.folder' "
        f"and name = '{folder_name}' and trashed = false"
    )
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", pageSize=10)
        .execute()
    )
    files = results.get("files", [])
    if files:
        folder_id = files[0]["id"]
    else:
        metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        folder = service.files().create(body=metadata, fields="id").execute()
        folder_id = folder["id"]

    st.session_state["gdrive_history_folder_id"] = folder_id
    return folder_id


def _find_file_in_folder(service, folder_id: str, filename: str) -> str | None:
    query = f"'{folder_id}' in parents and trashed = false and name = '{filename}'"
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", pageSize=5)
        .execute()
    )
    files = results.get("files", [])
    return files[0]["id"] if files else None


# ---------------------------------------------------------------------------
# JSON no Drive
# ---------------------------------------------------------------------------

def load_json_from_gdrive_history(filename: str):
    """Carrega um JSON (por nome) da pasta de históricos."""
    try:
        service = get_gdrive_service()
        folder_id = get_history_folder_id(service)
        file_id = _find_file_in_folder(service, folder_id, filename)
        if not file_id:
            return None

        request = service.files().get_media(fileId=file_id)
        fh = BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        fh.seek(0)
        return json.load(fh)
    except Exception:
        return None


def save_json_to_gdrive_history(filename: str, payload):
    """Salva/atualiza um JSON (por nome) na pasta de históricos."""
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)

    data_bytes = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    media = MediaIoBaseUpload(BytesIO(data_bytes), mimetype="application/json", resumable=False)

    file_id = _find_file_in_folder(service, folder_id, filename)
    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        metadata = {"name": filename, "parents": [folder_id], "mimeType": "application/json"}
        service.files().create(body=metadata, media_body=media, fields="id").execute()


# ---------------------------------------------------------------------------
# Arquivos Excel no histórico
# ---------------------------------------------------------------------------

def upload_history_to_gdrive(buffer: BytesIO, filename: str) -> str:
    """
    Envia um arquivo Excel para a pasta de históricos.
    Se já existir um arquivo com o mesmo nome, atualiza em vez de criar duplicata.
    """
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)
    buffer.seek(0)

    media = MediaIoBaseUpload(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )

    existing_id = _find_file_in_folder(service, folder_id, filename)
    if existing_id:
        file = service.files().update(fileId=existing_id, media_body=media).execute()
        return file["id"]
    else:
        metadata = {"name": filename, "parents": [folder_id]}
        file = service.files().create(body=metadata, media_body=media, fields="id, name").execute()
        return file["id"]


def list_history_from_gdrive() -> list[dict]:
    """Lista arquivos salvos na pasta de históricos (id, name, modifiedTime)."""
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)

    all_files = []
    page_token = None
    while True:
        kwargs = dict(
            q=f"'{folder_id}' in parents and trashed = false",
            spaces="drive",
            fields="nextPageToken, files(id, name, modifiedTime)",
            orderBy="modifiedTime desc",
            pageSize=100,
        )
        if page_token:
            kwargs["pageToken"] = page_token

        results = service.files().list(**kwargs).execute()
        all_files.extend(results.get("files", []))
        page_token = results.get("nextPageToken")
        if not page_token:
            break

    return all_files


def download_history_file(file_id: str) -> BytesIO:
    """Faz download de um arquivo do histórico e retorna BytesIO."""
    service = get_gdrive_service()
    request = service.files().get_media(fileId=file_id)
    buf = BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf


def delete_history_file(file_id: str):
    """Exclui um arquivo do histórico."""
    service = get_gdrive_service()
    service.files().delete(fileId=file_id).execute()


# ---------------------------------------------------------------------------
# Leitura de relatórios de fechamento
# ---------------------------------------------------------------------------

def _read_excel_sheet_safe(buf: BytesIO, sheet_name: str, header: int | None = 0) -> pd.DataFrame:
    try:
        buf.seek(0)
        return pd.read_excel(buf, sheet_name=sheet_name, header=header)
    except Exception:
        return pd.DataFrame()


def load_fechamento_report_from_gdrive(file_id: str) -> dict:
    """
    Carrega um relatório de fechamento salvo (fechamento_tempero_*.xlsx).
    Retorna dict com: consolidado, resumo_contas, categorias, movimentos, dinheiro.
    """
    buf = download_history_file(file_id)

    df_consol = _read_excel_sheet_safe(buf, "ResumoDados", header=0)

    df_resumo_raw = _read_excel_sheet_safe(buf, "Resumo", header=3)
    if not df_resumo_raw.empty and "Conta" in df_resumo_raw.columns:
        df_resumo_contas = df_resumo_raw[
            df_resumo_raw["Conta"].isin(["Itaú", "PagSeguro", "Dinheiro"])
        ].copy()
    else:
        df_resumo_contas = pd.DataFrame()

    df_cat = _read_excel_sheet_safe(buf, "Categorias", header=1)
    df_mov = _read_excel_sheet_safe(buf, "Movimentos", header=1)
    df_din = _read_excel_sheet_safe(buf, "Dinheiro", header=1)

    for df in (df_consol, df_resumo_contas, df_cat, df_mov, df_din):
        if not df.empty:
            df.columns = [str(c).strip() for c in df.columns]

    return {
        "consolidado": df_consol,
        "resumo_contas": df_resumo_contas,
        "categorias": df_cat,
        "movimentos": df_mov,
        "dinheiro": df_din,
    }


def list_fechamentos_history_files(arquivos: list[dict]) -> list[dict]:
    """Filtra somente relatórios de fechamento (fechamento_tempero_*.xlsx)."""
    return [f for f in (arquivos or []) if str(f.get("name", "")).startswith("fechamento_tempero_")]
