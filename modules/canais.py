"""
Receita por canal (cartão, iFood, PIX, vale-refeição, dinheiro...).

A receita do fechamento vem dos extratos (recebimentos); o canal é identificado pelo
texto do lançamento. Linhas que não se encaixam em nenhum canal ficam em "Outros" /
"TED (origem a confirmar)" em vez de serem chutadas.
"""
from __future__ import annotations

import re

import pandas as pd

from modules.utils import normalizar_texto

CAT_RECEITA = "Vendas / Receitas"

CANAIS_ORDEM = [
    "Cartão (maquininhas)",
    "iFood",
    "PIX",
    "Vale-refeição/benefício",
    "Dinheiro",
    "99 Food",
    "TED (origem a confirmar)",
    "Ajustes manuais",
    "Outros",
]

_RE_VALE = re.compile(r"ALELO|VR BENE|PLUXEE|TICKET|SODEXO|GREEN CARD")
_RE_CARTAO = re.compile(
    r"\bREDE\b|REDECARD|MASTERCARD|\bVISA\b|\bELO\b|BANRISUL|TOPAZIO|TUNA|CARTAO|CARTOES|DEBITO|CREDITO"
)


def canal_da_descricao(descricao, conta=None) -> str:
    u = normalizar_texto(descricao)
    if str(conta) == "Dinheiro":
        return "Ajustes manuais" if u.startswith("AJUSTE") else "Dinheiro"
    if "IFOOD" in u:
        return "iFood"
    if _RE_VALE.search(u):
        return "Vale-refeição/benefício"
    if "99 FOOD" in u or "99FOOD" in u:
        return "99 Food"
    if _RE_CARTAO.search(u):
        return "Cartão (maquininhas)"
    if "PIX" in u:
        return "PIX"
    if u.startswith("TED"):
        return "TED (origem a confirmar)"
    return "Outros"


def receita_por_canal_dict(df_mov: pd.DataFrame) -> dict[str, float]:
    """{canal: valor} só com Vendas / Receitas positivas (sem a linha de saldo inicial)."""
    if df_mov is None or df_mov.empty or "Categoria" not in df_mov.columns:
        return {}
    df = df_mov.copy()
    df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce").fillna(0.0)
    df = df[(df["Categoria"] == CAT_RECEITA) & (df["Valor"] > 0)]
    df = df[~df["Descrição"].astype(str).str.strip().str.lower().str.startswith("saldo inicial")]
    out: dict[str, float] = {}
    for _, r in df.iterrows():
        c = canal_da_descricao(r["Descrição"], r.get("Conta"))
        out[c] = out.get(c, 0.0) + float(r["Valor"])
    return out


def tabela_canais(canais: dict[str, float]) -> pd.DataFrame:
    total = sum(canais.values())
    linhas = []
    for c in CANAIS_ORDEM:
        v = canais.get(c, 0.0)
        if abs(v) >= 0.005:
            linhas.append({"Canal": c, "Valor": v, "% da receita": (v / total) if total else None})
    linhas.sort(key=lambda x: -x["Valor"])
    if linhas:
        linhas.append({"Canal": "Total", "Valor": total, "% da receita": 1.0 if total else None})
    return pd.DataFrame(linhas, columns=["Canal", "Valor", "% da receita"])
