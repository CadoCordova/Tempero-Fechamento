from io import BytesIO

import pandas as pd
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

from modules.gdrive import get_gdrive_service, get_history_folder_id


def get_cash_file_name(ano_mes_ref: str | None) -> str:
    if not ano_mes_ref:
        return "caixa_dinheiro_sem_periodo.xlsx"
    return f"caixa_dinheiro_{ano_mes_ref}.xlsx"


def _get_cash_file_id(service, folder_id: str, ano_mes_ref: str | None) -> str | None:
    filename = get_cash_file_name(ano_mes_ref)
    query = f"'{folder_id}' in parents and name = '{filename}' and trashed = false"
    results = (
        service.files()
        .list(q=query, spaces="drive", fields="files(id, name)", pageSize=10)
        .execute()
    )
    files = results.get("files", [])
    return files[0]["id"] if files else None


def load_cash_from_gdrive(ano_mes_ref: str | None) -> pd.DataFrame:
    """
    Lê o livro-caixa de dinheiro do mês (caixa_dinheiro_YYYY-MM.xlsx).
    Retorna DataFrame vazio com colunas padrão se o arquivo não existir.
    """
    _cols = ["Data", "Descrição", "Tipo", "Valor"]
    try:
        service = get_gdrive_service()
        folder_id = get_history_folder_id(service)
        file_id = _get_cash_file_id(service, folder_id, ano_mes_ref)

        if not file_id:
            return pd.DataFrame(columns=_cols)

        request = service.files().get_media(fileId=file_id)
        buf = BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        buf.seek(0)

        df = pd.read_excel(buf)
        df.columns = [str(c).strip() for c in df.columns]
        for col in _cols:
            if col not in df.columns:
                df[col] = None
        return df[_cols]
    except Exception as e:
        import streamlit as st
        st.warning(f"Não foi possível carregar o caixa do Google Drive: {e}")
        return pd.DataFrame(columns=_cols)


def save_cash_to_gdrive(ano_mes_ref: str | None, df: pd.DataFrame):
    """Salva (ou atualiza) o livro-caixa mensal do dinheiro no Drive."""
    service = get_gdrive_service()
    folder_id = get_history_folder_id(service)
    file_id = _get_cash_file_id(service, folder_id, ano_mes_ref)
    filename = get_cash_file_name(ano_mes_ref)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="CaixaDinheiro", index=False)
    buffer.seek(0)

    media = MediaIoBaseUpload(
        buffer,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        resumable=False,
    )

    if file_id:
        service.files().update(fileId=file_id, media_body=media).execute()
    else:
        metadata = {"name": filename, "parents": [folder_id]}
        service.files().create(body=metadata, media_body=media, fields="id").execute()
