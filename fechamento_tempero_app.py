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

# dicionário global de regras aprendidas (carregado em runtime)
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


# ---------- Regras de categorização & categorias personalizadas ----------

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

    # 2) Regras fixas
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
    repl = {
        "á": "a", "à": "a", "ã": "a", "â": "a",
        "é": "e", "ê": "e",
       
