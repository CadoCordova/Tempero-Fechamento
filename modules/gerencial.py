"""
Visão gerencial do fechamento.

O "Resultado do período" do fechamento é a variação de caixa: mistura a operação
da loja com retiradas dos sócios e movimentos de aplicação/resgate. Aqui o mesmo
número é aberto em uma "ponte":

    Receita
  − Custos e despesas operacionais
  = Resultado operacional
  − Retiradas dos sócios (Transferência / Sócios + Sangria)
  = Resultado após retiradas
  ± Aplicações e resgates (líquido, por conta)
  = Variação de caixa  (igual ao "Resultado do período")

Nada aqui altera os totais do fechamento: é só uma leitura em cima das categorias.
"""
from __future__ import annotations

import pandas as pd

CAT_RECEITA = "Vendas / Receitas"
CATS_SOCIOS = ("Transferência Interna / Sócios", "Sangria")
CATS_APLICACAO = ("Investimentos (Aplicações)", "Rendimentos de Aplicações")
# Compra de bem para a loja (veículo, equipamentos): não é custo do mês nem retirada
CAT_INVESTIMENTO_LOJA = "Investimento na Loja (Imobilizado)"
CATS_INSUMOS = ("Fornecedores e Insumos", "Embalagens")

# Retirada de um único lançamento acima disso é tratada como "pontual"
LIMITE_RETIRADA_PONTUAL = 10_000.0

# Destino de cada aplicação, por conta de origem do movimento
ROTULO_APLICACAO_CONTA = {
    "Itau": "Aplicações Itaú (melhorias da loja)",
    "Itaú": "Aplicações Itaú (melhorias da loja)",
    "PagSeguro": "Aplicações PagBank (reserva trabalhista)",
    "Dinheiro": "Ajustes lançados no caixa em dinheiro",
}


def ponte_por_categoria(cats: dict[str, float]) -> dict:
    """
    cats: {categoria: valor líquido (entradas + saídas)}.
    Retorna os blocos da ponte (valores com sinal: entrada +, saída −).
    """
    total = float(sum(cats.values()))
    receita = float(cats.get(CAT_RECEITA, 0.0))
    retiradas = float(sum(cats.get(c, 0.0) for c in CATS_SOCIOS))
    aplicacoes = float(sum(cats.get(c, 0.0) for c in CATS_APLICACAO))
    investimento_loja = float(cats.get(CAT_INVESTIMENTO_LOJA, 0.0))
    operacional = total - retiradas - aplicacoes - investimento_loja
    return {
        "receita": receita,
        "custos": operacional - receita,
        "operacional": operacional,
        "retiradas": retiradas,
        "apos_retiradas": operacional + retiradas,
        "investimento_loja": investimento_loja,
        "aplicacoes": aplicacoes,
        "variacao_caixa": total,
    }


def ponte_por_movimentos(df_mov: pd.DataFrame) -> dict:
    """Ponte completa a partir dos movimentos (permite abrir por conta e achar pontuais)."""
    if df_mov is None or df_mov.empty:
        return {
            **ponte_por_categoria({}),
            "aplicacoes_por_conta": {},
            "retiradas_pontuais": 0.0,
        }

    df = df_mov.copy()
    df["Valor"] = pd.to_numeric(df["Valor"], errors="coerce").fillna(0.0)
    cats = df.groupby("Categoria")["Valor"].sum().to_dict()
    ponte = ponte_por_categoria(cats)

    aplic = df[df["Categoria"].isin(CATS_APLICACAO)]
    ponte["aplicacoes_por_conta"] = {
        ROTULO_APLICACAO_CONTA.get(conta, f"Aplicações ({conta})"): float(v)
        for conta, v in aplic.groupby("Conta")["Valor"].sum().items()
        if abs(v) >= 0.005
    }

    soc = df[df["Categoria"].isin(CATS_SOCIOS) & (df["Valor"].abs() >= LIMITE_RETIRADA_PONTUAL)]
    ponte["retiradas_pontuais"] = float(soc["Valor"].sum())
    return ponte


def tabela_ponte(ponte: dict) -> pd.DataFrame:
    """DataFrame [Linha, Valor, % da receita] para tela e Excel."""
    receita = ponte["receita"]

    def pct(v):
        return (v / receita) if receita else None

    linhas = [
        ("(+) Receita (Vendas / Receitas)", ponte["receita"]),
        ("(−) Custos e despesas operacionais", ponte["custos"]),
        ("(=) Resultado operacional", ponte["operacional"]),
        ("(−) Retiradas dos sócios (Transferência + Sangria)", ponte["retiradas"]),
    ]
    if abs(ponte.get("retiradas_pontuais", 0.0)) >= 0.005:
        linhas.append(
            (f"   dos quais pontuais (lançamento acima de R$ {LIMITE_RETIRADA_PONTUAL:,.0f})"
             .replace(",", "."), ponte["retiradas_pontuais"])
        )
    linhas.append(("(=) Resultado após retiradas", ponte["apos_retiradas"]))
    if abs(ponte.get("investimento_loja", 0.0)) >= 0.005:
        linhas.append(("(−) Investimento na loja (veículo, equipamentos)", ponte["investimento_loja"]))

    por_conta = ponte.get("aplicacoes_por_conta") or {}
    if por_conta:
        for rotulo, v in sorted(por_conta.items()):
            linhas.append((rotulo + " — aplicação (−) / resgate (+)", v))
    else:
        linhas.append(("Aplicações e resgates (líquido)", ponte["aplicacoes"]))
    linhas.append(("(=) Variação de caixa (Resultado do período)", ponte["variacao_caixa"]))

    return pd.DataFrame(
        [{"Linha": nome, "Valor": float(v), "% da receita": pct(v)} for nome, v in linhas]
    )


def calcular_custo_insumos(cats: dict[str, float]) -> float:
    """Custo de insumos (positivo): Fornecedores + Embalagens."""
    return abs(sum(cats.get(c, 0.0) for c in CATS_INSUMOS))


def cmv_mensal_e_media_movel(
    meses: list[str], dados_cats: dict[str, dict[str, float]], janela: int = 3
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """
    CMV = (Fornecedores + Embalagens) / Receita.
    Retorna (cmv_do_mes, cmv_media_movel). A média móvel é ponderada
    (soma de custos / soma de receitas dos últimos `janela` meses).
    """
    cmv: dict[str, float | None] = {}
    mm: dict[str, float | None] = {}
    for i, mes in enumerate(meses):
        cats = dados_cats.get(mes, {})
        receita = abs(cats.get(CAT_RECEITA, 0.0))
        cmv[mes] = (calcular_custo_insumos(cats) / receita) if receita > 0 else None

        grupo = meses[max(0, i - janela + 1): i + 1]
        rec_g = sum(abs(dados_cats.get(m, {}).get(CAT_RECEITA, 0.0)) for m in grupo)
        cus_g = sum(calcular_custo_insumos(dados_cats.get(m, {})) for m in grupo)
        mm[mes] = (cus_g / rec_g) if rec_g > 0 else None
    return cmv, mm
