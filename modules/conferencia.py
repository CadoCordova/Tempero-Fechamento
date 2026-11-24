"""
Conferência geral dos fechamentos salvos.

Varre os relatórios (aba Movimentos / ResumoDados / Categorias) de todos os meses e devolve
uma lista de achados para revisão. Nada aqui altera os arquivos: é só leitura.

Cada achado: {"mes", "gravidade" ("erro" | "aviso"), "tipo", "texto", "valor"}.
"""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

import pandas as pd

CAT_RECEITA = "Vendas / Receitas"
CAT_A_CLASSIFICAR = "A Classificar"

TOLERANCIA = 0.011  # centavos
LIMITE_CAIXA_MANUAL = 200.0  # lançamento manual de caixa (sem "(Suitable)") a partir disso é listado
LIMITE_DUPLICADO_DINHEIRO = 50.0

# Categorias que entram juntas no CMV: o mesmo fornecedor vender insumo e embalagem não é inconsistência
# (ex.: Guaíba Distribuidora). Para esta verificação são tratadas como uma só.
GRUPO_EQUIVALENTE = {
    "Fornecedores e Insumos": "Insumos e Embalagens (CMV)",
    "Embalagens": "Insumos e Embalagens (CMV)",
}

TIPOS = {
    "saldo_inicial": "Saldo inicial contado como receita",
    "a_classificar": "Lançamentos A Classificar",
    "fornecedor_multi_categoria": "Mesmo favorecido em categorias diferentes",
    "ponte": "Totais que não fecham",
    "duplicado": "Possíveis lançamentos duplicados",
    "caixa_manual": "Lançamentos manuais no caixa",
}


def _norm(txt) -> str:
    s = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def _fmt(v: float) -> str:
    s = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if v < 0 else "") + "R$ " + s


def _prep_mov(rep: dict) -> pd.DataFrame:
    df = rep.get("movimentos")
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=["Data", "Conta", "Descrição", "Categoria", "Valor"])
    df = df.copy()
    for c in ("Data", "Conta", "Descrição", "Categoria", "Valor"):
        if c not in df.columns:
            df[c] = None
    df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce").fillna(0.0)
    df["Categoria"] = df["Categoria"].astype(str).str.strip()
    df["Descrição"] = df["Descrição"].astype(str)
    return df.reset_index(drop=True)


def favorecido(descricao: str) -> str:
    """
    Nome do favorecido de um débito. Extratos Itaú/PagSeguro vêm como
    'TIPO | NOME | DOCUMENTO | valor | ...': o favorecido é o 2º campo. Sem esse padrão, devolve "".
    """
    partes = [p.strip() for p in str(descricao or "").split("|")]
    if len(partes) < 3:
        return ""
    nome = _norm(partes[1])
    if not nome or nome == "NAN" or len(nome) < 4:
        return ""
    return nome


def _eh_saldo_inicial(desc: str) -> bool:
    d = _norm(desc).lower()
    return d.startswith("saldo inicial") or d.startswith("caixa inicial")


def achados_mes(mes: str, rep: dict) -> list[dict]:
    """Verificações que dependem só de um mês."""
    out: list[dict] = []
    df = _prep_mov(rep)

    def add(gravidade, tipo, texto, valor=None):
        out.append({"mes": mes, "gravidade": gravidade, "tipo": tipo, "texto": texto, "valor": valor})

    # 1) saldo inicial do caixa contado como receita
    if not df.empty:
        m = df["Descrição"].apply(_eh_saldo_inicial) & (df["Valor"] > 0) & (df["Categoria"] == CAT_RECEITA)
        for _, r in df[m].iterrows():
            add("erro", "saldo_inicial",
                f"'{str(r['Descrição']).strip()}' de {_fmt(r['Valor'])} está em {CAT_RECEITA}: não é receita do mês.",
                float(r["Valor"]))

    # 2) A Classificar
    if not df.empty:
        ac = df[df["Categoria"] == CAT_A_CLASSIFICAR]
        if len(ac):
            total = float(ac["Valor"].sum())
            add("aviso", "a_classificar",
                f"{len(ac)} lançamento(s) em {CAT_A_CLASSIFICAR} somando {_fmt(total)} (soma líquida).", total)

    # 3) totais que não fecham
    if not df.empty:
        soma_mov = float(df["Valor"].sum())
        cons = rep.get("consolidado")
        if cons is not None and len(cons):
            lin = cons.iloc[0]

            def num(k):
                return float(pd.to_numeric(lin.get(k), errors="coerce") or 0.0)

            res = num("Resultado do período")
            if abs(res - soma_mov) > TOLERANCIA:
                add("erro", "ponte",
                    f"Resultado do período ({_fmt(res)}) difere da soma dos Movimentos ({_fmt(soma_mov)}): "
                    f"diferença de {_fmt(res - soma_mov)}.", res - soma_mov)
            ent, sai = float(df[df["Valor"] > 0]["Valor"].sum()), float(df[df["Valor"] < 0]["Valor"].sum())
            if "Entradas totais" in lin.index and abs(num("Entradas totais") - ent) > TOLERANCIA:
                add("erro", "ponte",
                    f"Entradas totais ({_fmt(num('Entradas totais'))}) difere dos Movimentos ({_fmt(ent)}).",
                    num("Entradas totais") - ent)
            if "Saídas totais" in lin.index and abs(num("Saídas totais") - sai) > TOLERANCIA:
                add("erro", "ponte",
                    f"Saídas totais ({_fmt(num('Saídas totais'))}) difere dos Movimentos ({_fmt(sai)}).",
                    num("Saídas totais") - sai)
        cat = rep.get("categorias")
        if cat is not None and len(cat) and {"Categoria", "Entradas", "Saídas"} <= set(cat.columns):
            c = cat.copy()
            c["Entradas"] = pd.to_numeric(c["Entradas"], errors="coerce").fillna(0.0)
            c["Saídas"] = pd.to_numeric(c["Saídas"], errors="coerce").fillna(0.0)
            c = c[~c["Categoria"].astype(str).str.upper().str.startswith("TOTAL")]
            soma_cat = float(c["Entradas"].sum() + c["Saídas"].sum())
            if abs(soma_cat - soma_mov) > TOLERANCIA:
                add("erro", "ponte",
                    f"Soma da aba Categorias ({_fmt(soma_cat)}) difere dos Movimentos ({_fmt(soma_mov)}): "
                    f"diferença de {_fmt(soma_cat - soma_mov)}.", soma_cat - soma_mov)

    # 4) duplicados no mesmo mês
    if not df.empty:
        d = df.copy()
        d["_data"] = pd.to_datetime(d["Data"], errors="coerce").dt.date.astype(str)
        d["_desc"] = d["Descrição"].str.strip()
        d["_val"] = d["Valor"].round(2)
        grupos = d.groupby(["Conta", "_data", "_desc", "_val"]).size()
        for (conta, data, desc, val), n in grupos[grupos > 1].items():
            if conta == "Dinheiro" and abs(val) < LIMITE_DUPLICADO_DINHEIRO:
                continue
            if "VENDAS EM DINHEIRO" in _norm(desc):
                continue
            add("aviso", "duplicado",
                f"{n}x o mesmo lançamento em {conta} no dia {data}: {desc[:70]} ({_fmt(val)}).", float(val) * n)

    # 5) lançamentos manuais no caixa (sem o carimbo "(Suitable)")
    if not df.empty:
        cx = df[(df["Conta"] == "Dinheiro")
                & ~df["Descrição"].str.contains("(Suitable)", case=False, regex=False)
                & ~df["Descrição"].apply(_eh_saldo_inicial)]
        for _, r in cx.iterrows():
            nd = _norm(r["Descrição"])
            ajuste = "AJUSTE" in nd
            rotina = r["Categoria"] == "Sangria" or ("VENDAS" in nd and "DIA" in nd)  # lançamentos normais do caixa
            if ajuste or (abs(r["Valor"]) >= LIMITE_CAIXA_MANUAL and not rotina):
                add("aviso", "caixa_manual",
                    f"Lançamento manual no caixa: '{str(r['Descrição']).strip()[:60]}' {_fmt(r['Valor'])} "
                    f"({r['Categoria']}). Conferir se tem comprovante/motivo.", float(r["Valor"]))
    return out


def achados_favorecidos(reps: dict[str, dict]) -> list[dict]:
    """
    Favorecido (débitos Itaú/PagSeguro) que aparece em categorias diferentes, no mesmo mês ou entre meses.
    Pessoas físicas podem ter motivo (ex.: PIX a funcionária como Folha e como Terceiros), então é 'aviso'.
    """
    mapa: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for mes, rep in reps.items():
        df = _prep_mov(rep)
        if df.empty:
            continue
        for _, r in df[(df["Valor"] < 0) & (df["Conta"] != "Dinheiro")].iterrows():
            fav = favorecido(r["Descrição"])
            if fav:
                cat = GRUPO_EQUIVALENTE.get(r["Categoria"], r["Categoria"])
                mapa[fav][cat].append((mes, float(r["Valor"])))
    out = []
    for fav, cats in mapa.items():
        if len(cats) < 2:
            continue
        total = sum(v for lst in cats.values() for _, v in lst)
        partes = []
        for cat, lst in sorted(cats.items(), key=lambda kv: -len(kv[1])):
            meses = sorted({m for m, _ in lst})
            partes.append(f"{cat} ({len(lst)}x em {', '.join(meses)})")
        out.append({
            "mes": "vários",
            "gravidade": "aviso",
            "tipo": "fornecedor_multi_categoria",
            "texto": f"{fav.title()} ({_fmt(total)}): " + "; ".join(partes),
            "valor": float(total),
        })
    out.sort(key=lambda a: a["valor"])
    return out


def conferir(reps: dict[str, dict]) -> list[dict]:
    """reps: {YYYY-MM: relatório (load_fechamento_report_from_gdrive)}."""
    out: list[dict] = []
    for mes in sorted(reps):
        out.extend(achados_mes(mes, reps[mes]))
    out.extend(achados_favorecidos(reps))
    return out


def resumo_por_tipo(achados: list[dict]) -> pd.DataFrame:
    linhas = []
    for tipo, rotulo in TIPOS.items():
        itens = [a for a in achados if a["tipo"] == tipo]
        linhas.append({
            "Verificação": rotulo,
            "Achados": len(itens),
            "Situação": "✅ ok" if not itens else ("🔴 corrigir" if any(a["gravidade"] == "erro" for a in itens) else "🟡 revisar"),
        })
    return pd.DataFrame(linhas)


def carregar_relatorios_recentes() -> tuple[dict[str, dict], list[str]]:
    """Baixa a versão mais recente de cada mês cheio. Devolve ({mes: rep}, [avisos de leitura])."""
    from modules.controle_anual import _eh_quinzena, selecionar_versoes_recentes
    from modules.gdrive import list_history_from_gdrive, load_fechamento_report_from_gdrive
    from modules.utils import get_ano_mes

    avisos: list[str] = []
    arquivos = [
        f for f in list_history_from_gdrive()
        if str(f.get("name", "")).startswith("fechamento_tempero_") and str(f.get("name", "")).endswith(".xlsx")
        and not _eh_quinzena(f)
    ]
    reps: dict[str, dict] = {}
    for f in selecionar_versoes_recentes(arquivos):
        mes = get_ano_mes(f.get("name", ""))
        if not mes:
            continue
        try:
            reps[mes] = load_fechamento_report_from_gdrive(f["id"])
        except Exception as e:  # noqa: BLE001
            avisos.append(f"Não consegui ler {f.get('name')}: {e}")
    return reps, avisos
