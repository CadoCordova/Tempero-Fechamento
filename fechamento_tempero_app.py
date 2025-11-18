import math
from collections import defaultdict
from pathlib import Path
from io import BytesIO
import os
from datetime import datetime

import pandas as pd
import streamlit as st

from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


def formatar_tabela_excel(ws, df, start_row=1):
    """
    Aplica estilo básico:
    - Cabeçalho em negrito, fundo cinza, centralizado
    - Largura das colunas ajustada
    - Colunas de valor com formato de moeda
    """
    header_row = start_row
    n_rows = len(df)
    n_cols = len(df.columns)

    # Cabeçalho
    for col_idx in range(1, n_cols + 1):
        cell = ws.cell(row=header_row, column=col_idx)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        cell.alignment = Alignment(horizontal="center")

    # Congela linha de cabeçalho
    ws.freeze_panes = ws[f"A{header_row + 1}"]

    # Ajusta largura das colunas
    for col_idx, col_name in enumerate(df.columns, start=1):
        max_len = len(str(col_name))
        for row_idx in range(header_row + 1, header_row + 1 + n_rows):
            value = ws.cell(row=row_idx, column=col_idx).value
            if value is None:
                continue
            max_len = max(max_len, len(str(value)))
        ws.column_dimensions[get_column_letter(col_idx)].width = max_len + 2

    # Aplica formato de moeda para colunas de valor
    col_names_lower = [str(c).lower() for c in df.columns]
    for col_idx, col_name in enumerate(col_names_lower, start=1):
        if any(
            col_name.startswith(prefix)
            for prefix in ("entradas", "saídas", "saidas", "resultado", "saldo")
        ):
            for row_idx in range(header_row + 1, header_row + 1 + n_rows):
                cell = ws.cell(row=row_idx, column=col_idx)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = '"R$" #,##0.00'


# ---------- Autenticação simples por senha (via secrets) ----------

def check_auth():
    # se já autenticou na sessão, libera
    if st.session_state.get("auth_ok"):
        return True

    st.title("Tempero das Gurias - Acesso Restrito")

    senha = st.text_input("Digite a senha para acessar o sistema:", type="password")
    ok = st.button("Entrar")

    if ok:
        senha_correta = st.secrets.get("APP_PASSWORD")
        if senha_correta is None:
            st.error("Senha não configurada no Streamlit Secrets (APP_PASSWORD).")
            return False

        if senha == senha_correta:
            st.session_state["auth_ok"] = True
            st.rerun()  # recarrega já autenticado
        else:
            st.error("Senha incorreta. Tente novamente.")
            return False

    # Se ainda não autenticou, não libera o app
    st.stop()


# ---------- Funções de base ----------

def parse_numero_br(valor):
    if valor is None:
        return 0.0

    if isinstance(valor, (int, float)):
        if isinstance(valor, float) and math.isnan(valor):
            return 0.0
        return float(valor)

    s = str(valor)
    s = s.replace("R$", "").strip()
    if s == "" or s == "-":
        return 0.0

    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s)


def normalizar_texto(txt):
    if txt is None:
        return ""
    s = str(txt).upper()
    substituicoes = [
        ("Ó", "O"), ("Ô", "O"), ("Õ", "O"),
        ("Í", "I"),
        ("Á", "A"), ("À", "A"), ("Ã", "A"),
        ("É", "E"), ("Ê", "E"),
        ("Ú", "U"),
        ("Ç", "C"),
    ]
    for ac, sem in substituicoes:
        s = s.replace(ac, sem)
    return s


def extrair_descricao_linha(linha: dict):
    if "descricao" in linha and linha["descricao"] not in (None, ""):
        return linha["descricao"]

    partes = []

    # 1) colunas de histórico/descrição
    for k, v in linha.items():
        if not isinstance(k, str):
            continue
        if v is None:
            continue
        kl = normalizar_texto(k.strip())
        vs = str(v).strip()
        if vs == "":
            continue
        if "HIST" in kl or "DESCR" in kl:
            partes.append(vs)

    # 2) complementa com outros campos textuais
    candidatos_ignorados = [
        "DATA",
        "VALOR",
        "VALORES",
        "DEBITO",
        "DEBITO(-)",
        "DEBITO (+)",
        "DEBITO (-)",
        "CREDITO",
        "CREDITO(+)",
        "CREDITO (+)",
        "CREDITO (-)",
        "ENTRADA",
        "ENTRADAS",
        "SAIDA",
        "SAIDAS",
        "SALDO",
    ]

    for k, v in linha.items():
        if not isinstance(k, str):
            continue
        if v is None:
            continue
        kl = normalizar_texto(k.strip())
        vs = str(v).strip()
        if vs == "":
            continue
        if kl in candidatos_ignorados:
            continue
        if vs not in partes:
            partes.append(vs)

    if not partes:
        return None

    return " | ".join(partes)


def ler_arquivo_tabela_upload(uploaded_file):
    suffix = Path(uploaded_file.name).suffix.lower()

    if suffix in (".csv", ".txt"):
        df = pd.read_csv(uploaded_file, sep=";")
    elif suffix in (".xlsx", ".xls"):
        df = pd.read_excel(uploaded_file)
    else:
        raise RuntimeError(f"Formato não suportado: {suffix}. Use .csv ou .xlsx.")

    df = df.rename(columns=lambda c: str(c).strip())
    records = df.to_dict(orient="records")

    linhas = []
    for rec in records:
        nova_linha = {}
        for k, v in rec.items():
            if isinstance(k, str):
                k = k.strip()
            nova_linha[k] = v
        linhas.append(nova_linha)
    return linhas


def carregar_extrato_itau_upload(uploaded_file):
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    linhas = ler_arquivo_tabela_upload(uploaded_file)

    for linha in linhas:
        valor = parse_numero_br(
            linha.get("Valor") or linha.get("VALOR") or linha.get("valor") or linha.get("Valor (R$)") or 0
        )

        if valor == 0.0:
            debito = 0.0
            credito = 0.0
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

        movimentos.append(
            {
                "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
                "descricao": extrair_descricao_linha(linha),
                "valor": valor,
                "conta": "Itau",
            }
        )

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


def carregar_extrato_pagseguro_upload(uploaded_file):
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    linhas = ler_arquivo_tabela_upload(uploaded_file)

    for linha in linhas:
        entrada = parse_numero_br(
            linha.get("Entradas") or linha.get("ENTRADAS") or linha.get("entradas") or 0
        )
        saida = parse_numero_br(
            linha.get("Saidas")
            or linha.get("SAIDAS")
            or linha.get("Saídas")
            or linha.get("saídas")
            or 0
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

        movimentos.append(
            {
                "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
                "descricao": extrair_descricao_linha(linha),
                "valor": valor,
                "conta": "PagSeguro",
            }
        )

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


def classificar_categoria(mov):
    desc = normalizar_texto(mov.get("descricao"))
    valor = mov.get("valor", 0.0)

    if "ANTINSECT" in desc:
        return "Dedetização / Controle de Pragas"

    if "CIA ESTADUAL DE DIST" in desc or "CEEE" in desc or "ENERGIA ELETRICA" in desc:
        return "Energia Elétrica"

    if "RECH CONTABILIDADE" in desc or "RECH CONT" in desc:
        return "Contabilidade e RH"

    if (
        "BUSINESS      0503-2852" in desc
        or "BUSINESS 0503-2852" in desc
        or "ITAU UNIBANCO HOLDING S.A." in desc
        or "CARTAO" in desc
        or "CARTÃO" in desc
    ):
        return "Fatura Cartão"

    if "APLICACAO" in desc or "APLICAÇÃO" in desc or "CDB" in desc or "CREDBANCRF" in desc:
        return "Investimentos (Aplicações)"

    if (
        "REND PAGO APLIC" in desc
        or "RENDIMENTO APLIC" in desc
        or "REND APLIC" in desc
        or "RENDIMENTO" in desc
    ):
        return "Rendimentos de Aplicações"

    if "ZOOP" in desc or "ALUGUEL" in desc:
        return "Aluguel Comercial"

    if "MOTOBOY" in desc or "ENTREGA" in desc:
        return "Motoboy / Entregas"

    if (
        "CAROLINE" in desc
        or "VERONICA" in desc
        or "VERONICA DA SILVA CARDOSO" in desc
        or "VERÔNICA" in desc
        or "EVELLYN" in desc
        or "SALARIO" in desc
        or "SALÁRIO" in desc
        or "FOLHA" in desc
    ):
        return "Folha de Pagamento"

    if "ANA PAULA" in desc or "NUTRICIONISTA" in desc:
        return "Nutricionista"

    if (
        "DARF" in desc
        or "GPS" in desc
        or "FGTS" in desc
        or "INSS" in desc
        or "SIMPLES NACIONAL" in desc
        or "IMPOSTO" in desc
    ):
        return "Impostos e Encargos"

    if (
        ("TRANSFERENCIA" in desc or "TRANSFERÊNCIA" in desc or "PIX" in desc)
        and ("RICARDO" in desc or "LIZIANI" in desc or "LIZI" in desc)
    ):
        return "Transferência Interna / Sócios"

    if valor > 0:
        return "Vendas / Receitas"
    if valor < 0:
        return "Fornecedores e Insumos"

    return "A Classificar"


def format_currency(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def slugify(texto: str) -> str:
    s = texto.strip().lower()
    # troca acentos básicos
    repl = {
        "á": "a", "à": "a", "ã": "a", "â": "a",
        "é": "e", "ê": "e",
        "í": "i",
        "ó": "o", "ô": "o", "õ": "o",
        "ú": "u",
        "ç": "c",
    }
    for a, b in repl.items():
        s = s.replace(a, b)
    # troca separadores por underline
    for ch in [" ", "/", "\\", "|", ";", ","]:
        s = s.replace(ch, "_")
    # remove duplicados de "_"
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "periodo"


# ---------- Interface Streamlit ----------

st.set_page_config(page_title="Fechamento Tempero das Gurias", layout="wide")

# Checar senha antes de liberar o app
check_auth()

st.title("Fechamento Mensal - Tempero das Gurias")


st.sidebar.header("Parâmetros")

arquivo_itau = st.sidebar.file_uploader(
    "Extrato Itaú (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="itau"
)
arquivo_pag = st.sidebar.file_uploader(
    "Extrato PagSeguro (.csv ou .xlsx)", type=["csv", "xlsx", "xls"], key="pagseguro"
)

saldo_inicial_input = st.sidebar.text_input(
    "Saldo inicial consolidado do período (R$)", value="0"
)

# Novo: nome do período para histórico
default_periodo = datetime.today().strftime("%Y-%m") + " - período"
nome_periodo = st.sidebar.text_input(
    "Nome do período (para histórico)",
    value=default_periodo,
    help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
)

if arquivo_itau and arquivo_pag:
    try:
        saldo_inicial = parse_numero_br(saldo_inicial_input)
    except Exception:
        st.error("Saldo inicial inválido. Use formato 1234,56 ou 1234.56.")
        st.stop()

    try:
        ent_itau, sai_itau, res_itau, mov_itau = carregar_extrato_itau_upload(arquivo_itau)
        ent_pag, sai_pag, res_pag, mov_pag = carregar_extrato_pagseguro_upload(arquivo_pag)
    except RuntimeError as e:
        st.error(str(e))
        st.stop()

    entradas_totais = ent_itau + ent_pag
    saidas_totais = sai_itau + sai_pag
    resultado_consolidado = entradas_totais + saidas_totais
    saldo_final = saldo_inicial + resultado_consolidado

    col1, col2, col3 = st.columns(3)
    col1.metric("Entradas totais", format_currency(entradas_totais))
    col2.metric("Saídas totais", format_currency(saidas_totais))
    col3.metric("Resultado do período", format_currency(resultado_consolidado))

    st.subheader("Resumo por Conta")

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("**Itaú**")
        st.write("Entradas:", format_currency(ent_itau))
        st.write("Saídas  :", format_currency(sai_itau))
        st.write("Resultado:", format_currency(res_itau))

    with c2:
        st.markdown("**PagSeguro**")
        st.write("Entradas:", format_currency(ent_pag))
        st.write("Saídas  :", format_currency(sai_pag))
        st.write("Resultado:", format_currency(res_pag))

    st.subheader("Consolidado da Loja")
    st.write("Saldo inicial:", format_currency(saldo_inicial))
    st.write("Saldo final  :", format_currency(saldo_final))

    # ---------- Categorias e movimentos ----------
    movimentos = mov_itau + mov_pag
    entradas_cat = defaultdict(float)
    saidas_cat = defaultdict(float)
    movimentos_cat = []

    for mov in movimentos:
        cat = classificar_categoria(mov)
        v = mov.get("valor", 0.0)
        novo_mov = {
            "Data": mov.get("data"),
            "Conta": mov.get("conta"),
            "Descrição": mov.get("descricao"),
            "Categoria": cat,
            "Valor": v,
        }
        movimentos_cat.append(novo_mov)

        if v > 0:
            entradas_cat[cat] += v
        elif v < 0:
            saidas_cat[cat] += v

    categorias = sorted(set(list(entradas_cat.keys()) + list(saidas_cat.keys())))
    dados_cat = []
    for cat in categorias:
        dados_cat.append(
            {
                "Categoria": cat,
                "Entradas": entradas_cat.get(cat, 0.0),
                "Saídas": saidas_cat.get(cat, 0.0),
            }
        )

    df_cat_export = pd.DataFrame(dados_cat)
    df_cat_display = df_cat_export.copy()
    df_cat_display["Entradas"] = df_cat_display["Entradas"].map(format_currency)
    df_cat_display["Saídas"] = df_cat_display["Saídas"].map(format_currency)

    st.subheader("Resumo por Categoria")
    st.dataframe(df_cat_display, use_container_width=True)

    # ---------- DataFrames para relatório ----------
    df_mov_export = pd.DataFrame(movimentos_cat)

    df_resumo_contas = pd.DataFrame(
        [
            {"Conta": "Itaú", "Entradas": ent_itau, "Saídas": sai_itau, "Resultado": res_itau},
            {"Conta": "PagSeguro", "Entradas": ent_pag, "Saídas": sai_pag, "Resultado": res_pag},
        ]
    )
    df_consolidado = pd.DataFrame(
        [
            {
                "Nome do período": nome_periodo,
                "Entradas totais": entradas_totais,
                "Saídas totais": saidas_totais,
                "Resultado do período": resultado_consolidado,
                "Saldo inicial": saldo_inicial,
                "Saldo final": saldo_final,
            }
        ]
    )

    buffer = BytesIO()
with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    # Escreve as abas
    df_resumo_contas.to_excel(writer, sheet_name="Resumo", index=False, startrow=0)
    df_consolidado.to_excel(
        writer, sheet_name="Resumo", index=False, startrow=len(df_resumo_contas) + 2
    )
    df_cat_export.to_excel(writer, sheet_name="Categorias", index=False)
    df_mov_export.to_excel(writer, sheet_name="Movimentos", index=False)

    # Acessa workbook/worksheets
    wb = writer.book
    ws_resumo = writer.sheets["Resumo"]
    ws_cat = writer.sheets["Categorias"]
    ws_mov = writer.sheets["Movimentos"]

    # Aplica formatação nas abas de tabelas
    formatar_tabela_excel(ws_cat, df_cat_export, start_row=1)
    formatar_tabela_excel(ws_mov, df_mov_export, start_row=1)

    # Resumo tem duas tabelas, formatamos as duas
    formatar_tabela_excel(ws_resumo, df_resumo_contas, start_row=1)
    formatar_tabela_excel(ws_resumo, df_consolidado, start_row=len(df_resumo_contas) + 3)

buffer.seek(0)

    st.subheader("Relatório do período atual")

    col_dl1, col_dl2 = st.columns(2)
    with col_dl1:
        st.download_button(
            label="Baixar relatório Excel (período atual)",
            data=buffer,
            file_name="fechamento_tempero.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    # ---------- Salvar no histórico ----------
    with col_dl2:
        salvar = st.button("Salvar no histórico")
    if salvar:
        historico_dir = Path("fechamentos")
        historico_dir.mkdir(exist_ok=True)
        slug = slugify(nome_periodo)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = historico_dir / f"fechamento_tempero_{slug}_{timestamp}.xlsx"
        with open(fname, "wb") as f:
            f.write(buffer.getvalue())
        st.success(f"Relatório salvo no histórico como: {fname.name}")

else:
    st.info("Envie os arquivos do Itaú e PagSeguro na barra lateral para ver o fechamento.")


# ---------- Histórico de fechamentos ----------

st.subheader("Histórico de Fechamentos Salvos")

historico_dir = Path("fechamentos")
if historico_dir.exists():
    arquivos = sorted(
        [p for p in historico_dir.iterdir() if p.is_file() and p.suffix == ".xlsx"],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if not arquivos:
        st.write("Nenhum fechamento salvo ainda.")
    else:
        for arq in arquivos:
            stats = arq.stat()
            data_mod = datetime.fromtimestamp(stats.st_mtime).strftime("%Y-%m-%d %H:%M")
            with open(arq, "rb") as f:
                data_bin = f.read()
            col_a, col_b = st.columns([3, 1])
            with col_a:
                st.write(f"📄 **{arq.name}** — salvo em {data_mod}")
            with col_b:
                st.download_button(
                    label="Baixar",
                    data=data_bin,
                    file_name=arq.name,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_{arq.name}",
                )
else:
    st.write("Nenhum fechamento salvo ainda.")
