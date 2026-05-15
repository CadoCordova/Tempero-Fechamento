import pandas as pd
import streamlit as st

from modules.utils import format_currency


def validar_consistencia_fechamento(
    df_mov: pd.DataFrame,
    df_resumo_contas: pd.DataFrame,
    df_consolidado: pd.DataFrame,
    saldo_inicial: float,
) -> list[str]:
    """
    Verifica se os totais do fechamento são consistentes.
    Retorna lista de strings com avisos (vazia se tudo OK).
    """
    avisos: list[str] = []

    if df_mov.empty or df_resumo_contas.empty or df_consolidado.empty:
        return ["Dados insuficientes para validar consistência"]

    try:
        total_movimentos = df_mov["Valor"].sum()
        resultado_consolidado = df_consolidado["Resultado do período"].iloc[0]
        entradas_totais = df_consolidado["Entradas totais"].iloc[0]
        saidas_totais = df_consolidado["Saídas totais"].iloc[0]
        saldo_final_relatorio = df_consolidado["Saldo final"].iloc[0]

        # 1. Soma dos movimentos vs resultado consolidado
        diff = abs(total_movimentos - resultado_consolidado)
        if diff > 0.01:
            avisos.append(
                f"⚠️ Diferença na soma dos movimentos: "
                f"Total movimentos = {format_currency(total_movimentos)}, "
                f"Resultado consolidado = {format_currency(resultado_consolidado)} "
                f"(diferença: {format_currency(diff)})"
            )

        # 2. Saldo final
        saldo_final_calculado = saldo_inicial + resultado_consolidado
        diff = abs(saldo_final_calculado - saldo_final_relatorio)
        if diff > 0.01:
            avisos.append(
                f"⚠️ Saldo final inconsistente: "
                f"Calculado = {format_currency(saldo_final_calculado)}, "
                f"Relatório = {format_currency(saldo_final_relatorio)} "
                f"(diferença: {format_currency(diff)})"
            )

        # 3. Entradas/saídas totais vs soma por conta
        for label, total, col in [
            ("Entradas", entradas_totais, "Entradas"),
            ("Saídas", saidas_totais, "Saídas"),
        ]:
            soma_contas = df_resumo_contas[col].sum()
            diff = abs(total - soma_contas)
            if diff > 0.01:
                avisos.append(
                    f"⚠️ {label} totais não batem com soma por conta: "
                    f"Total = {format_currency(total)}, "
                    f"Soma contas = {format_currency(soma_contas)} "
                    f"(diferença: {format_currency(diff)})"
                )

        # 4. Resultado por conta (Entradas + Saídas = Resultado)
        for _, conta in df_resumo_contas.iterrows():
            calc = conta["Entradas"] + conta["Saídas"]
            diff = abs(calc - conta["Resultado"])
            if diff > 0.01:
                avisos.append(
                    f"⚠️ Conta {conta['Conta']}: resultado inconsistente: "
                    f"Calculado = {format_currency(calc)}, "
                    f"Relatório = {format_currency(conta['Resultado'])}"
                )

        # 5. Movimentos por conta vs resumo por conta
        for conta_nome in ("Itaú", "PagSeguro", "Dinheiro"):
            if conta_nome in df_mov["Conta"].values and conta_nome in df_resumo_contas["Conta"].values:
                soma_mov = df_mov[df_mov["Conta"] == conta_nome]["Valor"].sum()
                resumo_result = df_resumo_contas[df_resumo_contas["Conta"] == conta_nome].iloc[0]["Resultado"]
                diff = abs(soma_mov - resumo_result)
                if diff > 0.01:
                    avisos.append(
                        f"⚠️ Conta {conta_nome}: soma dos movimentos não bate com resumo: "
                        f"Movimentos = {format_currency(soma_mov)}, "
                        f"Resumo = {format_currency(resumo_result)}"
                    )

        # 6. Resultado consolidado vs soma dos resultados por conta
        soma_resultados = df_resumo_contas["Resultado"].sum()
        diff = abs(resultado_consolidado - soma_resultados)
        if diff > 0.01:
            avisos.append(
                f"⚠️ Resultado consolidado diferente da soma por conta: "
                f"Consolidado = {format_currency(resultado_consolidado)}, "
                f"Soma contas = {format_currency(soma_resultados)}"
            )

        # 7. Valores extremos e transações suspeitas
        if not df_mov.empty:
            valores_altos = df_mov[abs(df_mov["Valor"]) > 100_000]
            for _, mov in valores_altos.iterrows():
                desc = str(mov.get("Descrição", ""))[:50]
                avisos.append(
                    f"⚠️ Valor extremamente alto detectado: "
                    f"{format_currency(abs(mov['Valor']))} em {mov['Conta']} - {desc}..."
                )

            nao_classificadas = len(df_mov[df_mov["Categoria"] == "A Classificar"])
            if nao_classificadas > 10:
                avisos.append(f"⚠️ Alto número de transações não classificadas: {nao_classificadas}")

            zeros = len(df_mov[df_mov["Valor"] == 0])
            if zeros > 5:
                avisos.append(f"⚠️ {zeros} transações com valor zero")

        # 8. Sinais de entradas/saídas
        if entradas_totais < 0:
            avisos.append("⚠️ Entradas totais são negativas (verifique os sinais)")
        if saidas_totais > 0:
            avisos.append("⚠️ Saídas totais são positivas (verifique os sinais)")

        # 9. Consistência interna do consolidado
        resultado_calc = entradas_totais + saidas_totais
        diff = abs(resultado_consolidado - resultado_calc)
        if diff > 0.01:
            avisos.append(
                f"⚠️ Inconsistência interna no consolidado: "
                f"Entradas + Saídas = {format_currency(resultado_calc)}, "
                f"Resultado = {format_currency(resultado_consolidado)}"
            )

    except Exception as e:
        avisos.append(f"❌ Erro durante validação: {e}")

    return avisos


def exibir_painel_validacao(avisos: list[str]):
    """Exibe os avisos de validação em um painel expandível."""
    if not avisos:
        st.success("✅ Todas as validações passaram! Os dados estão consistentes.")
        return

    num_criticos = sum(1 for a in avisos if a.startswith("❌"))
    num_alertas = sum(1 for a in avisos if a.startswith("⚠️"))
    icone = "❌" if num_criticos else "⚠️"

    with st.expander(f"{icone} Validação de Consistência ({len(avisos)} avisos)", expanded=True):
        if num_criticos:
            st.error(f"**{num_criticos} erro(s) crítico(s)** encontrado(s)")
        if num_alertas:
            st.warning(f"**{num_alertas} alerta(s)** encontrado(s)")

        for i, aviso in enumerate(avisos, 1):
            st.markdown(f"**{i}.** {aviso}")

        st.markdown("---")
        st.markdown("**Ações recomendadas:**")

        if any("Diferença na soma dos movimentos" in a for a in avisos):
            st.markdown("• Verifique se todos os movimentos foram importados corretamente")
            st.markdown("• Confira os filtros aplicados aos extratos")

        if any("Saldo final inconsistente" in a for a in avisos):
            st.markdown("• Verifique o saldo inicial informado")
            st.markdown("• Confira os cálculos manuais do saldo")

        if any("Valor extremamente alto" in a for a in avisos):
            st.markdown("• Verifique se os valores estão corretos")
            st.markdown("• Confira se há duplicação de lançamentos")

        if any("Alto número de transações não classificadas" in a for a in avisos):
            st.markdown("• Ajuste as regras de categorização na aba 'Conferência & Categorias'")
            st.markdown("• Classifique manualmente as transações pendentes")
