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
        if "Data" in df.columns:
            df["Data"] = pd.to_datetime(df["Data"], dayfirst=True, errors="coerce")
        return df[_cols]
    except Exception as e:
        import streamlit as st
        st.warning(f"Não foi possível carregar o caixa do Google Drive: {e}")
        return pd.DataFrame(columns=_cols)


def _eh_linha_suitable(descricao) -> bool:
    """Linhas importadas do Gmail terminam com '(Suitable)'; as lançadas à mão, não."""
    return str(descricao or "").strip().endswith("(Suitable)")


def lancar_importados_gmail(ano_mes_ref: str | None, novos: list[dict]) -> tuple[int, int]:
    """
    Importa lançamentos do Gmail (Suitable) para o caixa do mês no Drive.

    Para cada dia que veio no email, as linhas "(Suitable)" já existentes nesse dia
    são SUBSTITUÍDAS pelas do email (o email é a fonte). Linhas lançadas à mão
    (sem "(Suitable)" na descrição, como "Saldo inicial") são preservadas, e dias
    que não vieram no email não são tocados.

    Isso evita dois problemas da regra antiga (Data + Descrição + Valor):
    gastos iguais no mesmo dia (2 × R$ 34 de água) eram descartados como duplicata,
    e uma linha editada à mão (217,50 em vez de 217,52) voltava duplicada.

    Retorna (qtd_inseridos, qtd_linhas_suitable_substituidas).
    """
    df_atual = load_cash_from_gdrive(ano_mes_ref)
    if not novos:
        return 0, 0

    def _dia(d) -> str:
        return d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)

    dias_novos = {_dia(item["Data"]) for item in novos}

    removidos = 0
    if not df_atual.empty:
        dia_atual = pd.to_datetime(df_atual["Data"], errors="coerce").dt.strftime("%Y-%m-%d")
        remover = dia_atual.isin(dias_novos) & df_atual["Descrição"].apply(_eh_linha_suitable)
        removidos = int(remover.sum())
        df_atual = df_atual[~remover]

    df_novos = pd.DataFrame(
        [
            {
                "Data": item["Data"],
                "Descrição": str(item.get("Descrição", "")).strip(),
                "Tipo": item.get("Tipo", "Entrada"),
                "Valor": float(item.get("Valor", 0.0)),
            }
            for item in novos
        ],
        columns=["Data", "Descrição", "Tipo", "Valor"],
    )

    df_merged = pd.concat([df_atual, df_novos], ignore_index=True)
    df_merged["Data"] = pd.to_datetime(df_merged["Data"], errors="coerce")
    df_merged = df_merged.sort_values("Data", kind="stable").reset_index(drop=True)
    save_cash_to_gdrive(ano_mes_ref, df_merged)

    return len(df_novos), removidos


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
