from io import BytesIO
from pathlib import Path

import pandas as pd

from modules.utils import extrair_descricao_linha, normalizar_texto, parse_numero_br


def ler_arquivo_tabela_upload(uploaded_file) -> list[dict]:
    """
    Lê CSV/XLSX de bancos aceitando o extrato original, mesmo com cabeçalho
    e informações antes da tabela de dados.
    """
    suffix = Path(uploaded_file.name).suffix.lower()

    if suffix in (".csv", ".txt"):
        # sep=None com engine="python" detecta automaticamente o separador (;  ,  tab…)
        df = pd.read_csv(uploaded_file, sep=None, engine="python")

    elif suffix in (".xlsx", ".xls"):
        # Lê os bytes uma única vez para poder reusar o stream caso o header não seja encontrado
        raw_bytes = BytesIO(uploaded_file.read())
        raw = pd.read_excel(raw_bytes, header=None)

        header_idx = None
        for i, row in raw.iterrows():
            valores = [str(x).strip().upper() for x in row.tolist() if not pd.isna(x)]
            if not valores:
                continue
            if "DATA" in valores and any(
                col in valores
                for col in ["LANÇAMENTO", "LANCAMENTO", "LANÇAMENTOS", "DESCRIÇÃO", "DESCRICAO", "TIPO"]
            ):
                header_idx = i
                break

        if header_idx is not None:
            header_row = raw.iloc[header_idx].tolist()
            cols = [
                v.strip() if isinstance(v, str) else ("" if pd.isna(v) else str(v))
                for v in header_row
            ]
            df = raw.iloc[header_idx + 1:].copy()
            df.columns = cols
            df = df.dropna(how="all").reset_index(drop=True)
        else:
            raw_bytes.seek(0)
            df = pd.read_excel(raw_bytes)
    else:
        raise RuntimeError(f"Formato não suportado: {suffix}. Use .csv ou .xlsx.")

    df = df.rename(columns=lambda c: str(c).strip())

    return [
        {(k.strip() if isinstance(k, str) else k): v for k, v in rec.items()}
        for rec in df.to_dict(orient="records")
    ]


def carregar_extrato_itau_upload(uploaded_file) -> tuple[float, float, float, list[dict]]:
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    for linha in ler_arquivo_tabela_upload(uploaded_file):
        descricao = extrair_descricao_linha(linha)
        desc_norm = normalizar_texto(descricao)

        if any(
            kw in desc_norm
            for kw in ("SALDO ANTERIOR", "SALDO TOTAL DISPONIVEL DIA", "SALDO DO DIA")
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
            debito = credito = 0.0
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

        movimentos.append({
            "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
            "descricao": descricao,
            "valor": valor,
            "conta": "Itau",
        })

    return entradas, saidas, entradas + saidas, movimentos


def carregar_extrato_pagseguro_upload(uploaded_file) -> tuple[float, float, float, list[dict]]:
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    for linha in ler_arquivo_tabela_upload(uploaded_file):
        descricao = extrair_descricao_linha(linha)
        desc_norm = normalizar_texto(descricao)

        if "SALDO DO DIA" in desc_norm or "SALDO DIA" in desc_norm:
            continue

        entrada = parse_numero_br(
            linha.get("Entradas") or linha.get("ENTRADAS") or linha.get("entradas") or 0
        )
        saida = parse_numero_br(
            linha.get("Saidas") or linha.get("SAIDAS")
            or linha.get("Saídas") or linha.get("saídas") or 0
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

        movimentos.append({
            "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
            "descricao": descricao,
            "valor": valor,
            "conta": "PagSeguro",
        })

    return entradas, saidas, entradas + saidas, movimentos
