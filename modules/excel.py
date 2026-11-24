from io import BytesIO

import pandas as pd
from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


def formatar_tabela_excel(ws, df, start_row=1):
    """
    Aplica estilo básico à tabela:
    - Cabeçalho em negrito, fundo cinza, centralizado
    - Largura das colunas ajustada ao conteúdo
    - Colunas de valor com formato de moeda (R$)
    """
    n_rows = len(df)
    n_cols = len(df.columns)

    for col_idx in range(1, n_cols + 1):
        cell = ws.cell(row=start_row, column=col_idx)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="center")

    ws.freeze_panes = ws[f"A{start_row + 1}"]

    for col_idx in range(1, n_cols + 1):
        max_len = 0
        for row_idx in range(start_row, start_row + 1 + n_rows):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value is not None:
                max_len = max(max_len, len(str(value)))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    col_names_lower = [str(c).lower() for c in df.columns]
    currency_prefixes = ("entradas", "saídas", "saidas", "resultado", "saldo", "valor")
    for col_idx, col_name in enumerate(col_names_lower, start=1):
        if any(col_name.startswith(p) for p in currency_prefixes):
            for row_idx in range(start_row + 1, start_row + 1 + n_rows):
                cell = ws.cell(row=row_idx, column=col_idx)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '"R$" #,##0.00'


def gerar_excel_fechamento(
    nome_periodo: str,
    df_resumo_contas: pd.DataFrame,
    df_consolidado: pd.DataFrame,
    df_cat_export: pd.DataFrame,
    df_mov: pd.DataFrame,
    df_dinheiro: pd.DataFrame,
    df_gerencial: pd.DataFrame,
    df_canais: pd.DataFrame,
) -> BytesIO:
    """
    Monta o Excel do fechamento (abas Resumo, ResumoDados, Categorias, Movimentos,
    Dinheiro, Gerencial e Canais). Usado ao gerar o fechamento e ao salvar uma
    nova versão de um fechamento já salvo.
    """
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        start_row_resumo = 3
        df_resumo_contas.to_excel(writer, sheet_name="Resumo", index=False, startrow=start_row_resumo)

        start_row_consol = start_row_resumo + len(df_resumo_contas) + 3
        df_consolidado.to_excel(writer, sheet_name="Resumo", index=False, startrow=start_row_consol)

        df_consolidado.to_excel(writer, sheet_name="ResumoDados", index=False)
        df_cat_export.to_excel(writer, sheet_name="Categorias", index=False, startrow=1)
        df_mov.to_excel(writer, sheet_name="Movimentos", index=False, startrow=1)
        df_dinheiro.to_excel(writer, sheet_name="Dinheiro", index=False, startrow=1)
        df_gerencial.to_excel(writer, sheet_name="Gerencial", index=False, startrow=1)
        df_canais.to_excel(writer, sheet_name="Canais", index=False, startrow=1)

        ws_res = writer.sheets["Resumo"]
        ws_cat = writer.sheets["Categorias"]
        ws_mov = writer.sheets["Movimentos"]
        ws_din = writer.sheets["Dinheiro"]
        ws_ger = writer.sheets["Gerencial"]
        ws_can = writer.sheets["Canais"]

        ws_res["A1"] = f"Fechamento Tempero das Gurias - {nome_periodo}"
        ws_res["A1"].font = Font(bold=True, size=14)
        ws_res["A1"].alignment = Alignment(horizontal="left")

        formatar_tabela_excel(ws_res, df_resumo_contas, start_row=start_row_resumo)
        formatar_tabela_excel(ws_res, df_consolidado, start_row=start_row_consol)
        if not df_cat_export.empty:
            formatar_tabela_excel(ws_cat, df_cat_export, start_row=1)
        if not df_mov.empty:
            formatar_tabela_excel(ws_mov, df_mov, start_row=1)
        if not df_dinheiro.empty:
            formatar_tabela_excel(ws_din, df_dinheiro, start_row=1)

        ws_ger["A1"] = "Visão gerencial (a variação de caixa é o mesmo 'Resultado do período' do Resumo)"
        ws_ger["A1"].font = Font(bold=True)
        formatar_tabela_excel(ws_ger, df_gerencial, start_row=1)
        for r in range(3, 3 + len(df_gerencial)):
            ws_ger.cell(row=r, column=3).number_format = "0.0%"
        ws_ger.column_dimensions["A"].width = 70

        ws_can["A1"] = "Receita por canal (identificado pelo texto do lançamento)"
        ws_can["A1"].font = Font(bold=True)
        formatar_tabela_excel(ws_can, df_canais, start_row=1)
        for r in range(3, 3 + len(df_canais)):
            ws_can.cell(row=r, column=3).number_format = "0.0%"

    buffer.seek(0)
    return buffer
