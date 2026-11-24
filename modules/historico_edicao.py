"""
Edição de um fechamento já salvo no histórico.

Só a CATEGORIA dos lançamentos pode mudar. Entradas, saídas e resultado não mudam
(recategorizar não altera valores); o que muda é a aba Categorias, a visão
Gerencial e a de Canais. O arquivo novo é gerado com o mesmo formato do fechamento.
"""
from __future__ import annotations

from collections import defaultdict
from io import BytesIO

import pandas as pd

from modules.canais import receita_por_canal_dict, tabela_canais
from modules.excel import gerar_excel_fechamento
from modules.gerencial import ponte_por_movimentos, tabela_ponte

_ROTULO_CONTA = {"Itau": "Itaú", "Itaú": "Itaú", "PagSeguro": "PagSeguro", "Dinheiro": "Dinheiro"}


def _resumo_por_conta(df_mov: pd.DataFrame) -> pd.DataFrame:
    linhas = []
    for conta in ("Itau", "PagSeguro", "Dinheiro"):
        s = df_mov[df_mov["Conta"] == conta]["Valor"]
        ent = float(s[s > 0].sum())
        sai = float(s[s < 0].sum())
        linhas.append({"Conta": _ROTULO_CONTA[conta], "Entradas": ent, "Saídas": sai, "Resultado": ent + sai})
    return pd.DataFrame(linhas)


def aplicar_ajustes_e_gerar_excel(
    rep: dict, ajustes: dict[int, str], nome_arquivo_fallback: str = ""
) -> tuple[pd.DataFrame, BytesIO, str]:
    """
    rep: dict de load_fechamento_report_from_gdrive (movimentos, consolidado, resumo_contas, dinheiro).
    ajustes: {posição da linha em movimentos: nova categoria}.
    Retorna (df_mov_novo, excel, nome_do_periodo).
    """
    df_mov = rep["movimentos"].copy()
    df_mov["Valor"] = pd.to_numeric(df_mov["Valor"], errors="coerce").fillna(0.0)
    col_cat = df_mov.columns.get_loc("Categoria")
    for pos, cat in ajustes.items():
        if not cat:
            continue
        df_mov.iloc[int(pos), col_cat] = cat

    entradas_cat: dict[str, float] = defaultdict(float)
    saidas_cat: dict[str, float] = defaultdict(float)
    for cat, v in zip(df_mov["Categoria"], df_mov["Valor"]):
        if v > 0:
            entradas_cat[cat] += v
        elif v < 0:
            saidas_cat[cat] += v
    df_cat = pd.DataFrame(
        [
            {"Categoria": c, "Entradas": entradas_cat.get(c, 0.0), "Saídas": saidas_cat.get(c, 0.0)}
            for c in sorted(set(entradas_cat) | set(saidas_cat))
        ]
    )

    df_consol = rep.get("consolidado")
    if df_consol is None or df_consol.empty:
        raise ValueError("Este fechamento não tem a aba ResumoDados; não dá para gerar uma nova versão.")
    df_res = rep.get("resumo_contas")
    if df_res is None or df_res.empty or not {"Conta", "Entradas", "Saídas", "Resultado"} <= set(df_res.columns):
        df_res = _resumo_por_conta(df_mov)

    df_din = rep.get("dinheiro")
    if df_din is None:
        df_din = pd.DataFrame(columns=["Data", "Descrição", "Tipo", "Valor"])

    nome_periodo = str(df_consol.iloc[0].get("Nome do período") or nome_arquivo_fallback or "periodo")
    excel = gerar_excel_fechamento(
        nome_periodo,
        df_res,
        df_consol,
        df_cat,
        df_mov,
        df_din,
        tabela_ponte(ponte_por_movimentos(df_mov)),
        tabela_canais(receita_por_canal_dict(df_mov)),
    )
    return df_mov, excel, nome_periodo
