import csv
import math
from pathlib import Path
from collections import defaultdict

# Tenta importar pandas para ler arquivos Excel
try:
    import pandas as pd
except ImportError:
    pd = None


def ler_arquivo_tabela(caminho_arquivo: Path):
    """
    Lê arquivo .csv ou .xlsx e devolve uma lista de dicionários (linhas).
    Normaliza os nomes das colunas (strip).
    """
    suffix = caminho_arquivo.suffix.lower()

    if suffix in (".csv", ".txt"):
        with open(caminho_arquivo, mode="r", encoding="utf-8-sig") as f:
            leitor = csv.DictReader(f, delimiter=";")
            linhas = []
            for linha in leitor:
                nova_linha = {}
                for k, v in linha.items():
                    if isinstance(k, str):
                        k = k.strip()
                    nova_linha[k] = v
                linhas.append(nova_linha)
            return linhas

    elif suffix in (".xlsx", ".xls"):
        if pd is None:
            raise RuntimeError(
                "Para ler arquivos Excel (.xlsx/.xls), instale 'pandas' e 'openpyxl' "
                "com o comando: py -m pip install pandas openpyxl"
            )
        df = pd.read_excel(caminho_arquivo)
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

    else:
        raise RuntimeError(
            f"Formato de arquivo não suportado: {suffix}. Use .csv ou .xlsx."
        )


def parse_numero_br(valor):
    """
    Converte valor em float, aceitando:
    - números do Excel (int/float)
    - texto '1.234,56' ou '1234.56'
    - texto com 'R$'
    - vazio, None, '-' -> 0.0
    """
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
    """
    Monta uma 'descricao' juntando as principais colunas textuais.
    1) Tenta colunas com 'HIST' ou 'DESCR' no nome.
    2) Complementa com outros campos textuais que não sejam óbvios numéricos.
    """
    if "descricao" in linha and linha["descricao"] not in (None, ""):
        return linha["descricao"]

    partes = []

    # 1) Colunas que parecem histórico/descrição
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

    # 2) Complementa com outros campos textuais
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


def carregar_extrato_itau(caminho_arquivo: Path):
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    linhas = ler_arquivo_tabela(caminho_arquivo)

    for linha in linhas:
        # 1) Tenta coluna única de valor
        valor = parse_numero_br(
            linha.get("Valor") or linha.get("VALOR") or linha.get("valor") or linha.get("Valor (R$)") or 0
        )

        # 2) Se ainda zero, tenta combinação débito/crédito
        if valor == 0.0:
            debito = 0.0
            credito = 0.0

            for k, v in linha.items():
                if not isinstance(k, str):
                    continue
                kl = normalizar_texto(k.strip())

                if "DEBITO" in kl or "DEBITO(-)" in kl or "DEBITO (+)" in kl:
                    debito = parse_numero_br(v)
                if "CREDITO" in kl or "CREDITO(+)" in kl or "CREDITO (+)" in kl:
                    credito = parse_numero_br(v)

            if debito != 0.0 or credito != 0.0:
                valor = credito - debito

        if valor > 0:
            entradas += valor
        elif valor < 0:
            saidas += valor

        movimentos.append({
            "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
            "descricao": extrair_descricao_linha(linha),
            "valor": valor,
            "conta": "Itau",
        })

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


def carregar_extrato_pagseguro(caminho_arquivo: Path):
    entradas = 0.0
    saidas = 0.0
    movimentos = []

    linhas = ler_arquivo_tabela(caminho_arquivo)

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

        movimentos.append({
            "data": linha.get("Data") or linha.get("DATA") or linha.get("data"),
            "descricao": extrair_descricao_linha(linha),
            "valor": valor,
            "conta": "PagSeguro",
        })

    resultado = entradas + saidas
    return entradas, saidas, resultado, movimentos


def classificar_categoria(mov):
    desc = normalizar_texto(mov.get("descricao"))
    valor = mov.get("valor", 0.0)

    # Regras específicas

    # Dedetização / Controle de Pragas
    if "ANTINSECT" in desc:
        return "Dedetização / Controle de Pragas"

    # Energia Elétrica (vai entrar quando aparecer CEEE/CIA ESTADUAL etc. em outros períodos)
    if "CIA ESTADUAL DE DIST" in desc or "CEEE" in desc or "ENERGIA ELETRICA" in desc:
        return "Energia Elétrica"

    # Contabilidade e RH
    if "RECH CONTABILIDADE" in desc or "RECH CONT" in desc:
        return "Contabilidade e RH"

    # Fatura de Cartão
    if (
        "BUSINESS      0503-2852" in desc
        or "BUSINESS 0503-2852" in desc
        or "ITAU UNIBANCO HOLDING S.A." in desc
        or "CARTAO" in desc
        or "CARTÃO" in desc
    ):
        return "Fatura Cartão"

    # Investimentos (Aplicações)
    if "APLICACAO" in desc or "APLICAÇÃO" in desc or "CDB" in desc or "CREDBANCRF" in desc:
        return "Investimentos (Aplicações)"

    # Rendimentos de Aplicações
    if "REND PAGO APLIC" in desc or "RENDIMENTO APLIC" in desc or "REND APLIC" in desc or "RENDIMENTO" in desc:
        return "Rendimentos de Aplicações"

    # Aluguel Comercial (Zoop)
    if "ZOOP" in desc or "ALUGUEL" in desc:
        return "Aluguel Comercial"

    # Motoboy / Entregas
    if "MOTOBOY" in desc or "ENTREGA" in desc:
        return "Motoboy / Entregas"

    # Folha de Pagamento (funcionárias + termos de folha/salário)
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

    # Nutricionista
    if "ANA PAULA" in desc or "NUTRICIONISTA" in desc:
        return "Nutricionista"

    # Impostos e Encargos
    if (
        "DARF" in desc
        or "GPS" in desc
        or "FGTS" in desc
        or "INSS" in desc
        or "SIMPLES NACIONAL" in desc
        or "IMPOSTO" in desc
    ):
        return "Impostos e Encargos"

    # Transferência Interna / Sócios
    if (
        ("TRANSFERENCIA" in desc or "TRANSFERÊNCIA" in desc or "PIX" in desc)
        and ("RICARDO" in desc or "LIZIANI" in desc or "LIZI" in desc)
    ):
        return "Transferência Interna / Sócios"

    # Defaults
    if valor > 0:
        return "Vendas / Receitas"
    if valor < 0:
        return "Fornecedores e Insumos"

    return "A Classificar"


def format_currency(valor):
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def limpar_caminho(texto: str) -> Path:
    texto = texto.strip().strip('"').strip("'")
    return Path(texto)


def main():
    print("=== Fechamento Mensal - Tempero das Gurias ===")

    caminho_itau_input = input("Informe o caminho do extrato do Itaú (.csv ou .xlsx): ").strip()
    caminho_pag_input = input("Informe o caminho do extrato do PagSeguro (.csv ou .xlsx): ").strip()

    caminho_itau = limpar_caminho(caminho_itau_input)
    caminho_pagseguro = limpar_caminho(caminho_pag_input)

    if not caminho_itau.exists():
        print(f"Arquivo do Itaú não encontrado: {caminho_itau}")
        return
    if not caminho_pagseguro.exists():
        print(f"Arquivo do PagSeguro não encontrado: {caminho_pagseguro}")
        return

    saldo_inicial_str = input(
        "Saldo inicial consolidado da loja no início do período (em R$): "
    ).strip()
    if saldo_inicial_str == "":
        saldo_inicial = 0.0
    else:
        saldo_inicial = parse_numero_br(saldo_inicial_str)

    try:
        ent_itau, sai_itau, res_itau, mov_itau = carregar_extrato_itau(caminho_itau)
        ent_pag, sai_pag, res_pag, mov_pag = carregar_extrato_pagseguro(caminho_pagseguro)
    except RuntimeError as e:
        print("Erro:", e)
        return

    entradas_totais = ent_itau + ent_pag
    saidas_totais = sai_itau + sai_pag
    resultado_consolidado = entradas_totais + saidas_totais
    saldo_final = saldo_inicial + resultado_consolidado

    print("\n--- Resumo Itaú ---")
    print("Entradas:", format_currency(ent_itau))
    print("Saídas  :", format_currency(sai_itau))
    print("Resultado do período:", format_currency(res_itau))

    print("\n--- Resumo PagSeguro ---")
    print("Entradas:", format_currency(ent_pag))
    print("Saídas  :", format_currency(sai_pag))
    print("Resultado do período:", format_currency(res_pag))

    print("\n=== Consolidado Loja ===")
    print("Entradas totais:", format_currency(entradas_totais))
    print("Saídas totais  :", format_currency(saidas_totais))
    print("Resultado do período (superávit/déficit):", format_currency(resultado_consolidado))
    print("Saldo inicial  :", format_currency(saldo_inicial))
    print("Saldo final    :", format_currency(saldo_final))

    # --- Resumo por Categoria ---
    movimentos = mov_itau + mov_pag
    entradas_cat = defaultdict(float)
    saidas_cat = defaultdict(float)

    for mov in movimentos:
        cat = classificar_categoria(mov)
        v = mov.get("valor", 0.0)
        if v > 0:
            entradas_cat[cat] += v
        elif v < 0:
            saidas_cat[cat] += v

    print("\n=== Por Categoria ===")
    print(f"{'Categoria':30} {'Entradas':>15} {'Saídas':>15}")
    print("-" * 65)

    todas_cats = sorted(set(list(entradas_cat.keys()) + list(saidas_cat.keys())))

    for cat in todas_cats:
        ent = entradas_cat.get(cat, 0.0)
        sai = saidas_cat.get(cat, 0.0)
        print(f"{cat:30} {format_currency(ent):>15} {format_currency(sai):>15}")


if __name__ == "__main__":
    main()
