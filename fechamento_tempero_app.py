import re
from collections import defaultdict
from datetime import datetime
from io import BytesIO
from pathlib import Path

APP_VERSION = Path("VERSION").read_text(encoding="utf-8").strip()

import altair as alt
import pandas as pd
import streamlit as st
from openpyxl.styles import Alignment, Font

from modules.auth import check_auth, current_role, current_user, has_role, require_role
from modules.caixa import lancar_importados_gmail, load_cash_from_gdrive, save_cash_to_gdrive
from modules.gmail_suitable import buscar_fechamentos_gmail
from modules.categorias import (
    CATEGORIAS_PADRAO,
    carregar_categorias_personalizadas,
    carregar_regras,
    classificar_categoria,
    get_regras_sessao,
    reload_regras_sessao,
    salvar_categorias_personalizadas,
    salvar_regras,
)
from modules.excel import formatar_tabela_excel
from modules.extratos import carregar_extrato_itau_upload, carregar_extrato_pagseguro_upload
from modules.gdrive import (
    delete_history_file,
    download_history_file,
    list_fechamentos_history_files,
    list_history_from_gdrive,
    load_fechamento_report_from_gdrive,
    upload_history_to_gdrive,
)
from modules.ui import inject_css, metric_card_html
from modules.utils import format_currency, get_ano_mes, normalizar_texto, parse_numero_br, slugify
from modules.validacao import exibir_painel_validacao, validar_consistencia_fechamento

# ========================
#  Config Streamlit
# ========================

st.set_page_config(page_title="Fechamento Tempero das Gurias", layout="wide")
inject_css()
check_auth()

st.markdown(
    '<div class="tempero-title">💗 Tempero das Gurias — Painel Financeiro</div>',
    unsafe_allow_html=True,
)
st.markdown(
    '<div class="tempero-subtitle">Fechamento mensal, caixa diário e histórico da loja em um único lugar.</div>',
    unsafe_allow_html=True,
)

# ========================
#  Barra lateral
# ========================

st.sidebar.header("Configurações do período")

arquivo_itau = st.sidebar.file_uploader(
    "Extrato Itaú (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="itau"
)
arquivo_pag = st.sidebar.file_uploader(
    "Extrato PagSeguro (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="pagseguro"
)

saldo_inicial_input = st.sidebar.text_input(
    "Saldo inicial consolidado do período (R$)", value="0"
)

default_periodo = datetime.today().strftime("%Y-%m") + " - período"
nome_periodo = st.sidebar.text_input(
    "Nome do período (para histórico)",
    value=default_periodo,
    help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
)

st.sidebar.markdown("---")
fonte_dados_global = st.sidebar.radio(
    "Fonte de dados",
    ["Upload (extratos do mês)", "Histórico (Drive)"],
    horizontal=False,
    key="fonte_dados_global",
)

# Seletor de relatório histórico na sidebar
if has_role("admin") and st.session_state.get("fonte_dados_global") == "Histórico (Drive)":
    st.sidebar.markdown("### Relatório do histórico")
    try:
        _arquivos_hist_sb = list_history_from_gdrive()
    except Exception as e:
        st.sidebar.error(f"Erro ao acessar Google Drive: {e}")
        _arquivos_hist_sb = []

    _fechamentos_sb = list_fechamentos_history_files(_arquivos_hist_sb)
    if not _fechamentos_sb:
        st.sidebar.info("Nenhum fechamento (fechamento_tempero_*.xlsx) no histórico.")
        for k in ("hist_report_loaded", "hist_report_name", "hist_loaded_file_id"):
            st.session_state.pop(k, None)
    else:
        _opcoes_sb = {f["name"]: f["id"] for f in _fechamentos_sb}
        _names_sb = list(_opcoes_sb.keys())

        _default_name = st.session_state.get("hist_selected_name_sidebar")
        if _default_name not in _opcoes_sb:
            _default_name = _names_sb[0]
        _idx = _names_sb.index(_default_name) if _default_name in _names_sb else 0

        hist_nome_sel = st.sidebar.selectbox(
            "Abrir fechamento (Drive)",
            options=_names_sb,
            index=_idx,
            key="hist_selected_name_sidebar",
        )
        _file_id_sel = _opcoes_sb.get(hist_nome_sel)

        if _file_id_sel and st.session_state.get("hist_loaded_file_id") != _file_id_sel:
            with st.spinner("Carregando relatório do histórico..."):
                try:
                    st.session_state["hist_report_loaded"] = load_fechamento_report_from_gdrive(_file_id_sel)
                    st.session_state["hist_report_name"] = hist_nome_sel
                    st.session_state["hist_loaded_file_id"] = _file_id_sel
                except Exception as e:
                    st.sidebar.error(f"Erro ao carregar relatório: {e}")
                    for k in ("hist_report_loaded", "hist_report_name", "hist_loaded_file_id"):
                        st.session_state.pop(k, None)

st.sidebar.markdown("---")
st.sidebar.markdown(f"Feito para a **Tempero das Gurias** 💕")
st.sidebar.caption(f"v{APP_VERSION}")

if st.session_state.get("auth_ok"):
    st.sidebar.markdown("---")
    st.sidebar.markdown(f"**Usuário:** {current_user()}  ")
    st.sidebar.markdown(f"**Perfil:** {current_role()}")
    if st.sidebar.button("Sair"):
        for k in ["auth_ok", "user", "role"]:
            st.session_state.pop(k, None)
        st.rerun()

# ========================
#  Livro-caixa mensal
# ========================

ano_mes_ref = get_ano_mes(nome_periodo) or datetime.today().strftime("%Y-%m")

# Quando o usuário visualiza um relatório histórico, o caixa deve refletir
# o mês daquele relatório, não o mês do campo "Nome do período".
_hist_nome = st.session_state.get("hist_report_name")
if st.session_state.get("fonte_dados_global") == "Histórico (Drive)" and _hist_nome:
    _ano_mes_caixa = get_ano_mes(_hist_nome) or ano_mes_ref
else:
    _ano_mes_caixa = ano_mes_ref

_cache_key = f"{st.session_state.get('fonte_dados_global','upload')}|{_ano_mes_caixa}"
if st.session_state.get("cash_loaded_for") != _cache_key:
    st.session_state["df_caixa_mes"] = load_cash_from_gdrive(_ano_mes_caixa)
    st.session_state["cash_loaded_for"] = _cache_key

df_dinheiro_periodo = st.session_state["df_caixa_mes"].copy()
if not df_dinheiro_periodo.empty and "Data" in df_dinheiro_periodo.columns:
    df_dinheiro_periodo["Data"] = pd.to_datetime(
        df_dinheiro_periodo["Data"], dayfirst=True, errors="coerce"
    )

# ========================
#  Cálculos principais (modo Upload)
# ========================

dados_carregados = False
mensagem_erro = None
avisos_validacao: list[str] = []

entradas_totais = saidas_totais = resultado_consolidado = 0.0
saldo_final = saldo_inicial = 0.0
ent_itau = sai_itau = res_itau = 0.0
ent_pag = sai_pag = res_pag = 0.0
entradas_dinheiro_periodo = saidas_dinheiro_periodo = saldo_dinheiro_periodo = 0.0

df_mov = pd.DataFrame()
df_cat_export = pd.DataFrame()
df_resumo_contas = pd.DataFrame()
df_consolidado = pd.DataFrame()
excel_buffer: BytesIO | None = None

if arquivo_itau and arquivo_pag:
    try:
        saldo_inicial = parse_numero_br(saldo_inicial_input)
    except Exception:
        mensagem_erro = "Saldo inicial inválido. Use formato 1234,56 ou 1234.56."
    else:
        try:
            regras = get_regras_sessao()

            ent_itau, sai_itau, res_itau, mov_itau = carregar_extrato_itau_upload(arquivo_itau)
            ent_pag, sai_pag, res_pag, mov_pag = carregar_extrato_pagseguro_upload(arquivo_pag)

            # Descobre meses presentes nos extratos
            meses_extratos: set[str] = set()
            for mov in mov_itau + mov_pag:
                d = mov.get("data")
                if not d:
                    continue
                dt = pd.to_datetime(d, dayfirst=True, errors="coerce")
                if not pd.isna(dt):
                    meses_extratos.add(dt.strftime("%Y-%m"))

            meses_extratos = sorted(meses_extratos)

            if not meses_extratos:
                raise RuntimeError(
                    "Não consegui identificar datas válidas nos extratos. "
                    "Verifique o arquivo exportado (coluna Data) e tente novamente."
                )
            if len(meses_extratos) != 1:
                raise RuntimeError(
                    f"Extratos parecem conter múltiplos meses: {', '.join(meses_extratos)}. "
                    "Regra do sistema: sempre fechar mês cheio (um único YYYY-MM)."
                )

            mes_extrato = meses_extratos[0]
            if ano_mes_ref and mes_extrato != ano_mes_ref:
                raise RuntimeError(
                    f"Período selecionado: {ano_mes_ref}, mas os extratos são de: {mes_extrato}. "
                    "Ajuste o Nome do período (iniciando com YYYY-MM) ou envie os extratos corretos."
                )

            # Caixa em dinheiro do mesmo mês
            df_dinheiro_periodo_fechar = load_cash_from_gdrive(mes_extrato)

            df_din_validos = df_dinheiro_periodo_fechar.copy()
            if not df_din_validos.empty and "Valor" in df_din_validos.columns:
                df_din_validos = df_din_validos[df_din_validos["Valor"] > 0]

            entradas_dinheiro_periodo = df_din_validos.loc[df_din_validos["Tipo"] == "Entrada", "Valor"].sum()
            saidas_dinheiro_periodo = df_din_validos.loc[df_din_validos["Tipo"] == "Saída", "Valor"].sum()
            saldo_dinheiro_periodo = entradas_dinheiro_periodo - saidas_dinheiro_periodo

            # Consolidado
            entradas_totais = ent_itau + ent_pag + entradas_dinheiro_periodo
            saidas_totais = sai_itau + sai_pag - saidas_dinheiro_periodo
            resultado_consolidado = entradas_totais + saidas_totais
            saldo_final = saldo_inicial + resultado_consolidado

            # Monta lista de movimentos
            movimentos = mov_itau + mov_pag
            if not df_din_validos.empty:
                for _, linha in df_din_validos.iterrows():
                    valor = float(linha.get("Valor", 0.0) or 0.0)
                    if str(linha.get("Tipo", "")) == "Saída":
                        valor = -valor
                    movimentos.append({
                        "data": linha.get("Data"),
                        "descricao": linha.get("Descrição"),
                        "valor": valor,
                        "conta": "Dinheiro",
                    })

            movimentos_cat = [
                {
                    "Data": mov.get("data"),
                    "Conta": mov.get("conta"),
                    "Descrição": mov.get("descricao"),
                    "Categoria": classificar_categoria(mov, regras),
                    "Valor": mov.get("valor", 0.0),
                }
                for mov in movimentos
            ]
            df_mov = pd.DataFrame(movimentos_cat)
            if not df_mov.empty and "Data" in df_mov.columns:
                df_mov["Data"] = pd.to_datetime(df_mov["Data"], dayfirst=True, errors="coerce")

            entradas_cat: dict[str, float] = defaultdict(float)
            saidas_cat: dict[str, float] = defaultdict(float)
            for _, row in df_mov.iterrows():
                cat, v = row["Categoria"], row["Valor"]
                if v > 0:
                    entradas_cat[cat] += v
                elif v < 0:
                    saidas_cat[cat] += v

            categorias_calc = sorted(set(entradas_cat) | set(saidas_cat))
            df_cat_export = pd.DataFrame([
                {"Categoria": cat, "Entradas": entradas_cat.get(cat, 0.0), "Saídas": saidas_cat.get(cat, 0.0)}
                for cat in categorias_calc
            ])

            df_resumo_contas = pd.DataFrame([
                {"Conta": "Itaú", "Entradas": ent_itau, "Saídas": sai_itau, "Resultado": res_itau},
                {"Conta": "PagSeguro", "Entradas": ent_pag, "Saídas": sai_pag, "Resultado": res_pag},
                {
                    "Conta": "Dinheiro",
                    "Entradas": entradas_dinheiro_periodo,
                    "Saídas": -saidas_dinheiro_periodo,
                    "Resultado": saldo_dinheiro_periodo,
                },
            ])
            df_consolidado = pd.DataFrame([{
                "Nome do período": nome_periodo,
                "Entradas totais": entradas_totais,
                "Saídas totais": saidas_totais,
                "Resultado do período": resultado_consolidado,
                "Saldo inicial": saldo_inicial,
                "Saldo final": saldo_final,
            }])

            avisos_validacao = validar_consistencia_fechamento(
                df_mov, df_resumo_contas, df_consolidado, saldo_inicial
            )

            # Gera Excel
            buffer = BytesIO()
            with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                start_row_resumo = 3
                df_resumo_contas.to_excel(writer, sheet_name="Resumo", index=False, startrow=start_row_resumo)

                start_row_consol = start_row_resumo + len(df_resumo_contas) + 3
                df_consolidado.to_excel(writer, sheet_name="Resumo", index=False, startrow=start_row_consol)

                df_consolidado.to_excel(writer, sheet_name="ResumoDados", index=False)
                df_cat_export.to_excel(writer, sheet_name="Categorias", index=False, startrow=1)
                df_mov.to_excel(writer, sheet_name="Movimentos", index=False, startrow=1)
                df_dinheiro_periodo_fechar.to_excel(writer, sheet_name="Dinheiro", index=False, startrow=1)

                ws_res = writer.sheets["Resumo"]
                ws_cat = writer.sheets["Categorias"]
                ws_mov = writer.sheets["Movimentos"]
                ws_din = writer.sheets["Dinheiro"]

                ws_res["A1"] = f"Fechamento Tempero das Gurias - {nome_periodo}"
                ws_res["A1"].font = Font(bold=True, size=14)
                ws_res["A1"].alignment = Alignment(horizontal="left")

                formatar_tabela_excel(ws_res, df_resumo_contas, start_row=start_row_resumo)
                formatar_tabela_excel(ws_res, df_consolidado, start_row=start_row_consol)
                if not df_cat_export.empty:
                    formatar_tabela_excel(ws_cat, df_cat_export, start_row=1)
                if not df_mov.empty:
                    formatar_tabela_excel(ws_mov, df_mov, start_row=1)
                if not df_dinheiro_periodo_fechar.empty:
                    formatar_tabela_excel(ws_din, df_dinheiro_periodo_fechar, start_row=1)

            buffer.seek(0)
            excel_buffer = buffer
            dados_carregados = True

        except RuntimeError as e:
            mensagem_erro = str(e)


# ========================
#  Abas
# ========================

tab1, tab2, tab3, tab4 = st.tabs([
    "💵 Caixa Diário",
    "💗 Fechamento Mensal",
    "🧾 Conferência & Categorias",
    "📊 Histórico & Comparativos",
])


# ---------- ABA 1: Caixa Diário ----------

with tab1:
    st.markdown('<div class="tempero-section-title">💵 Caixa diário em dinheiro</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="tempero-section-sub">'
        "Registre aqui as entradas e saídas em dinheiro. "
        "Esses lançamentos são salvos no Google Drive e usados nos fechamentos mensais."
        "</div>",
        unsafe_allow_html=True,
    )

    if df_dinheiro_periodo.empty:
        df_dinheiro_periodo = pd.DataFrame(
            [{"Data": pd.Timestamp.today().normalize(), "Descrição": "", "Tipo": "Entrada", "Valor": 0.0}],
            columns=["Data", "Descrição", "Tipo", "Valor"],
        )

    df_dinheiro_periodo["Data"] = pd.to_datetime(df_dinheiro_periodo["Data"], errors="coerce")

    df_dinheiro_ui = st.data_editor(
        df_dinheiro_periodo,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "Data": st.column_config.DateColumn("Data"),
            "Descrição": st.column_config.TextColumn("Descrição"),
            "Tipo": st.column_config.SelectboxColumn("Tipo", options=["Entrada", "Saída"], required=True),
            "Valor": st.column_config.NumberColumn("Valor (R$)", step=0.01, min_value=0.0),
        },
        key=f"editor_dinheiro_{_ano_mes_caixa or 'padrao'}",
    )

    df_din_limpo = df_dinheiro_ui.copy()
    if not df_din_limpo.empty:
        df_din_limpo = df_din_limpo[
            ~((df_din_limpo["Valor"].fillna(0) == 0) & (df_din_limpo["Descrição"].fillna("").str.strip() == ""))
        ]

    col_btn1, _ = st.columns([1, 3])
    with col_btn1:
        salvar_caixa = st.button("Salvar lançamentos de dinheiro")

    if salvar_caixa:
        try:
            save_cash_to_gdrive(_ano_mes_caixa, df_din_limpo)
            _df_save = df_din_limpo.copy()
            if not _df_save.empty and "Data" in _df_save.columns:
                _df_save["Data"] = pd.to_datetime(_df_save["Data"], errors="coerce")
            st.session_state["df_caixa_mes"] = _df_save
            st.session_state["cash_loaded_for"] = _cache_key
            st.success("Lançamentos de dinheiro salvos com sucesso no Google Drive!")
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao salvar caixa diário no Drive: {e}")

    # ---- Importação do Gmail (Suitable) ----
    with st.expander("📥 Importar do Gmail (Suitable)"):
        st.markdown(
            "Busca os emails de **Fechamento de caixa** do Suitable "
            f"para o período **{_ano_mes_caixa}** e importa os lançamentos em dinheiro."
        )

        if st.button("🔍 Buscar fechamentos do Gmail", key="btn_buscar_gmail"):
            with st.spinner("Buscando emails..."):
                try:
                    preview = buscar_fechamentos_gmail(_ano_mes_caixa)
                    st.session_state["gmail_preview"] = preview
                except RuntimeError as e:
                    st.error(str(e))
                    st.session_state.pop("gmail_preview", None)
                except Exception as e:
                    st.error(f"Erro inesperado ao buscar emails: {e}")
                    st.session_state.pop("gmail_preview", None)

        preview = st.session_state.get("gmail_preview")
        if preview is not None:
            if not preview:
                st.info(f"Nenhum lançamento encontrado nos emails de {_ano_mes_caixa}.")
            else:
                st.success(f"**{len(preview)} lançamento(s)** encontrado(s) para {_ano_mes_caixa}:")

                df_prev = pd.DataFrame(preview)
                df_prev["Data"] = pd.to_datetime(df_prev["Data"]).dt.strftime("%d/%m/%Y")
                df_prev["Valor"] = df_prev["Valor"].apply(format_currency)
                st.dataframe(df_prev[["Data", "Descrição", "Tipo", "Valor"]], use_container_width=True, hide_index=True)

                if st.button("✅ Confirmar importação", key="btn_confirmar_gmail"):
                    with st.spinner("Importando..."):
                        try:
                            inseridos, duplicatas = lancar_importados_gmail(
                                _ano_mes_caixa, st.session_state["gmail_preview"]
                            )
                            # Recarrega caixa na sessão
                            st.session_state.pop("df_caixa_mes", None)
                            st.session_state.pop("cash_loaded_for", None)
                            st.session_state.pop("gmail_preview", None)
                            msg = f"✅ **{inseridos}** lançamento(s) importado(s)."
                            if duplicatas:
                                msg += f" {duplicatas} duplicata(s) ignorada(s)."
                            st.success(msg)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao importar lançamentos: {e}")

    df_din_calc = df_din_limpo.copy()
    if not df_din_calc.empty and "Valor" in df_din_calc.columns:
        df_din_calc = df_din_calc[df_din_calc["Valor"] > 0]

    entradas_d = df_din_calc.loc[df_din_calc["Tipo"] == "Entrada", "Valor"].sum()
    saidas_d = df_din_calc.loc[df_din_calc["Tipo"] == "Saída", "Valor"].sum()

    st.markdown("---")
    col_c1, col_c2, col_c3 = st.columns(3)
    with col_c1:
        st.write("Entradas em dinheiro no período:", format_currency(entradas_d))
    with col_c2:
        st.write("Saídas em dinheiro no período:", format_currency(-saidas_d) if saidas_d else "R$ 0,00")
    with col_c3:
        st.write("Saldo do dinheiro no período:", format_currency(entradas_d - saidas_d))


# ---------- ABA 2: Fechamento Mensal ----------

with tab2:
    require_role("admin")

    st.markdown('<div class="tempero-section-title">Resumo do período</div>', unsafe_allow_html=True)
    fonte_tab2 = st.session_state.get("fonte_dados_global", "Upload (extratos do mês)")

    if fonte_tab2 == "Histórico (Drive)":
        rep = st.session_state.get("hist_report_loaded")
        nome_rep = st.session_state.get("hist_report_name")

        if not rep:
            st.info("Selecione um relatório do histórico na barra lateral para carregar.")
        else:
            df_consol_h = rep.get("consolidado", pd.DataFrame())
            df_res_contas_h = rep.get("resumo_contas", pd.DataFrame())
            df_cat_h = rep.get("categorias", pd.DataFrame())

            if df_consol_h.empty:
                st.warning("Não consegui ler a aba **ResumoDados** deste relatório. Ele pode ser muito antigo.")
            else:
                linha = df_consol_h.iloc[0]
                ent_h = float(linha.get("Entradas totais", 0.0) or 0.0)
                sai_h = float(linha.get("Saídas totais", 0.0) or 0.0)
                res_h = float(linha.get("Resultado do período", 0.0) or 0.0)
                si_h = float(linha.get("Saldo inicial", 0.0) or 0.0)
                sf_h = float(linha.get("Saldo final", 0.0) or 0.0)

                st.markdown("---")
                m1, m2, m3 = st.columns(3)
                with m1:
                    st.markdown(metric_card_html("Entradas totais", format_currency(ent_h)), unsafe_allow_html=True)
                with m2:
                    st.markdown(metric_card_html("Saídas totais", format_currency(sai_h)), unsafe_allow_html=True)
                with m3:
                    st.markdown(metric_card_html("Resultado do período", format_currency(res_h)), unsafe_allow_html=True)

                st.markdown("---")
                st.markdown('<div class="tempero-section-title">🏁 Consolidado da loja</div>', unsafe_allow_html=True)
                st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                st.write("Saldo inicial:", format_currency(si_h))
                st.write("Saldo final  :", format_currency(sf_h))
                st.markdown("</div>", unsafe_allow_html=True)

            st.markdown('<div class="tempero-section-title">📑 Resumo por conta (do relatório)</div>', unsafe_allow_html=True)
            if df_res_contas_h.empty:
                st.info("Não foi possível extrair o resumo por conta da aba **Resumo**.")
            else:
                df_show = df_res_contas_h.copy()
                for col in ["Entradas", "Saídas", "Resultado"]:
                    if col in df_show.columns:
                        df_show[col] = df_show[col].apply(
                            lambda x: format_currency(float(x)) if pd.notna(x) else "-"
                        )
                st.dataframe(df_show, use_container_width=True)

            st.markdown('<div class="tempero-section-title">📌 Resumo por categoria (do relatório)</div>', unsafe_allow_html=True)
            if df_cat_h.empty:
                st.info("Este relatório não possui a aba **Categorias**.")
            else:
                df_cat_disp = df_cat_h.copy()
                for col in ["Entradas", "Saídas"]:
                    if col in df_cat_disp.columns:
                        df_cat_disp[col] = df_cat_disp[col].apply(
                            lambda x: format_currency(float(x)) if pd.notna(x) else "-"
                        )
                st.dataframe(df_cat_disp, use_container_width=True)

        st.markdown("---")
        st.caption("Fonte: Histórico (Drive) — visualização somente leitura")
        if nome_rep:
            st.caption(f"Relatório carregado: {nome_rep}")

    else:
        if mensagem_erro:
            st.error(mensagem_erro)
        elif not dados_carregados:
            st.info("Envie os arquivos do Itaú e PagSeguro na barra lateral para ver o fechamento.")
        else:
            if avisos_validacao:
                exibir_painel_validacao(avisos_validacao)

            st.markdown("---")
            m1, m2, m3 = st.columns(3)
            with m1:
                st.markdown(metric_card_html("Entradas totais", format_currency(entradas_totais)), unsafe_allow_html=True)
            with m2:
                st.markdown(metric_card_html("Saídas totais", format_currency(saidas_totais)), unsafe_allow_html=True)
            with m3:
                st.markdown(metric_card_html("Resultado do período", format_currency(resultado_consolidado)), unsafe_allow_html=True)

            st.markdown("---")
            st.markdown('<div class="tempero-section-title">📑 Resumo por conta</div>', unsafe_allow_html=True)
            col_a, col_b, col_c = st.columns(3)
            for col_ui, label, ent, sai, res in [
                (col_a, "Itaú", ent_itau, sai_itau, res_itau),
                (col_b, "PagSeguro", ent_pag, sai_pag, res_pag),
                (col_c, "Dinheiro (caixa físico)", entradas_dinheiro_periodo, -saidas_dinheiro_periodo, saldo_dinheiro_periodo),
            ]:
                with col_ui:
                    st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
                    st.markdown(f"**{label}**")
                    st.write("Entradas:", format_currency(ent))
                    st.write("Saídas  :", format_currency(sai))
                    st.write("Resultado:", format_currency(res))
                    if label.startswith("Dinheiro"):
                        st.caption("Edite os lançamentos na aba 💵 Caixa Diário.")
                    st.markdown("</div>", unsafe_allow_html=True)

            st.markdown("---")
            st.markdown('<div class="tempero-section-title">🏁 Consolidado da loja</div>', unsafe_allow_html=True)
            st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
            st.write("Saldo inicial:", format_currency(saldo_inicial))
            st.write("Saldo final  :", format_currency(saldo_final))
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown('<div class="tempero-section-title">📌 Resumo por categoria</div>', unsafe_allow_html=True)
            st.markdown(
                '<div class="tempero-section-sub">Baseado nas categorias atuais (já considera regras salvas anteriormente).</div>',
                unsafe_allow_html=True,
            )
            df_cat_display = df_cat_export.copy()
            if not df_cat_display.empty:
                df_cat_display["Entradas"] = df_cat_display["Entradas"].map(format_currency)
                df_cat_display["Saídas"] = df_cat_display["Saídas"].map(format_currency)
            st.dataframe(df_cat_display, use_container_width=True)

            st.markdown('<div class="tempero-section-title">📥 Relatório do período atual</div>', unsafe_allow_html=True)
            st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
            col_dl1, col_dl2 = st.columns(2)
            with col_dl1:
                st.download_button(
                    label="Baixar relatório Excel (período atual)",
                    data=excel_buffer,
                    file_name="fechamento_tempero.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            with col_dl2:
                salvar = st.button("Salvar no histórico")

            if salvar:
                slug = slugify(nome_periodo)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"fechamento_tempero_{slug}_{timestamp}.xlsx"
                try:
                    upload_history_to_gdrive(excel_buffer, filename)
                    st.success(f"Relatório salvo no histórico (Google Drive) como: {filename}")
                except Exception as e:
                    st.error(f"Erro ao salvar no Google Drive: {e}")
            st.markdown("</div>", unsafe_allow_html=True)


# ---------- ABA 3: Conferência & Categorias ----------

with tab3:
    require_role("admin")

    st.markdown('<div class="tempero-section-title">🧾 Conferência de lançamentos e categorias</div>', unsafe_allow_html=True)
    fonte_tab3 = st.session_state.get("fonte_dados_global", "Upload (extratos do mês)")

    if fonte_tab3 == "Histórico (Drive)":
        rep = st.session_state.get("hist_report_loaded")
        nome_rep = st.session_state.get("hist_report_name")

        if not rep:
            st.info("Selecione um relatório do histórico na barra lateral para carregar.")
        else:
            df_mov_h = rep.get("movimentos", pd.DataFrame())
            df_cat_h = rep.get("categorias", pd.DataFrame())

            st.markdown("---")
            st.markdown("**Categorias (do relatório)**")
            if df_cat_h.empty:
                st.info("Este relatório não possui a aba **Categorias**.")
            else:
                df_cat_disp = df_cat_h.copy()
                for col in ["Entradas", "Saídas"]:
                    if col in df_cat_disp.columns:
                        df_cat_disp[col] = df_cat_disp[col].apply(
                            lambda x: format_currency(float(x)) if pd.notna(x) else "-"
                        )
                st.dataframe(df_cat_disp, use_container_width=True)

            st.markdown("---")
            st.markdown("**Movimentos (do relatório)**")
            if df_mov_h.empty:
                st.info("Este relatório não possui a aba **Movimentos**.")
            else:
                df_mov_h_display = df_mov_h.copy()
                if "Data" in df_mov_h_display.columns:
                    df_mov_h_display["Data"] = (
                        pd.to_datetime(df_mov_h_display["Data"], dayfirst=True, errors="coerce")
                        .dt.strftime("%d/%m/%Y")
                        .fillna("")
                    )
                st.dataframe(df_mov_h_display, use_container_width=True)

        st.markdown("---")
        st.caption("Fonte: Histórico (Drive) — visualização somente leitura")
        if nome_rep:
            st.caption(f"Relatório carregado: {nome_rep}")

    else:
        if not dados_carregados:
            st.info("Envie os arquivos do Itaú e PagSeguro na barra lateral para conferir as categorias.")
        else:
            st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
            st.markdown("**Gerenciar categorias**")

            categorias_custom = carregar_categorias_personalizadas()
            categorias_possiveis = CATEGORIAS_PADRAO + categorias_custom

            col_nc1, col_nc2 = st.columns([2, 1])
            with col_nc1:
                nova_cat = st.text_input("Criar nova categoria:")
            with col_nc2:
                if st.button("Adicionar categoria"):
                    nova_cat = nova_cat.strip()
                    if nova_cat and nova_cat not in categorias_possiveis:
                        categorias_custom.append(nova_cat)
                        salvar_categorias_personalizadas(categorias_custom)
                        st.success(f"Categoria '{nova_cat}' criada com sucesso!")
                        st.rerun()
                    elif nova_cat:
                        st.warning("Essa categoria já existe.")
            st.markdown("</div>", unsafe_allow_html=True)

            st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
            st.markdown("**Conferência de lançamentos**")
            st.markdown(
                '<div class="tempero-section-sub">Ajuste as categorias linha a linha, se necessário. '
                "Ao salvar as regras, o sistema aprende para os próximos fechamentos.</div>",
                unsafe_allow_html=True,
            )

            edited_df = st.data_editor(
                df_mov,
                key="editor_movimentos",
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Data": st.column_config.DateColumn("Data"),
                    "Categoria": st.column_config.SelectboxColumn(
                        "Categoria", options=categorias_possiveis, help="Ajuste a categoria se necessário."
                    ),
                },
            )

            if st.button("Salvar regras de categorização"):
                regras = carregar_regras()
                alteracoes = 0
                for _, row in edited_df.iterrows():
                    desc = row.get("Descrição")
                    cat = row.get("Categoria")
                    if not desc or not cat:
                        continue
                    desc_norm = normalizar_texto(desc)
                    if regras.get(desc_norm) != cat:
                        regras[desc_norm] = cat
                        alteracoes += 1
                salvar_regras(regras)
                reload_regras_sessao()
                st.success(
                    f"{alteracoes} regra(s) de categorização salva(s). "
                    "Os próximos fechamentos já virão com essas categorias aplicadas."
                )
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)


# ---------- ABA 4: Histórico & Comparativos ----------

with tab4:
    require_role("admin")

    st.markdown('<div class="tempero-section-title">📊 Histórico de fechamentos e comparativo</div>', unsafe_allow_html=True)

    try:
        arquivos = list_history_from_gdrive()
    except Exception as e:
        st.error(f"Erro ao acessar Google Drive: {e}")
        arquivos = []

    if not arquivos:
        st.write("Nenhum fechamento salvo ainda.")
    else:
        st.markdown("**Comparativo entre períodos (Histórico Analítico)**")
        st.markdown(
            '<div class="tempero-section-sub">Baseado nos relatórios salvos no histórico (Google Drive).</div>',
            unsafe_allow_html=True,
        )

        resumos = []
        for file_info in arquivos:
            if not str(file_info.get("name", "")).startswith("fechamento_tempero_"):
                continue
            try:
                buf = download_history_file(file_info["id"])
                try:
                    df_consol = pd.read_excel(buf, sheet_name="ResumoDados")
                except Exception:
                    buf.seek(0)
                    df_res = pd.read_excel(buf, sheet_name="Resumo")
                    if "Nome do período" not in df_res.columns:
                        continue
                    df_consol = df_res[df_res["Nome do período"].notna()]
                    if df_consol.empty:
                        continue

                linha = df_consol.iloc[0]
                saldo_final_val = linha.get("Saldo final")
                resumos.append({
                    "Período": str(linha.get("Nome do período", file_info["name"])),
                    "Entradas": float(linha.get("Entradas totais", 0.0)),
                    "Saídas": float(linha.get("Saídas totais", 0.0)),
                    "Resultado": float(linha.get("Resultado do período", 0.0)),
                    "Saldo final": float(saldo_final_val) if saldo_final_val is not None else None,
                })
            except Exception:
                continue

        if not resumos:
            st.info("Ainda não foi possível montar o comparativo. Gere e salve alguns fechamentos no novo formato.")
        else:
            df_hist = pd.DataFrame(resumos).iloc[::-1].reset_index(drop=True)

            df_display = df_hist.copy()
            for col in ["Entradas", "Saídas", "Resultado", "Saldo final"]:
                if col in df_display.columns:
                    df_display[col] = df_display[col].apply(
                        lambda x: format_currency(x) if pd.notna(x) else "-"
                    )
            st.dataframe(df_display, use_container_width=True)

            st.markdown("**Resultado por período:**")

            _MESES_PT_CHART = {
                "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
                "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
                "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
            }

            def _periodo_to_dt(periodo):
                s = str(periodo).strip().lower()
                m = re.search(r"(\d{4})-(\d{2})", s)
                if m:
                    y, mm = int(m.group(1)), int(m.group(2))
                    if 1 <= mm <= 12:
                        return pd.Timestamp(y, mm, 1)
                for nome_mes, num_mes in _MESES_PT_CHART.items():
                    if s.startswith(nome_mes):
                        y_match = re.search(r"(\d{4})", s)
                        if y_match:
                            return pd.Timestamp(int(y_match.group(1)), num_mes, 1)
                m2 = re.search(r"(\d{2})/(\d{4})", s)
                if m2:
                    mm, y = int(m2.group(1)), int(m2.group(2))
                    if 1 <= mm <= 12:
                        return pd.Timestamp(y, mm, 1)
                return pd.NaT

            df_chart = df_hist.copy()
            df_chart["ordem"] = df_chart["Período"].apply(_periodo_to_dt)
            if df_chart["ordem"].notna().any():
                df_chart = df_chart.dropna(subset=["ordem"]).sort_values("ordem")
            period_order = df_chart["Período"].tolist()

            chart = (
                alt.Chart(df_chart)
                .mark_bar()
                .encode(
                    x=alt.X("Período:N", sort=period_order, title=None),
                    y=alt.Y("Resultado:Q", title=None),
                    tooltip=[alt.Tooltip("Período:N"), alt.Tooltip("Resultado:Q", format=",.2f")],
                )
                .properties(height=320)
            )
            st.altair_chart(chart, use_container_width=True)

        st.markdown("---")
        st.markdown("**Fechamentos salvos**")
        st.markdown('<div class="tempero-card">', unsafe_allow_html=True)

        for file_info in arquivos:
            file_id = file_info["id"]
            nome = file_info["name"]
            mod_raw = file_info.get("modifiedTime", "")

            try:
                dt = datetime.fromisoformat(mod_raw.replace("Z", "+00:00"))
                data_mod = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                data_mod = mod_raw

            col_a, col_b, col_c = st.columns([5, 1, 1])

            with col_a:
                st.write(f"📄 **{nome}**")
                st.caption(f"salvo em {data_mod}")

            with col_b:
                try:
                    buf = download_history_file(file_id)
                    st.download_button(
                        label="Baixar",
                        data=buf.getvalue(),
                        file_name=nome,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"baixar_{file_id}",
                    )
                except Exception as e:
                    st.error(f"Erro ao baixar {nome}: {e}")

            with col_c:
                confirm_key = f"confirmar_excluir_{file_id}"
                if not st.session_state.get(confirm_key):
                    if st.button("Excluir", key=f"excluir_{file_id}"):
                        st.session_state[confirm_key] = True
                        st.rerun()
                else:
                    if st.button("⚠️ Confirmar", key=f"conf_{file_id}"):
                        try:
                            delete_history_file(file_id)
                            st.session_state.pop(confirm_key, None)
                            st.success(f"Arquivo **{nome}** excluído.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao excluir {nome}: {e}")
                    if st.button("Cancelar", key=f"cancel_{file_id}"):
                        st.session_state.pop(confirm_key, None)
                        st.rerun()

        st.markdown("</div>", unsafe_allow_html=True)
