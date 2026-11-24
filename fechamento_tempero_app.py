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
from modules.gmail_suitable import buscar_fechamentos_gmail_com_info
from modules.categorias import (
    CATEGORIAS_PADRAO,
    carregar_categorias_personalizadas,
    carregar_regras,
    chave_contraparte,
    chave_reclassificacao,
    classificar_categoria,
    get_reclassificacoes_sessao,
    get_regras_sessao,
    reload_reclassificacoes_sessao,
    reload_regras_sessao,
    salvar_categorias_personalizadas,
    salvar_reclassificacoes,
    salvar_regras,
)
from modules.excel import formatar_tabela_excel, gerar_excel_fechamento
from modules.extratos import carregar_extrato_itau_upload, carregar_extrato_pagseguro_upload
from modules.gdrive import (
    delete_history_file,
    download_history_file,
    trash_history_file,
    list_fechamentos_history_files,
    list_history_from_gdrive,
    load_fechamento_report_from_gdrive,
    upload_history_to_gdrive,
)
from modules.ui import inject_css, metric_card_html
from modules.utils import format_currency, get_ano_mes, normalizar_texto, parse_numero_br, slugify
from modules.validacao import exibir_painel_validacao, validar_consistencia_fechamento
from modules.controle_anual import carregar_dre_anual, gerar_alertas, selecionar_versoes_recentes
from modules.conferencia import TIPOS as _TIPOS_CONF, carregar_relatorios_recentes, conferir, resumo_por_tipo
from modules.historico_edicao import aplicar_ajustes_e_gerar_excel
from modules.canais import CANAIS_ORDEM, receita_por_canal_dict, tabela_canais
from modules.gerencial import (
    ponte_por_categoria,
    ponte_por_movimentos,
    tabela_ponte,
    cmv_mensal_e_media_movel,
)

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

# Saldo inicial consolidado removido da barra lateral: o saldo inicial do caixa em
# dinheiro é lançado na grade do Caixa Diário (linha "Saldo inicial"). Mantido como 0
# internamente só para compatibilidade com as colunas do Excel/histórico.
saldo_inicial_input = "0"

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

def _eh_saldo_inicial(descricao) -> bool:
    """Linha manual de saldo inicial do caixa (não é receita do mês)."""
    return str(descricao or "").strip().lower().startswith("saldo inicial")


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
df_gerencial = pd.DataFrame()
df_canais = pd.DataFrame()
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
            # Saldo inicial do caixa não é receita: fica na aba "Dinheiro" (auditoria),
            # mas não entra em entradas, resultado nem movimentos do fechamento.
            if not df_din_validos.empty and "Descrição" in df_din_validos.columns:
                df_din_validos = df_din_validos[
                    ~df_din_validos["Descrição"].apply(_eh_saldo_inicial)
                ]

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

            # Visão gerencial: abre o resultado em operação / retiradas / aplicações
            df_gerencial = tabela_ponte(ponte_por_movimentos(df_mov))
            df_canais = tabela_canais(receita_por_canal_dict(df_mov))

            avisos_validacao = validar_consistencia_fechamento(
                df_mov, df_resumo_contas, df_consolidado, saldo_inicial
            )

            # Gera Excel
            excel_buffer = gerar_excel_fechamento(
                nome_periodo,
                df_resumo_contas,
                df_consolidado,
                df_cat_export,
                df_mov,
                df_dinheiro_periodo_fechar,
                df_gerencial,
                df_canais,
            )
            dados_carregados = True

        except RuntimeError as e:
            mensagem_erro = str(e)


def _render_gerencial(df_ger: pd.DataFrame):
    """Mostra a ponte: resultado operacional → retiradas → aplicações → variação de caixa."""
    if df_ger is None or df_ger.empty:
        return
    st.markdown('<div class="tempero-section-title">📈 Visão gerencial</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="tempero-section-sub">O "Resultado do período" é a variação de caixa: '
        "mistura a operação com retiradas dos sócios e aplicações/resgates. "
        "Aqui ele é aberto em partes.</div>",
        unsafe_allow_html=True,
    )
    _show = df_ger.copy()
    _show["Valor"] = _show["Valor"].map(format_currency)
    _show["% da receita"] = _show["% da receita"].map(
        lambda x: "" if pd.isna(x) else f"{x * 100:.1f}%".replace(".", ",")
    )
    st.dataframe(_show, use_container_width=True, hide_index=True)
    st.caption(
        "Retiradas = Transferência Interna / Sócios + Sangria. Receita = recebimentos no período "
        "(iFood e cartão já chegam líquidos de comissão/taxa)."
    )


def _render_canais(df_can: pd.DataFrame):
    """Receita por canal (cartão, iFood, PIX, vale, dinheiro...)."""
    if df_can is None or df_can.empty:
        return
    st.markdown('<div class="tempero-section-title">🧭 Receita por canal</div>', unsafe_allow_html=True)
    _show = df_can.copy()
    _show["Valor"] = _show["Valor"].map(format_currency)
    _show["% da receita"] = _show["% da receita"].map(
        lambda x: "" if pd.isna(x) else f"{x * 100:.1f}%".replace(".", ",")
    )
    st.dataframe(_show, use_container_width=True, hide_index=True)
    st.caption(
        "O canal é identificado pelo texto do lançamento no extrato. "
        "\"TED (origem a confirmar)\" e \"Outros\" são recebimentos que não deu para identificar."
    )


# ========================
#  Abas
# ========================

tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "💵 Caixa Diário",
    "💗 Fechamento Mensal",
    "🧾 Conferência & Categorias",
    "📊 Histórico & Comparativos",
    "📅 Controle Anual",
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
            f"para o período **{_ano_mes_caixa}** e importa os lançamentos em dinheiro. "
            "Se um dia já foi importado, as linhas \"(Suitable)\" daquele dia são substituídas pelas do email; "
            "lançamentos manuais (ex.: Saldo inicial) não são alterados."
        )

        if st.button("🔍 Buscar fechamentos do Gmail", key="btn_buscar_gmail"):
            with st.spinner("Buscando emails..."):
                try:
                    preview, info_gmail = buscar_fechamentos_gmail_com_info(_ano_mes_caixa)
                    st.session_state["gmail_preview"] = preview
                    st.session_state["gmail_info"] = info_gmail
                except RuntimeError as e:
                    st.error(str(e))
                    st.session_state.pop("gmail_preview", None)
                except Exception as e:
                    st.error(f"Erro inesperado ao buscar emails: {e}")
                    st.session_state.pop("gmail_preview", None)

        preview = st.session_state.get("gmail_preview")
        for _aviso in (st.session_state.get("gmail_info") or {}).get("avisos", []):
            st.warning(_aviso)
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
                            inseridos, substituidos = lancar_importados_gmail(
                                _ano_mes_caixa, st.session_state["gmail_preview"]
                            )
                            # Recarrega caixa na sessão
                            st.session_state.pop("df_caixa_mes", None)
                            st.session_state.pop("cash_loaded_for", None)
                            st.session_state.pop("gmail_preview", None)
                            msg = f"✅ **{inseridos}** lançamento(s) do Suitable importado(s)."
                            if substituidos:
                                msg += (
                                    f" {substituidos} linha(s) \"(Suitable)\" que já estavam nesses dias "
                                    "foram substituídas pelas do email; lançamentos manuais foram preservados."
                                )
                            st.success(msg)
                            st.rerun()
                        except Exception as e:
                            st.error(f"Erro ao importar lançamentos: {e}")

    df_din_calc = df_din_limpo.copy()
    if not df_din_calc.empty and "Valor" in df_din_calc.columns:
        df_din_calc = df_din_calc[df_din_calc["Valor"] > 0]

    if not df_din_calc.empty and "Descrição" in df_din_calc.columns:
        _mask_si = df_din_calc["Descrição"].apply(_eh_saldo_inicial)
    else:
        _mask_si = pd.Series(False, index=df_din_calc.index)
    saldo_ini_d = df_din_calc.loc[_mask_si & (df_din_calc["Tipo"] == "Entrada"), "Valor"].sum()
    df_din_calc = df_din_calc[~_mask_si]

    entradas_d = df_din_calc.loc[df_din_calc["Tipo"] == "Entrada", "Valor"].sum()
    saidas_d = df_din_calc.loc[df_din_calc["Tipo"] == "Saída", "Valor"].sum()

    st.markdown("---")
    # Saldo em caixa = saldo inicial (linha "Saldo inicial") + entradas − saídas do período.
    # Deve bater com a linha "Dinheiro" de "Saldos de Fechamento" do email do Suitable.
    _saldo_caixa = saldo_ini_d + entradas_d - saidas_d
    _ult = (st.session_state.get("gmail_info") or {}).get("ultimo_saldo")

    _saidas_txt = format_currency(-saidas_d) if saidas_d else "R$ 0,00"
    if saldo_ini_d:
        col_c0, col_c1, col_c2, col_c3 = st.columns(4)
        with col_c0:
            st.write("Saldo inicial:", format_currency(saldo_ini_d))
        with col_c1:
            st.write("Entradas:", format_currency(entradas_d))
        with col_c2:
            st.write("Saídas:", _saidas_txt)
        with col_c3:
            st.write("**Saldo em caixa:**", f"**{format_currency(_saldo_caixa)}**")
    else:
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            st.write("Entradas em dinheiro no período:", format_currency(entradas_d))
        with col_c2:
            st.write("Saídas em dinheiro no período:", _saidas_txt)
        with col_c3:
            st.write("Saldo do dinheiro no período:", format_currency(entradas_d - saidas_d))

    if _ult:
        _dt_ult, _saldo_gaveta = _ult
        _gav = format_currency(_saldo_gaveta).replace("$", "\\$")
        if saldo_ini_d:
            _dif = round(_saldo_caixa - _saldo_gaveta, 2)
            if abs(_dif) < 0.005:
                st.success(
                    f"✅ Bate com o Suitable: gaveta de {_dt_ult:%d/%m/%Y} = {_gav}."
                )
            else:
                _d = format_currency(_dif).replace("$", "\\$")
                st.warning(
                    f"Não bate com o Suitable: gaveta de {_dt_ult:%d/%m/%Y} = {_gav}; "
                    f"diferença (app − Suitable) = {_d}. Confira lançamentos do período."
                )
        else:
            _impl = format_currency(_saldo_gaveta - (entradas_d - saidas_d)).replace("$", "\\$")
            st.caption(
                f"Gaveta segundo o email do Suitable de {_dt_ult:%d/%m/%Y}: {_gav}. "
                f"Saldo inicial implícito (gaveta − saldo do período): {_impl}. "
                "Lance uma linha com a descrição \"Saldo inicial\" com esse valor."
            )
    elif saldo_ini_d:
        st.caption(
            "Para conferir com o Suitable, clique em \"Buscar fechamentos do Gmail\" acima."
        )


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

                if not df_cat_h.empty and {"Categoria", "Entradas", "Saídas"} <= set(df_cat_h.columns):
                    _cats_h = {
                        str(r["Categoria"]): float(r["Entradas"] or 0) + float(r["Saídas"] or 0)
                        for _, r in df_cat_h.iterrows()
                        if pd.notna(r["Categoria"])
                    }
                    _render_gerencial(tabela_ponte(ponte_por_categoria(_cats_h)))
                _mov_h_canais = rep.get("movimentos", pd.DataFrame())
                if not _mov_h_canais.empty:
                    _render_canais(tabela_canais(receita_por_canal_dict(_mov_h_canais)))

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
            st.write("Resultado do período:", format_currency(resultado_consolidado))
            st.markdown("</div>", unsafe_allow_html=True)

            _render_gerencial(df_gerencial)
            _render_canais(df_canais)

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
            st.markdown("**Movimentos (do relatório) — editar categorias / excluir lançamentos**")
            if df_mov_h.empty:
                st.info("Este relatório não possui a aba **Movimentos**.")
            else:
                st.caption(
                    "Você pode **trocar a Categoria** ou marcar **Excluir** (ex.: saldo inicial do caixa, que não é receita). "
                    "Trocar categoria não muda entradas, saídas nem resultado; excluir recalcula os totais. "
                    "As alterações ficam guardadas ao trocar o filtro e só valem ao clicar em salvar: é gerada uma "
                    "**nova versão** do fechamento (a mais recente vale no Controle Anual)."
                )
                df_mov_h = df_mov_h.reset_index(drop=True)
                _fid = st.session_state.get("hist_loaded_file_id")
                _store = st.session_state.setdefault(f"hist_edits_{_fid}", {"cat": {}, "del": set()})
                df_view = df_mov_h.copy()
                if "Data" in df_view.columns:
                    df_view["Data"] = (
                        pd.to_datetime(df_view["Data"], dayfirst=True, errors="coerce").dt.strftime("%d/%m/%Y").fillna("")
                    )
                _cats_h = CATEGORIAS_PADRAO + carregar_categorias_personalizadas()
                _cats_h += sorted(c for c in df_mov_h["Categoria"].dropna().unique() if c not in _cats_h)
                _busca = st.text_input("Filtrar por descrição/categoria (opcional):", key="hist_edit_busca")
                _mask = pd.Series(True, index=df_view.index)
                if _busca.strip():
                    _b = normalizar_texto(_busca)
                    _mask = df_view.apply(
                        lambda r: _b in normalizar_texto(f"{r.get('Descrição', '')} {r.get('Categoria', '')}"), axis=1
                    )
                # base do editor: fixa por filtro (o editor guarda só as mudanças em cima dela)
                _base_key = f"hist_base_{_fid}_{_busca}"
                if _base_key not in st.session_state:
                    _b0 = df_view[_mask].copy()
                    for i in _b0.index:
                        if i in _store["cat"]:
                            _b0.loc[i, "Categoria"] = _store["cat"][i]
                    _b0["Excluir"] = pd.Series([i in _store["del"] for i in _b0.index], index=_b0.index, dtype=bool)
                    st.session_state[_base_key] = _b0
                if st.session_state[_base_key].empty:
                    st.info("Nenhum lançamento encontrado para esse filtro.")
                    _ed = st.session_state[_base_key]
                else:
                  _ed = st.data_editor(
                    st.session_state[_base_key],
                    use_container_width=True,
                    hide_index=True,
                    disabled=[c for c in df_view.columns if c != "Categoria"],
                    column_config={
                        "Categoria": st.column_config.SelectboxColumn("Categoria", options=_cats_h),
                        "Excluir": st.column_config.CheckboxColumn("Excluir"),
                    },
                    key=f"hist_editor_{_fid}_{_busca}",
                )
                for i in _ed.index:
                    if bool(_ed.loc[i, "Excluir"]):
                        _store["del"].add(int(i))
                    else:
                        _store["del"].discard(int(i))
                    if _ed.loc[i, "Categoria"] != df_mov_h.loc[i, "Categoria"]:
                        _store["cat"][int(i)] = _ed.loc[i, "Categoria"]
                    else:
                        _store["cat"].pop(int(i), None)
                _ajustes = {i: c for i, c in _store["cat"].items() if i not in _store["del"]}
                _exclusoes = set(_store["del"])
                if _ajustes or _exclusoes:
                    st.warning(
                        f"{len(_ajustes)} categoria(s) alterada(s) e {len(_exclusoes)} lançamento(s) marcado(s) para excluir "
                        "(ainda não salvo)."
                    )
                    for i in sorted(_exclusoes)[:20]:
                        st.caption(
                            f"• EXCLUIR {df_view.loc[i, 'Data']} | {format_currency(float(df_mov_h.loc[i, 'Valor']))} | "
                            f"{str(df_mov_h.loc[i, 'Descrição'])[:60]} ({df_mov_h.loc[i, 'Categoria']})"
                        )
                    for i, c in list(_ajustes.items())[:20]:
                        st.caption(
                            f"• {df_view.loc[i, 'Data']} | {format_currency(float(df_mov_h.loc[i, 'Valor']))} | "
                            f"{str(df_mov_h.loc[i, 'Descrição'])[:60]}: {df_mov_h.loc[i, 'Categoria']} → **{c}**"
                        )
                    _trash = st.checkbox(
                        "Mandar a versão anterior para a lixeira do Drive (recuperável por 30 dias)",
                        value=True,
                        key="hist_edit_trash",
                    )
                    if st.button("💾 Salvar como nova versão", key="hist_edit_save", type="primary"):
                        try:
                            _novo_mov, _xl, _nome_p = aplicar_ajustes_e_gerar_excel(rep, _ajustes, exclusoes=_exclusoes)
                            _ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            _fname = f"fechamento_tempero_{slugify(_nome_p)}_{_ts}.xlsx"
                            upload_history_to_gdrive(_xl, _fname)
                        except Exception as e:
                            st.error(f"Não foi possível salvar a nova versão (nada foi apagado): {e}")
                        else:
                            _old_id = st.session_state.get("hist_loaded_file_id")
                            _msg = f"Nova versão salva: {_fname}."
                            if _trash and _old_id:
                                try:
                                    trash_history_file(_old_id)
                                    _msg += " Versão anterior enviada para a lixeira."
                                except Exception as e:
                                    _msg += f" Atenção: não consegui apagar a versão anterior ({e}); o Controle Anual usa a mais recente."
                            for k in list(st.session_state.keys()):
                                if k.startswith(("hist_edits_", "hist_base_", "hist_editor_")):
                                    st.session_state.pop(k, None)
                            for k in ("hist_report_loaded", "hist_report_name", "hist_loaded_file_id", "hist_selected_name_sidebar"):
                                st.session_state.pop(k, None)
                            st.session_state["hist_edit_msg"] = _msg
                            st.rerun()
        if st.session_state.get("hist_edit_msg"):
            st.success(st.session_state.pop("hist_edit_msg"))

        st.markdown("---")
        st.caption("Fonte: Histórico (Drive) — edição de categorias gera nova versão")
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
            # categorias que já aparecem nos lançamentos (regras iniciais, histórico) também podem ser escolhidas
            categorias_possiveis += sorted(
                c for c in df_mov["Categoria"].dropna().unique() if c not in categorias_possiveis
            )

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

            _n_ac = int((df_mov["Categoria"] == "A Classificar").sum())
            _so_ac = st.checkbox(
                f"Mostrar só 'A Classificar' ({_n_ac} lançamento(s))",
                value=_n_ac > 0,
                key="so_a_classificar",
            )
            _df_editor = df_mov[df_mov["Categoria"] == "A Classificar"] if _so_ac else df_mov
            if _so_ac and _df_editor.empty:
                st.success("Nenhum lançamento em 'A Classificar'. Desmarque para ver todos.")

            _editor_key = f"editor_movimentos_{int(_so_ac)}"
            edited_df = st.data_editor(
                _df_editor,
                key=_editor_key,
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
                _reclass_atual = get_reclassificacoes_sessao()
                # Regra é por descrição e vale para todos os lançamentos iguais. Se a mesma
                # descrição aparece com categorias diferentes no mês (ex.: PIX de R$ 5.500 e de
                # R$ 42.000 para a mesma pessoa), ela é ambígua e NÃO vira regra.
                # A regra passa a ser pela CONTRAPARTE (ex.: "NUTRIR DISTRIBUIDORA DE ALIMENTOS"),
                # não pela descrição inteira: a descrição do banco traz o valor e só repetia quando
                # o valor repetia. Sem nome identificável, usa a descrição completa como antes.
                def _chave_regra(_row):
                    return chave_contraparte(_row.get("Descrição"), _row.get("Conta")) or normalizar_texto(
                        _row.get("Descrição")
                    )

                _chaves = edited_df.apply(_chave_regra, axis=1)
                _n_cat = edited_df.groupby(_chaves)["Categoria"].nunique()
                _ambiguas = set(_n_cat[_n_cat > 1].index)
                alteracoes = 0
                puladas: set[str] = set()
                # Só viram regra as linhas cuja categoria o usuário MUDOU nesta tela
                # (antes toda linha do mês virava regra, inclusive as que ele não tocou).
                _mudancas = (st.session_state.get(_editor_key) or {}).get("edited_rows", {})
                for _pos, _mud in _mudancas.items():
                    if "Categoria" not in _mud:
                        continue
                    _pos = int(_pos)
                    row = edited_df.iloc[_pos]
                    chave_regra = _chaves.iloc[_pos]
                    desc = row.get("Descrição")
                    cat = _mud["Categoria"]
                    if not desc or not cat or cat == "A Classificar":
                        continue
                    if chave_regra in _ambiguas or chave_reclassificacao(
                        row.get("Data"), row.get("Valor"), desc
                    ) in _reclass_atual:
                        puladas.add(str(desc)[:50])
                        continue
                    if regras.get(chave_regra) != cat:
                        regras[chave_regra] = cat
                        alteracoes += 1
                salvar_regras(regras)
                reload_regras_sessao()
                st.success(
                    f"{alteracoes} regra(s) de categorização salva(s). "
                    "Os próximos fechamentos já virão com essas categorias aplicadas."
                )
                if puladas:
                    st.warning(
                        f"{len(puladas)} descrição(ões) não viraram regra (categorias diferentes para a "
                        "mesma descrição, ou lançamento reclassificado à mão). Para um lançamento "
                        "específico, use o quadro abaixo."
                    )
                else:
                    st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

            # ---- Reclassificar um lançamento específico ----
            st.markdown('<div class="tempero-card">', unsafe_allow_html=True)
            st.markdown("**Reclassificar um lançamento específico**")
            st.markdown(
                '<div class="tempero-section-sub">Vale só para este lançamento (data + valor). '
                "Não vira regra: os outros lançamentos com a mesma descrição não mudam. "
                "Ex.: o PIX de R\\$ 42.000 que foi compra do carro da loja.</div>",
                unsafe_allow_html=True,
            )
            _df_rc = df_mov.copy()
            _df_rc["_abs"] = _df_rc["Valor"].abs()
            _df_rc = _df_rc.sort_values("_abs", ascending=False)
            _opcoes_rc = {}
            for _i, _r in _df_rc.iterrows():
                _dt = pd.to_datetime(_r["Data"], errors="coerce")
                _dt_txt = "" if pd.isna(_dt) else _dt.strftime("%d/%m/%Y")
                _opcoes_rc[
                    f"{_dt_txt} · {_r['Conta']} · {str(_r['Descrição'])[:55]} · "
                    f"{format_currency(_r['Valor'])} · hoje: {_r['Categoria']}"
                ] = _i
            _rotulo_rc = st.selectbox("Lançamento (os maiores valores primeiro):", list(_opcoes_rc.keys()), key="rc_lancamento")
            _padrao_cat = "Investimento na Loja (Imobilizado)"
            _idx_cat = categorias_possiveis.index(_padrao_cat) if _padrao_cat in categorias_possiveis else 0
            _cat_rc = st.selectbox("Nova categoria:", categorias_possiveis, index=_idx_cat, key="rc_categoria")
            if st.button("Aplicar só a este lançamento", key="rc_aplicar"):
                _r = df_mov.loc[_opcoes_rc[_rotulo_rc]]
                _reclass = dict(get_reclassificacoes_sessao())
                _reclass[chave_reclassificacao(_r["Data"], _r["Valor"], _r["Descrição"])] = _cat_rc
                try:
                    salvar_reclassificacoes(_reclass)
                    reload_reclassificacoes_sessao()
                    st.rerun()
                except Exception as e:
                    st.error(f"Não consegui salvar no Google Drive: {e}")

            _reclass_vigentes = get_reclassificacoes_sessao()
            if _reclass_vigentes:
                st.markdown("**Reclassificações salvas** (valem para qualquer fechamento que traga esse lançamento):")
                _df_rv = pd.DataFrame(
                    [{"Lançamento (data | valor | descrição)": k, "Categoria": v} for k, v in _reclass_vigentes.items()]
                )
                st.dataframe(_df_rv, use_container_width=True, hide_index=True)
                _rm = st.selectbox("Remover uma reclassificação:", ["—"] + list(_reclass_vigentes.keys()), key="rc_remover")
                if _rm != "—" and st.button("Remover", key="rc_remover_btn"):
                    _novo = {k: v for k, v in _reclass_vigentes.items() if k != _rm}
                    try:
                        salvar_reclassificacoes(_novo)
                        reload_reclassificacoes_sessao()
                        st.rerun()
                    except Exception as e:
                        st.error(f"Não consegui salvar no Google Drive: {e}")
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
        _fechamentos_cmp = selecionar_versoes_recentes(
            [f for f in arquivos if str(f.get("name", "")).startswith("fechamento_tempero_")]
        )
        _ordem_arq = {f["id"]: i for i, f in enumerate(arquivos)}
        _fechamentos_cmp.sort(key=lambda f: _ordem_arq.get(f["id"], 0))
        for file_info in _fechamentos_cmp:
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
                # normaliza para remover acentos (ex: "Março" → "marco")
                s = normalizar_texto(str(periodo).strip()).lower()
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

            _zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
                color="#aaaaaa", strokeDash=[4, 4]
            ).encode(y="y:Q")

            _line = (
                alt.Chart(df_chart)
                .mark_line(color="#d63384", strokeWidth=2.5)
                .encode(
                    x=alt.X("Período:N", sort=period_order, title=None),
                    y=alt.Y("Resultado:Q", title=None),
                )
            )
            _points = (
                alt.Chart(df_chart)
                .mark_point(color="#d63384", filled=True, size=80)
                .encode(
                    x=alt.X("Período:N", sort=period_order, title=None),
                    y=alt.Y("Resultado:Q", title=None),
                    tooltip=[
                        alt.Tooltip("Período:N", title="Período"),
                        alt.Tooltip("Resultado:Q", title="Resultado (R$)", format=",.2f"),
                    ],
                )
            )
            _labels = (
                alt.Chart(df_chart)
                .mark_text(dy=-14, fontSize=11, color="#555555")
                .encode(
                    x=alt.X("Período:N", sort=period_order),
                    y=alt.Y("Resultado:Q"),
                    text=alt.Text("Resultado:Q", format=",.0f"),
                )
            )
            chart = (_zero + _line + _points + _labels).properties(height=320)
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


# ---------- ABA 5: Controle Anual ----------

with tab5:
    require_role("admin")

    st.markdown('<div class="tempero-section-title">📅 Controle Anual</div>', unsafe_allow_html=True)
    st.markdown(
        '<div class="tempero-section-sub">DRE mensal comparativo gerado automaticamente a partir dos fechamentos salvos no Drive.</div>',
        unsafe_allow_html=True,
    )

    with st.spinner("Carregando fechamentos do Drive..."):
        try:
            resultado_anual = carregar_dre_anual()
            if len(resultado_anual) == 3:
                meses_anual, linhas_dre, resumos_anual = resultado_anual
            else:
                meses_anual, linhas_dre, resumos_anual = [], [], {}
        except Exception as e:
            st.error(f"Erro ao carregar dados anuais: {e}")
            meses_anual, linhas_dre, resumos_anual = [], [], {}

    if not meses_anual:
        st.info("Nenhum fechamento encontrado no Drive. Salve ao menos um fechamento para ver o controle anual.")
    else:
        # Monta dados de categorias para alertas e CMV
        _dados_cats_anual = {}
        for linha in linhas_dre:
            cat = linha["Categoria"]
            if str(cat).startswith("__"):
                continue
            for mes in meses_anual:
                if mes not in _dados_cats_anual:
                    _dados_cats_anual[mes] = {}
                _dados_cats_anual[mes][cat] = linha.get(mes, 0.0)

        # --- Cards de resumo ---
        _pontes_anual = {m: ponte_por_categoria(_dados_cats_anual.get(m, {})) for m in meses_anual}
        receita_total = sum(abs(p_["receita"]) for p_ in _pontes_anual.values())
        operacional_total = sum(p_["operacional"] for p_ in _pontes_anual.values())
        retiradas_total = sum(abs(p_["retiradas"]) for p_ in _pontes_anual.values())
        # aplicações líquidas: positivo = guardou dinheiro; negativo = resgatou mais do que aplicou
        aplicado_liquido = -sum(p_["aplicacoes"] for p_ in _pontes_anual.values())

        n_meses = len(meses_anual) or 1
        resultado_caixa_total = sum(p_["variacao_caixa"] for p_ in _pontes_anual.values())
        dif_apos_retiradas = operacional_total - retiradas_total
        # "mês comum" = mediana mensal: uma retirada pontual grande não distorce
        _ret_mensais = sorted(abs(p_["retiradas"]) for p_ in _pontes_anual.values())
        _meio = len(_ret_mensais) // 2
        _retirada_mes_comum = (
            _ret_mensais[_meio] if len(_ret_mensais) % 2
            else (_ret_mensais[_meio - 1] + _ret_mensais[_meio]) / 2
        ) if _ret_mensais else 0.0

        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.markdown(metric_card_html(
                "Vendas", format_currency(receita_total),
                f"soma de {n_meses} meses"), unsafe_allow_html=True)
        with col2:
            st.markdown(metric_card_html(
                "Sobra da operação", format_currency(operacional_total),
                f"vendas − todos os custos<br>≈ {format_currency(operacional_total / n_meses)} por mês"),
                unsafe_allow_html=True)
        with col3:
            st.markdown(metric_card_html(
                "Retirada dos sócios", format_currency(retiradas_total),
                f"transferências + sangria<br>mês comum ≈ {format_currency(_retirada_mes_comum)}"),
                unsafe_allow_html=True)
        with col4:
            if aplicado_liquido < 0:
                st.markdown(metric_card_html(
                    "Reservas usadas", format_currency(abs(aplicado_liquido)),
                    "resgatou mais do que aplicou"), unsafe_allow_html=True)
            else:
                st.markdown(metric_card_html(
                    "Guardado em aplicações", format_currency(aplicado_liquido),
                    "aplicou mais do que resgatou"), unsafe_allow_html=True)

        # Resumo em frases (R$ escapado: o Streamlit trata $ como fórmula)
        investimento_loja_total = abs(sum(p_["investimento_loja"] for p_ in _pontes_anual.values()))
        _frase = (
            f"Em {n_meses} meses a operação gerou {format_currency(operacional_total)} e os sócios retiraram "
            f"{format_currency(retiradas_total)}"
        )
        if dif_apos_retiradas >= 0:
            _frase += f": sobraram **{format_currency(dif_apos_retiradas)}**."
        else:
            _frase += f": as retiradas passaram da operação em **{format_currency(abs(dif_apos_retiradas))}**."
        if investimento_loja_total >= 0.005:
            _dif2 = dif_apos_retiradas - investimento_loja_total
            _frase += f" A loja também investiu {format_currency(investimento_loja_total)} em bens (veículo/equipamentos)"
            if _dif2 < 0:
                _frase += f", o que deixou **{format_currency(abs(_dif2))}** a mais do que a operação gerou."
            else:
                _frase += f"; ainda sobraram **{format_currency(_dif2)}**."
        if aplicado_liquido < 0:
            _frase += f" Nas aplicações, resgatou {format_currency(abs(aplicado_liquido))} a mais do que aplicou."
        else:
            _frase += f" Nas aplicações, guardou {format_currency(aplicado_liquido)} a mais do que resgatou."
        _frase += f" Variação final do caixa no período: {format_currency(resultado_caixa_total)}."
        st.info(_frase.replace("$", "\\$"))

        st.markdown("---")

        # --- DRE mensal ---
        st.markdown('<div class="tempero-section-title">DRE mensal por categoria</div>', unsafe_allow_html=True)

        df_dre = pd.DataFrame(linhas_dre)
        # Linhas gerenciais no fim da tabela (o __ Resultado __ já existente é a variação de caixa)
        _extra = [
            {"Categoria": "__ Resultado operacional __", **{m: _pontes_anual[m]["operacional"] for m in meses_anual}},
            {"Categoria": "__ Retiradas dos sócios __", **{m: _pontes_anual[m]["retiradas"] for m in meses_anual}},
            {"Categoria": "__ Resultado após retiradas __", **{m: _pontes_anual[m]["apos_retiradas"] for m in meses_anual}},
        ]
        df_dre = pd.concat([df_dre, pd.DataFrame(_extra)], ignore_index=True)
        df_dre_display = df_dre.copy()
        for mes in meses_anual:
            df_dre_display[mes] = df_dre_display[mes].apply(
                lambda x: format_currency(x) if isinstance(x, (int, float)) and x != 0.0 else "—"
            )

        st.dataframe(
            df_dre_display.set_index("Categoria"),
            use_container_width=True,
        )

        # --- Receita por canal, mês a mês ---
        _canais_mes = {m: (resumos_anual.get(m, {}).get("canais") or {}) for m in meses_anual}
        if any(_canais_mes.values()):
            st.markdown('<div class="tempero-section-title">Receita por canal</div>', unsafe_allow_html=True)
            _linhas_canal = []
            for _c in [c for c in CANAIS_ORDEM if any(c in _canais_mes[m] for m in meses_anual)]:
                _lin = {"Canal": _c}
                for _m in meses_anual:
                    _tot = sum(_canais_mes[_m].values())
                    _v = _canais_mes[_m].get(_c, 0.0)
                    _lin[_m] = "—" if not _v or not _tot else f"{format_currency(_v)} ({_v / _tot * 100:.0f}%)"
                _linhas_canal.append(_lin)
            st.dataframe(pd.DataFrame(_linhas_canal).set_index("Canal"), use_container_width=True)
            st.caption("Canal identificado pelo texto do lançamento. Meses salvos sem a aba Movimentos ficam em branco.")

        st.markdown("---")

        # --- Barra de CMV ---
        st.markdown('<div class="tempero-section-title">CMV — custo de insumos sobre receita</div>', unsafe_allow_html=True)

        cmv_por_mes, cmv_mm_por_mes = cmv_mensal_e_media_movel(meses_anual, _dados_cats_anual)
        for mes in meses_anual:
            cmv = cmv_por_mes.get(mes)
            if cmv is None:
                continue
            perc = cmv * 100
            mm = cmv_mm_por_mes.get(mes)
            perc_mm = (mm if mm is not None else cmv) * 100
            # a cor segue a média móvel de 3 meses (o mês isolado oscila com o calendário de boletos)
            if perc_mm > 32:
                label_cor = "🔴"
            elif perc_mm > 28:
                label_cor = "🟡"
            else:
                label_cor = "🟢"

            st.markdown(
                f"**{mes}** {label_cor} &nbsp; mês `{perc:.1f}%` &nbsp;·&nbsp; média 3 meses `{perc_mm:.1f}%`",
                unsafe_allow_html=True,
            )
            st.progress(min(perc / 50, 1.0))  # escala: 50% = barra cheia

        st.caption(
            "CMV = (Fornecedores e Insumos + Embalagens) ÷ Receita. "
            "Referência para marmitarias: 28–32%. A receita é líquida de comissão do iFood e taxa de cartão."
        )

        st.markdown("---")

        # --- Alertas automáticos ---
        st.markdown('<div class="tempero-section-title">Alertas do período</div>', unsafe_allow_html=True)

        alertas = gerar_alertas(meses_anual, resumos_anual, _dados_cats_anual)

        if not alertas:
            st.success("Nenhum alerta encontrado. Todos os indicadores dentro do esperado.")
        else:
            for alerta in alertas:
                if alerta["tipo"] == "erro":
                    st.error(alerta["texto"])
                elif alerta["tipo"] == "aviso":
                    st.warning(alerta["texto"])
                else:
                    st.success(alerta["texto"])

        st.markdown("---")

        # --- Conferência geral dos fechamentos salvos ---
        st.markdown('<div class="tempero-section-title">🔎 Conferência geral</div>', unsafe_allow_html=True)
        st.caption(
            "Varre a versão mais recente de cada mês salvo e aponta o que merece revisão: saldo inicial contado "
            "como receita, A Classificar, mesmo favorecido em categorias diferentes, totais que não fecham, "
            "lançamentos duplicados e lançamentos manuais no caixa. Só leitura: nada é alterado."
        )
        if st.button("Rodar conferência geral", key="conf_geral_rodar"):
            with st.spinner("Lendo os fechamentos do Drive..."):
                try:
                    _reps_conf, _avisos_conf = carregar_relatorios_recentes()
                    st.session_state["conf_geral"] = {
                        "achados": conferir(_reps_conf),
                        "meses": sorted(_reps_conf),
                        "avisos": _avisos_conf,
                    }
                except Exception as e:
                    st.session_state.pop("conf_geral", None)
                    st.error(f"Erro ao rodar a conferência: {e}")

        _conf = st.session_state.get("conf_geral")
        if _conf:
            st.caption(f"Meses conferidos: {', '.join(_conf['meses']) or 'nenhum'}.")
            for _a in _conf["avisos"]:
                st.warning(_a)
            _achados = _conf["achados"]
            st.dataframe(resumo_por_tipo(_achados), use_container_width=True, hide_index=True)
            if not _achados:
                st.success("Nenhum achado: os fechamentos salvos passaram em todas as verificações.")
            for _tipo, _rotulo in _TIPOS_CONF.items():
                _itens = [a for a in _achados if a["tipo"] == _tipo]
                if not _itens:
                    continue
                with st.expander(f"{_rotulo} ({len(_itens)})", expanded=any(a["gravidade"] == "erro" for a in _itens)):
                    for _a in _itens:
                        _msg = f"**{_a['mes']}** — {_a['texto']}"
                        (st.error if _a["gravidade"] == "erro" else st.warning)(_msg)
            st.caption(
                "Para corrigir: Conferência & Categorias → Fonte: Histórico (Drive) → editar categorias / "
                "excluir lançamentos (gera uma nova versão do mês)."
            )
