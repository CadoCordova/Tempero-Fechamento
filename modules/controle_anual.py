"""
Módulo de Controle Anual — lê todos os fechamentos salvos no Drive
e monta o DRE mensal comparativo com alertas automáticos.
"""
import re
import unicodedata

import pandas as pd

from modules.gdrive import download_history_file, list_history_from_gdrive
from modules.gerencial import (
    CATS_SOCIOS,
    CAT_RECEITA,
    cmv_mensal_e_media_movel,
    ponte_por_categoria,
)
from modules.utils import format_currency, get_ano_mes

# Categorias que aparecem no DRE, na ordem desejada
CATEGORIAS_DRE = [
    "Vendas / Receitas",
    "Fornecedores e Insumos",
    "Motoboy / Entregas",
    "Fatura Cartão",
    "Impostos e Encargos",
    "Transferência Interna / Sócios",
    "Folha de Pagamento",
    "Aluguel Comercial",
    "Contabilidade e RH",
    "Energia Elétrica",
    "Nutricionista",
    "Sangria",
    "Investimento na Loja (Imobilizado)",
    "Investimentos (Aplicações)",
    "Rendimentos de Aplicações",
    "Internet",
    "Dedetização / Controle de Pragas",
]

# Limites para alertas automáticos
LIMITE_CMV_ATENCAO = 0.32   # acima de 32% → alerta vermelho
LIMITE_CMV_AVISO = 0.28     # entre 28–32% → alerta amarelo
LIMITE_RETIRADA_SOCIOS = 7000.0
LIMITE_MOTOBOY_PERC = 0.15  # acima de 15% da receita


def _normalizar(txt) -> str:
    if not txt:
        return ""
    s = str(txt).upper()
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")


def _chave_versao(f: dict) -> str:
    """Ordena versões do mesmo mês: carimbo _YYYYMMDD_HHMMSS do nome; senão modifiedTime."""
    m = re.search(r"_(\d{8})_(\d{6})\.xlsx$", str(f.get("name", "")))
    if m:
        return m.group(1) + m.group(2)
    return re.sub(r"\D", "", str(f.get("modifiedTime", "")))[:14]


def _ler_categorias_fechamento(buf) -> dict[str, float]:
    """
    Lê a aba Categorias do fechamento e retorna dict {categoria: valor_liquido}.
    Entradas são positivas, saídas negativas.
    """
    try:
        buf.seek(0)
        df = pd.read_excel(buf, sheet_name="Categorias", header=None)
        # A aba tem 1 linha de cabeçalho vazia (startrow=1), então pula linha 0
        df = df.iloc[1:].reset_index(drop=True)
        df.columns = ["Categoria", "Entradas", "Saídas"] + list(df.columns[3:])
        df["Entradas"] = pd.to_numeric(df["Entradas"], errors="coerce").fillna(0)
        df["Saídas"] = pd.to_numeric(df["Saídas"], errors="coerce").fillna(0)
        resultado = {}
        for _, row in df.iterrows():
            cat = str(row["Categoria"]).strip()
            if cat and cat != "nan":
                resultado[cat] = float(row["Entradas"]) + float(row["Saídas"])
        return resultado
    except Exception:
        return {}


def carregar_dre_anual() -> tuple[list[str], list[dict], dict]:
    """
    Lê todos os fechamentos do Drive e monta o DRE mensal.

    Retorna:
        meses: lista de strings YYYY-MM ordenada
        linhas_dre: lista de dicts {categoria, YYYY-MM: valor, ...}
        resumos: dict {YYYY-MM: {entradas, saidas, resultado}}
    """
    try:
        arquivos = list_history_from_gdrive()
    except Exception:
        return [], [], {}

    fechamentos = [
        f for f in arquivos
        if str(f.get("name", "")).startswith("fechamento_tempero_")
        and str(f.get("name", "")).endswith(".xlsx")
    ]

    dados: dict[str, dict[str, float]] = {}  # {ano_mes: {categoria: valor}}
    resumos: dict[str, dict] = {}            # {ano_mes: {entradas, saidas, resultado}}

    # Se o mesmo mês foi salvo mais de uma vez, vale a versão mais recente
    # (a listagem vem do mais novo para o mais antigo e a versão antiga estava vencendo).
    mais_recente: dict[str, dict] = {}
    for f in fechamentos:
        ano_mes = get_ano_mes(f.get("name", ""))
        if not ano_mes:
            continue
        atual = mais_recente.get(ano_mes)
        if atual is None or _chave_versao(f) > _chave_versao(atual):
            mais_recente[ano_mes] = f

    for ano_mes, f in mais_recente.items():
        try:
            buf = download_history_file(f["id"])

            # ResumoDados
            buf.seek(0)
            df_rd = pd.read_excel(buf, sheet_name="ResumoDados")
            if df_rd.empty:
                continue
            linha = df_rd.iloc[0]
            resumos[ano_mes] = {
                "entradas": float(linha.get("Entradas totais", 0) or 0),
                "saidas": float(linha.get("Saídas totais", 0) or 0),
                "resultado": float(linha.get("Resultado do período", 0) or 0),
            }

            # Categorias
            cats = _ler_categorias_fechamento(buf)
            dados[ano_mes] = cats

        except Exception:
            continue

    if not dados:
        return [], [], {}

    meses = sorted(dados.keys())

    # Monta linhas do DRE
    todas_cats = set()
    for cats in dados.values():
        todas_cats.update(cats.keys())

    # Ordena: primeiro as do DRE padrão, depois as demais
    cats_ordenadas = [c for c in CATEGORIAS_DRE if c in todas_cats]
    cats_extras = sorted(todas_cats - set(cats_ordenadas))
    cats_ordenadas += cats_extras

    linhas_dre = []
    for cat in cats_ordenadas:
        linha = {"Categoria": cat}
        for mes in meses:
            linha[mes] = dados[mes].get(cat, 0.0)
        linhas_dre.append(linha)

    # Linha de resultado no final
    linha_resultado = {"Categoria": "__ Resultado __"}
    for mes in meses:
        linha_resultado[mes] = resumos.get(mes, {}).get("resultado", 0.0)
    linhas_dre.append(linha_resultado)

    return meses, linhas_dre, resumos


def calcular_cmv(dados_resumos: dict, dados_cats: dict) -> dict[str, float | None]:
    """
    CMV (%) por mês: (Fornecedores + Embalagens) / Receita.
    (Antes só Fornecedores entrava; Embalagens é custo de insumo do prato.)
    """
    meses = sorted(dados_cats.keys())
    cmv, _ = cmv_mensal_e_media_movel(meses, dados_cats)
    return cmv


def gerar_alertas(meses, resumos, dados_cats) -> list[dict]:
    """
    Gera lista de alertas automáticos.
    Cada alerta: {tipo: 'erro'|'aviso'|'ok', texto: str}
    """
    alertas = []

    cmv_mes, cmv_mm = cmv_mensal_e_media_movel(list(meses), dados_cats)

    # CMV: o mês isolado oscila (boleto pago em um mês, consumo em outro),
    # então o alerta usa a média móvel de 3 meses.
    for mes in meses:
        cmv = cmv_mm.get(mes)
        if cmv is None:
            continue
        mes_txt = f" (mês isolado: {cmv_mes[mes]:.1%})" if cmv_mes.get(mes) is not None else ""
        if cmv > LIMITE_CMV_ATENCAO:
            alertas.append({
                "tipo": "erro",
                "texto": f"CMV (média 3 meses) em {mes}: {cmv:.1%}{mes_txt} — acima do limite de 32%. Investigar mix e desperdício."
            })
        elif cmv > LIMITE_CMV_AVISO:
            alertas.append({
                "tipo": "aviso",
                "texto": f"CMV (média 3 meses) em {mes}: {cmv:.1%}{mes_txt} — zona de atenção (28–32%)."
            })

    # Tendência de CMV crescente (3+ meses seguidos subindo, na média móvel)
    cmv_validos = [(m, cmv_mm[m]) for m in meses if cmv_mm.get(m) is not None]
    if len(cmv_validos) >= 3:
        subindo = all(
            cmv_validos[i][1] < cmv_validos[i + 1][1]
            for i in range(len(cmv_validos) - 1)
        )
        if subindo:
            alertas.append({
                "tipo": "erro",
                "texto": f"CMV (média 3 meses) subindo todo mês desde {cmv_validos[0][0]} — tendência de alta contínua. Ação urgente."
            })

    # Resultado operacional (sem aplicações e sem retiradas) e retiradas
    for mes in meses:
        ponte = ponte_por_categoria(dados_cats.get(mes, {}))
        if ponte["operacional"] < 0:
            alertas.append({
                "tipo": "erro",
                "texto": f"Resultado operacional negativo em {mes}: {format_currency(ponte['operacional'])}."
            })
        elif ponte["apos_retiradas"] < 0:
            alertas.append({
                "tipo": "aviso",
                "texto": (
                    f"Retiradas em {mes} ({format_currency(abs(ponte['retiradas']))}) acima do resultado "
                    f"operacional ({format_currency(ponte['operacional'])}): a diferença saiu do caixa/aplicações."
                )
            })

    # Retirada de sócios acima do limite (Transferência + Sangria)
    for mes in meses:
        cats = dados_cats.get(mes, {})
        retirada = abs(sum(cats.get(c, 0.0) for c in CATS_SOCIOS))
        if retirada > LIMITE_RETIRADA_SOCIOS:
            alertas.append({
                "tipo": "aviso",
                "texto": (
                    f"Retirada de sócios em {mes}: {format_currency(retirada)} "
                    f"(Transferência + Sangria) — acima de R$ 7.000. Verificar."
                )
            })

    # Motoboy acima de 15% da receita
    for mes in meses:
        cats = dados_cats.get(mes, {})
        receita = abs(cats.get(CAT_RECEITA, 0.0))
        motoboy = abs(cats.get("Motoboy / Entregas", 0.0))
        if receita > 0 and motoboy / receita > LIMITE_MOTOBOY_PERC:
            alertas.append({
                "tipo": "aviso",
                "texto": f"Motoboy em {mes}: {motoboy/receita:.1%} da receita — acima de 15%. Verificar volume."
            })

    # Positivo: receita crescendo nos últimos 2 meses
    if len(meses) >= 2:
        rec_penultimo = abs(dados_cats.get(meses[-2], {}).get(CAT_RECEITA, 0.0))
        rec_ultimo = abs(dados_cats.get(meses[-1], {}).get(CAT_RECEITA, 0.0))
        if rec_ultimo > rec_penultimo:
            alertas.append({
                "tipo": "ok",
                "texto": f"Receita crescendo: {format_currency(rec_penultimo)} em {meses[-2]} → {format_currency(rec_ultimo)} em {meses[-1]}."
            })

    return alertas
