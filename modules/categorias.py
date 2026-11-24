import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd
import streamlit as st

from modules.gdrive import load_json_from_gdrive_history, save_json_to_gdrive_history
from modules.utils import normalizar_texto

RULES_PATH = Path("regras_categorias.json")
RECLASS_PATH = Path("reclassificacoes_pontuais.json")
# Fornecedores/contrapartes já conhecidos (gerado a partir dos fechamentos salvos).
SEED_PATH = Path(__file__).resolve().parent.parent / "regras_iniciais.json"

# Categorias de pessoas: uma ENTRADA de funcionária/prestador (ex.: PIX de marmita) é receita, não
# redução de folha.
CATS_PESSOAS = ("Folha de Pagamento", "Nutricionista", "Pagamento Terceiros")
CATEGORIAS_PATH = Path("categorias_personalizadas.json")

CATEGORIAS_PADRAO = [
    "Vendas / Receitas",
    "Fornecedores e Insumos",
    "Folha de Pagamento",
    "Aluguel Comercial",
    "Contabilidade e RH",
    "Dedetização / Controle de Pragas",
    "Limpeza de Coifa",
    "Energia Elétrica",
    "Motoboy / Entregas",
    "Internet",
    "Sangria",
    "Nutricionista",
    "Impostos e Encargos",
    "Investimentos (Aplicações)",
    "Rendimentos de Aplicações",
    "Fatura Cartão",
    "Transferência Interna / Sócios",
    "Investimento na Loja (Imobilizado)",
    "A Classificar",
]


# ---------------------------------------------------------------------------
# Persistência de regras
# ---------------------------------------------------------------------------

def carregar_regras() -> dict:
    """
    Carrega regras de categorização.
    Prioridade: Google Drive → arquivo local.
    """
    # 1) tenta Drive (sobrevive a redeploys no Streamlit Cloud)
    data_drive = load_json_from_gdrive_history(RULES_PATH.name)
    if isinstance(data_drive, dict):
        return data_drive

    # 2) fallback local
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
    """Salva regras localmente e no Google Drive."""
    with RULES_PATH.open("w", encoding="utf-8") as f:
        json.dump(regras, f, ensure_ascii=False, indent=2)

    try:
        save_json_to_gdrive_history(RULES_PATH.name, regras)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Reclassificações pontuais (um lançamento específico, sem virar regra)
# ---------------------------------------------------------------------------
# Regras por descrição valem para TODOS os lançamentos iguais (ex.: todo PIX para
# a mesma pessoa). Quando só um lançamento é diferente (ex.: PIX de R$ 42.000 que foi
# compra de veículo da loja), ele é guardado por data + valor + descrição.

def chave_reclassificacao(data, valor, descricao) -> str:
    dt = pd.to_datetime(data, dayfirst=True, errors="coerce")
    dia = dt.strftime("%Y-%m-%d") if not pd.isna(dt) else str(data)
    try:
        v = f"{abs(float(valor)):.2f}"
    except (TypeError, ValueError):
        v = str(valor)
    return f"{dia}|{v}|{normalizar_texto(descricao)[:60].strip()}"


def carregar_reclassificacoes() -> dict:
    """Drive → arquivo local. Retorna {chave: categoria}."""
    data_drive = load_json_from_gdrive_history(RECLASS_PATH.name)
    if isinstance(data_drive, dict):
        return data_drive
    if RECLASS_PATH.exists():
        try:
            with RECLASS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
    return {}


def salvar_reclassificacoes(reclass: dict):
    """Salva no Drive (erro de Drive sobe para a tela: não pode falhar em silêncio)."""
    save_json_to_gdrive_history(RECLASS_PATH.name, reclass)
    try:
        with RECLASS_PATH.open("w", encoding="utf-8") as f:
            json.dump(reclass, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def get_reclassificacoes_sessao() -> dict:
    if "reclassificacoes" not in st.session_state:
        st.session_state["reclassificacoes"] = carregar_reclassificacoes()
    return st.session_state["reclassificacoes"]


def reload_reclassificacoes_sessao():
    st.session_state["reclassificacoes"] = carregar_reclassificacoes()


# ---------------------------------------------------------------------------
# Categorias personalizadas
# ---------------------------------------------------------------------------

def carregar_categorias_personalizadas() -> list[str]:
    """
    Carrega categorias personalizadas.
    Prioridade: Google Drive → arquivo local.
    """
    categorias = []

    data_drive = load_json_from_gdrive_history(CATEGORIAS_PATH.name)
    if isinstance(data_drive, list):
        categorias.extend(c for c in data_drive if isinstance(c, str) and c.strip())

    if CATEGORIAS_PATH.exists():
        try:
            with CATEGORIAS_PATH.open("r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    categorias.extend(c for c in data if isinstance(c, str) and c.strip())
        except Exception:
            pass

    seen: set[str] = set()
    out = []
    for c in categorias:
        c = c.strip()
        if c and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def salvar_categorias_personalizadas(lista: list[str]):
    """Salva categorias personalizadas localmente e no Google Drive."""
    seen: set[str] = set()
    norm = []
    for c in (lista or []):
        if isinstance(c, str):
            c = c.strip()
            if c and c not in seen:
                norm.append(c)
                seen.add(c)

    with CATEGORIAS_PATH.open("w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)

    try:
        save_json_to_gdrive_history(CATEGORIAS_PATH.name, norm)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Contraparte (quem recebeu/pagou) e regras iniciais
# ---------------------------------------------------------------------------

_SEG_NUMERICO = re.compile(r"^[-+]?[\d\.\,\s/\-]+$")


def chave_contraparte(descricao, conta=None) -> str | None:
    """
    Nome da contraparte na descrição do extrato, sem valor/CPF/CNPJ, para regras que
    valem em qualquer mês (a descrição completa traz o valor e só repete se o valor repetir).
    Itaú: "TIPO | NOME | CPF/CNPJ | valor | ..." → NOME.  PagSeguro: "NOME | ... | tipo" → NOME.
    Retorna None quando não há nome identificável (ex.: "RESGATE CDB DI | nan | 42961.82").
    """
    segs = [s.strip() for s in str(descricao or "").split("|") if s.strip()]
    if len(segs) < 2:
        return None
    nome = segs[0] if str(conta) == "PagSeguro" else segs[1]
    n = normalizar_texto(nome).strip()
    n = re.sub(r"\s+\d{9,14}\s*$", "", n)
    if not n or n == "NAN" or _SEG_NUMERICO.match(n) or len(n) < 8:
        return None
    return n


@lru_cache(maxsize=1)
def carregar_regras_iniciais() -> dict:
    try:
        with SEED_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Classificação
# ---------------------------------------------------------------------------

def get_regras_sessao() -> dict:
    """Retorna as regras de categorização armazenadas na sessão."""
    if "regras_categoria" not in st.session_state:
        st.session_state["regras_categoria"] = carregar_regras()
    return st.session_state["regras_categoria"]


def reload_regras_sessao():
    """Força recarga das regras do disco para a sessão."""
    st.session_state["regras_categoria"] = carregar_regras()


def classificar_categoria(mov: dict, regras: dict | None = None, reclass: dict | None = None) -> str:
    """
    Classifica um movimento em uma categoria.
    Aceita um dict de regras externo; se None, usa as regras da sessão.
    Reclassificação pontual (data + valor + descrição) vence qualquer regra.
    """
    if regras is None:
        regras = get_regras_sessao()
    if reclass is None:
        reclass = get_reclassificacoes_sessao()

    desc_orig = mov.get("descricao")
    desc_norm = normalizar_texto(desc_orig)
    valor = mov.get("valor", 0.0)
    conta = mov.get("conta")

    if reclass:
        chave = chave_reclassificacao(mov.get("data"), valor, desc_orig)
        if chave in reclass:
            return reclass[chave]

    for padrao, categoria in regras.items():
        if padrao in desc_norm:
            return categoria

    # Contraparte já conhecida (fornecedor, concessionária etc.) — só para saídas
    if valor < 0:
        contraparte = chave_contraparte(desc_orig, conta)
        if contraparte:
            categoria_seed = carregar_regras_iniciais().get(contraparte)
            if categoria_seed:
                return categoria_seed

    categoria = _classificar_por_palavras(desc_norm, valor, conta)

    # Entrada de funcionária/prestador (ex.: PIX de marmita) é receita, não redução de custo
    if valor > 0 and categoria in CATS_PESSOAS:
        return "Vendas / Receitas"
    return categoria


def _classificar_por_palavras(desc_norm: str, valor: float, conta: str | None) -> str:
    """Regras por palavra-chave e padrão final (valor > 0 receita; valor < 0 a classificar)."""
    if "SANGRIA" in desc_norm:
        return "Sangria"

    if "RECEITA FEDERAL" in desc_norm or "RFB" in desc_norm:
        return "Impostos e Encargos"

    if "CLARO" in desc_norm:
        return "Internet"
    if "VIVO" in desc_norm and any(
        kw in desc_norm for kw in ("CONCESSIONARIA", "VIVO-RS")
    ):
        return "Internet"

    if "ANTINSECT" in desc_norm:
        return "Dedetização / Controle de Pragas"

    if "ECO PRO" in desc_norm and valor < 0:
        return "Limpeza de Coifa"

    if "CIA ESTADUAL DE DIST" in desc_norm or "CEEE" in desc_norm or "ENERGIA ELETRICA" in desc_norm:
        return "Energia Elétrica"

    if "RECH CONTABILIDADE" in desc_norm or "RECH CONT" in desc_norm:
        return "Contabilidade e RH"

    if any(kw in desc_norm for kw in ("BUSINESS      0503-2852", "BUSINESS 0503-2852",
                                       "ITAU UNIBANCO HOLDING S.A.", "CARTAO")):
        return "Fatura Cartão"

    if any(kw in desc_norm for kw in ("APLICACAO", "CDB", "CREDBANCRF", "ITAUVEST", "RESGATE")) or desc_norm.startswith("APLIC "):
        return "Investimentos (Aplicações)"

    # Tarifas do banco ("TAR PLANO ADAPT", "TAR PIXQR LIQ MAQUININHA"...) e taxa estadual (SEFAZ)
    if desc_norm.startswith("TAR "):
        return "Tarifas Bancos"
    if "SEFAZ" in desc_norm:
        return "Impostos e Encargos"

    if any(kw in desc_norm for kw in ("REND PAGO APLIC", "RENDIMENTO APLIC", "REND APLIC", "RENDIMENTO")):
        return "Rendimentos de Aplicações"

    if "ZOOP" in desc_norm or "ALUGUEL" in desc_norm:
        return "Aluguel Comercial"

    if "MOTOBOY" in desc_norm or "ENTREGA" in desc_norm:
        return "Motoboy / Entregas"

    if any(kw in desc_norm for kw in ("CAROLINE", "VERONICA", "EVELLYN", "SALARIO", "FOLHA")):
        return "Folha de Pagamento"

    if "ANA PAULA" in desc_norm or "NUTRICIONISTA" in desc_norm:
        return "Nutricionista"

    if any(kw in desc_norm for kw in ("DARF", "GPS", "FGTS", "INSS", "SIMPLES NACIONAL", "IMPOSTO")):
        return "Impostos e Encargos"

    if any(kw in desc_norm for kw in ("TRANSFERENCIA", "PIX")) and any(
        kw in desc_norm for kw in ("RICARDO", "LIZIANI", "LIZI")
    ):
        return "Transferência Interna / Sócios"

    if valor > 0:
        return "Vendas / Receitas"
    if valor < 0:
        # Saída sem regra nenhuma: antes virava "Fornecedores" em silêncio (e entrava no CMV).
        # Agora fica para você classificar. Exceção: lançamentos do caixa em dinheiro
        # (contas pagas do Suitable), que são pequenos e quase sempre insumo.
        if conta == "Dinheiro":
            return "Fornecedores e Insumos"
        return "A Classificar"

    return "A Classificar"
