import math
from collections import defaultdict
from pathlib import Path
from io import BytesIO
from datetime import datetime
import json

import pandas as pd
import streamlit as st

from openpyxl.styles import Font, Alignment, PatternFill
from openpyxl.utils import get_column_letter


# ---------- Caminhos de arquivos auxiliares ----------

RULES_PATH = Path("regras_categorias.json")
CATEGORIAS_PATH = Path("categorias_personalizadas.json")

# dicionário global de regras aprendidas
REGRAS_CATEGORIA = {}


# ---------- Função de formatação genérica para tabelas no Excel ----------

def formatar_tabela_excel(ws, df, start_row=1):
    """
    Aplica estilo básico:
    - Cabeçalho em negrito, fundo cinza, centralizado
    - Largura das colunas ajustada
    - Colunas de valor com formato de moeda (R$)
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
            for prefix in ("entradas", "saídas", "saidas", "resultado", "saldo", "valor")
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
        # extrai descrição primeiro
        descricao = extrair_descricao_linha(linha)
        desc_norm = normalizar_texto(descricao)

        # pula linhas de saldo (não são movimentação real)
        if (
            "SALDO ANTERIOR" in desc_norm
            or "SALDO TOTAL DISPONIVEL DIA" in desc_norm
            or "SALDO TOTAL DISPONÍVEL DIA" in desc_norm
            or "SALDO DO DIA" in desc_norm
        ):
            continue

        valor = parse_numero_br(
            linha.get("Valor")
            or linha.get("VALOR")
            or linha.get("valor")
            or linha.get("Valor (R$)")
            or 0
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
                "descricao": descricao,
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
        # descrição primeiro
        descricao = extrair_descricao_linha(linha)
        desc_norm = normalizar_texto(descricao)

        # linhas de saldo do dia não interessam
        if "SALDO DO DIA" in desc_norm or "SALDO DIA" in desc_norm:
            continue

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
                "descricao": descricao,
                "valor": valor,
                "conta": "PagSeguro",
            }
        )

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


# ---------- Regras de categorização aprendidas ----------

def carregar_regras():
    if RULES_PATH.exists():
        try:
            with RULES_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def salvar_regras(regras: dict):
    with RULES_PATH.open("w", encoding="utf-8") as f:
        json.dump(regras, f, ensure_ascii=False, indent=2)


def carregar_categorias_personalizadas():
    if CATEGORIAS_PATH.exists():
        try:
            with CATEGORIAS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def salvar_categorias_personalizadas(lista):
    with CATEGORIAS_PATH.open("w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)


def classificar_categoria(mov):
    desc_orig = mov.get("descricao")
    desc_norm = normalizar_texto(desc_orig)
    valor = mov.get("valor", 0.0)

    # 1) Regras aprendidas pelo usuário (prioridade máxima)
    if REGRAS_CATEGORIA:
        for padrao, categoria in REGRAS_CATEGORIA.items():
            if padrao in desc_norm:
                return categoria

    # 2) Regras fixas que já tínhamos
    if "ANTINSECT" in desc_norm:
        return "Dedetização / Controle de Pragas"

    if "CIA ESTADUAL DE DIST" in desc_norm or "CEEE" in desc_norm or "ENERGIA ELETRICA" in desc_norm:
        return "Energia Elétrica"

    if "RECH CONTABILIDADE" in desc_norm or "RECH CONT" in desc_norm:
        return "Contabilidade e RH"

    if (
        "BUSINESS      0503-2852" in desc_norm
        or "BUSINESS 0503-2852" in desc_norm
        or "ITAU UNIBANCO HOLDING S.A." in desc_norm
        or "CARTAO" in desc_norm
        or "CARTÃO" in desc_norm
    ):
        return "Fatura Cartão"

    if "APLICACAO" in desc_norm or "APLICAÇÃO" in desc_norm or "CDB" in desc_norm or "CREDBANCRF" in desc_norm:
        return "Investimentos (Aplicações)"

    if (
        "REND PAGO APLIC" in desc_norm
        or "RENDIMENTO APLIC" in desc_norm
        or "REND APLIC" in desc_norm
        or "RENDIMENTO" in desc_norm
    ):
        return "Rendimentos de Aplicações"

    if "ZOOP" in desc_norm or "ALUGUEL" in desc_norm:
        return "Aluguel Comercial"

    if "MOTOBOY" in desc_norm or "ENTREGA" in desc_norm:
        return "Motoboy / Entregas"

    if (
        "CAROLINE" in desc_norm
        or "VERONICA" in desc_norm
        or "VERONICA DA SILVA CARDOSO" in desc_norm
        or "EVELLYN" in desc_norm
        or "SALARIO" in desc_norm
        or "SALÁRIO" in desc_norm
        or "FOLHA" in desc_norm
    ):
        return "Folha de Pagamento"

    if "ANA PAULA" in desc_norm or "NUTRICIONISTA" in desc_norm:
        return "Nutricionista"

    if (
        "DARF" in desc_norm
        or "GPS" in desc_norm
        or "FGTS" in desc_norm
        or "INSS" in desc_norm
        or "SIMPLES NACIONAL" in desc_norm
        or "IMPOSTO" in desc_norm
    ):
        return "Impostos e Encargos"

    if (
        ("TRANSFERENCIA" in desc_norm or "TRANSFERÊNCIA" in desc_norm or "PIX" in desc_norm)
        and ("RICARDO" in desc_norm or "LIZIANI" in desc_norm or "LIZI" in desc_norm)
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

# Nome do período para histórico
default_periodo = datetime.today().strftime("%Y-%m") + " - período"
nome_periodo = st.sidebar.text_input(
    "Nome do período (para histórico)",
    value=default_periodo,
    help='Ex.: "2025-11 1ª quinzena", "2025-10 mês cheio"',
)


# ---------- Lógica principal ----------

if arquivo_itau and arquivo_pag:
    # Carrega regras aprendidas (se existirem)
    REGRAS_CATEGORIA = carregar_regras()

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

    # ---------- Categorias e movimentos (auto) ----------
    movimentos = mov_itau + mov_pag
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

    df_mov = pd.DataFrame(movimentos_cat)

    # ---------- Gerenciar categorias (criação de novas) ----------
    st.subheader("Gerenciar Categorias")

    categorias_padrao = [
        "Vendas / Receitas",
        "Fornecedores e Insumos",
        "Folha de Pagamento",
        "Aluguel Comercial",
        "Contabilidade e RH",
        "Dedetização / Controle de Pragas",
        "Energia Elétrica",
        "Motoboy / Entregas",
        "Nutricionista",
        "Impostos e Encargos",
        "Investimentos (Aplicações)",
        "Rendimentos de Aplicações",
        "Fatura Cartão",
        "Transferência Interna / Sócios",
        "A Classificar",
    ]

    categorias_custom = carregar_categorias_personalizadas()
    categorias_possiveis = categorias_padrao + categorias_custom

    nova_cat = st.text_input("Criar nova categoria:")
    if st.button("Adicionar categoria"):
        if nova_cat.strip() != "":
            if nova_cat not in categorias_possiveis:
                categorias_custom.append(nova_cat)
                salvar_categorias_personalizadas(categorias_custom)
                st.success(f"Categoria '{nova_cat}' criada com sucesso!")
                st.rerun()
            else:
                st.warning("Essa categoria já existe.")

    # ---------- Conferência e ajustes de categorias ----------
    st.subheader("Conferência e ajustes de categorias")

    edited_df = st.data_editor(
        df_mov,
        key="editor_movimentos",
        use_container_width=True,
        num_rows="fixed",
        column_config={
            "Categoria": st.column_config.SelectboxColumn(
                "Categoria",
                options=categorias_possiveis,
                help="Ajuste a categoria se necessário.",
            )
        },
    )

    st.markdown(
        "_Dica: ajuste as categorias que estiverem erradas e, se quiser que o sistema memorize, "
        "clique em **Salvar regras de categorização**._"
    )

    salvar_ajustes = st.button("Salvar regras de categorização")

    if salvar_ajustes:
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
        REGRAS_CATEGORIA = regras
        st.success(
            f"{alteracoes} regra(s) de categorização salva(s). "
            "Nos próximos fechamentos, descrições iguais serão classificadas automaticamente."
        )

    # Usaremos sempre o edited_df como base para resumo/exportação
    df_mov_export = edited_df.copy()

    # ---------- Resumo por categoria com base nas categorias ajustadas ----------
    entradas_cat = defaultdict(float)
    saidas_cat = defaultdict(float)

    for _, row in df_mov_export.iterrows():
        cat = row["Categoria"]
        v = row["Valor"]
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

    # ---------- Geração do Excel estilizado ----------
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
    # -------------------- Resumo Itaú / PagSeguro --------------------
    start_row_resumo = 3
    df_resumo_contas.to_excel(
        writer, sheet_name="Resumo", index=False, startrow=start_row_resumo
    )

    # -------------------- Consolidação --------------------
    start_row_consol = start_row_resumo + len(df_resumo_contas) + 3
    df_consolidado.to_excel(
        writer, sheet_name="Resumo", index=False, startrow=start_row_consol
    )

    # 🔹 NOVO: aba limpa para comparativo histórico
    df_consolidado.to_excel(writer, sheet_name="ResumoDados", index=False)

    # -------------------- Categorias --------------------
    df_cat_export.to_excel(writer, sheet_name="Categorias", index=False, startrow=1)

    # -------------------- Movimentos --------------------
    df_mov_export.to_excel(writer, sheet_name="Movimentos", index=False, startrow=1)

    # Autofit
    workbook = writer.book
    for sheet in writer.sheets.values():
        ws = sheet
        for col in ws.columns:
            max_len = 0
            col_letter = col[0].column_letter
            for cell in col:
                try:
                    if cell.value:
                        max_len = max(max_len, len(str(cell.value)))
                except:
                    pass
            adjusted_width = max_len + 2
            ws.column_dimensions[col_letter].width = adjusted_width


    start_row_consol = start_row_resumo + len(df_resumo_contas) + 3
    df_consolidado.to_excel(
        writer, sheet_name="Resumo", index=False, startrow=start_row_consol
    )

    # 🔹 NOVO: aba técnica só com os dados consolidados, para o histórico analítico
    df_consolidado.to_excel(writer, sheet_name="ResumoDados", index=False)

    df_cat_export.to_excel(writer, sheet_name="Categorias", index=False, startrow=1)
    df_mov_export.to_excel(writer, sheet_name="Movimentos", index=False, startrow=1)
    ...


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
        # Lista simples com nome + data + botão de download (como antes)
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

        # ---------- Comparativo analítico entre períodos ----------
        st.subheader("Comparativo entre períodos (Histórico Analítico)")

                resumos = []
        for arq in arquivos:
            try:
                # 1) Tenta ler a aba técnica nova (ResumoDados)
                try:
                    df_consol = pd.read_excel(arq, sheet_name="ResumoDados")
                except Exception:
                    # 2) Compatibilidade com arquivos antigos (tenta extrair da aba Resumo)
                    df_res = pd.read_excel(arq, sheet_name="Resumo")
                    if "Nome do período" not in df_res.columns:
                        continue
                    df_consol = df_res[df_res["Nome do período"].notna()]
                    if df_consol.empty:
                        continue

                linha = df_consol.iloc[0]
                periodo = str(linha.get("Nome do período", arq.name))
                entradas = float(linha.get("Entradas totais", 0.0))
                saidas = float(linha.get("Saídas totais", 0.0))
                resultado = float(linha.get("Resultado do período", 0.0))
                saldo_final_val = linha.get("Saldo final", None)
                saldo_final = float(saldo_final_val) if saldo_final_val is not None else None

                resumos.append(
                    {
                        "Período": periodo,
                        "Entradas": entradas,
                        "Saídas": saidas,
                        "Resultado": resultado,
                        "Saldo final": saldo_final,
                    }
                )
            except Exception:
                # se der erro em algum arquivo, só pula
                continue


        if not resumos:
            st.info("Ainda não foi possível montar o comparativo. Gere e salve alguns fechamentos no novo formato.")
        else:
            df_hist = pd.DataFrame(resumos)

            # Ordena do mais antigo pro mais recente (pra ficar lógico no gráfico)
            df_hist = df_hist.iloc[::-1].reset_index(drop=True)

            df_display = df_hist.copy()
            for col in ["Entradas", "Saídas", "Resultado", "Saldo final"]:
                if col in df_display.columns:
                    df_display[col] = df_display[col].apply(
                        lambda x: format_currency(x) if pd.notna(x) else "-"
                    )

            st.write("Tabela comparativa:")
            st.dataframe(df_display, use_container_width=True)

            # Gráfico do resultado por período
            st.write("Resultado por período:")
            chart_df = df_hist.set_index("Período")[["Resultado"]]
            st.bar_chart(chart_df)

else:
    st.write("Nenhum fechamento salvo ainda.")
