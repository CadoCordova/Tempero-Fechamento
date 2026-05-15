import math
import re
import unicodedata
from datetime import datetime


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
        # formato BR: "1.234,56" → remove ponto de milhar, troca vírgula por ponto
        s = s.replace(".", "").replace(",", ".")
    elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
        # ponto de milhar sem vírgula: "1.234" → "1234"
        s = s.replace(".", "")
    return float(s)


def normalizar_texto(txt) -> str:
    if txt is None:
        return ""
    s = str(txt).upper()
    return unicodedata.normalize("NFD", s).encode("ascii", "ignore").decode("ascii")


def extrair_descricao_linha(linha: dict):
    if "descricao" in linha and linha["descricao"] not in (None, ""):
        return linha["descricao"]

    partes = []

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

    candidatos_ignorados = {
        "DATA", "VALOR", "VALORES",
        "DEBITO", "DEBITO(-)", "DEBITO (+)", "DEBITO (-)",
        "CREDITO", "CREDITO(+)", "CREDITO (+)", "CREDITO (-)",
        "ENTRADA", "ENTRADAS", "SAIDA", "SAIDAS", "SALDO",
    }

    for k, v in linha.items():
        if not isinstance(k, str):
            continue
        if v is None:
            continue
        kl = normalizar_texto(k.strip())
        vs = str(v).strip()
        if vs == "" or kl in candidatos_ignorados or vs in partes:
            continue
        partes.append(vs)

    return " | ".join(partes) if partes else None


def format_currency(valor) -> str:
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def slugify(texto: str) -> str:
    s = texto.strip().lower()
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
    for ch in [" ", "/", "\\", "|", ";", ","]:
        s = s.replace(ch, "_")
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_") or "periodo"


_MESES_PT = {
    "janeiro": "01", "fevereiro": "02", "marco": "03", "abril": "04",
    "maio": "05", "junho": "06", "julho": "07", "agosto": "08",
    "setembro": "09", "outubro": "10", "novembro": "11", "dezembro": "12",
}


def get_ano_mes(nome_periodo: str) -> str | None:
    """
    Extrai YYYY-MM a partir de várias formas de nome de período:
      - "YYYY-MM …"
      - "Mês AAAA …"  (pt-BR)
      - "MM/YYYY …"
    """
    if not nome_periodo:
        return None

    s = str(nome_periodo).strip()

    # 1) YYYY-MM em qualquer posição
    m = re.search(r"(\d{4})-(\d{2})", s)
    if m:
        try:
            datetime.strptime(f"{m.group(1)}-{m.group(2)}", "%Y-%m")
            return f"{m.group(1)}-{m.group(2)}"
        except ValueError:
            pass

    # 2) "Mês AAAA" no início
    norm = normalizar_texto(s)
    m2 = re.match(r"^([A-Z]+)\s+(\d{4})", norm)
    if m2:
        mes_num = _MESES_PT.get(m2.group(1).lower())
        if mes_num:
            return f"{m2.group(2)}-{mes_num}"

    # 3) MM/YYYY
    m3 = re.search(r"(\d{2})/(\d{4})", s)
    if m3:
        mm, y = int(m3.group(1)), int(m3.group(2))
        if 1 <= mm <= 12:
            return f"{y}-{mm:02d}"

    return None
