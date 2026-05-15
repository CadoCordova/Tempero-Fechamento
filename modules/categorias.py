import json
from pathlib import Path

import streamlit as st

from modules.gdrive import load_json_from_gdrive_history, save_json_to_gdrive_history
from modules.utils import normalizar_texto

RULES_PATH = Path("regras_categorias.json")
CATEGORIAS_PATH = Path("categorias_personalizadas.json")

CATEGORIAS_PADRAO = [
    "Vendas / Receitas",
    "Fornecedores e Insumos",
    "Folha de Pagamento",
    "Aluguel Comercial",
    "Contabilidade e RH",
    "Dedetização / Controle de Pragas",
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


def classificar_categoria(mov: dict, regras: dict | None = None) -> str:
    """
    Classifica um movimento em uma categoria.
    Aceita um dict de regras externo; se None, usa as regras da sessão.
    """
    if regras is None:
        regras = get_regras_sessao()

    desc_orig = mov.get("descricao")
    desc_norm = normalizar_texto(desc_orig)
    valor = mov.get("valor", 0.0)

    for padrao, categoria in regras.items():
        if padrao in desc_norm:
            return categoria

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

    if "CIA ESTADUAL DE DIST" in desc_norm or "CEEE" in desc_norm or "ENERGIA ELETRICA" in desc_norm:
        return "Energia Elétrica"

    if "RECH CONTABILIDADE" in desc_norm or "RECH CONT" in desc_norm:
        return "Contabilidade e RH"

    if any(kw in desc_norm for kw in ("BUSINESS      0503-2852", "BUSINESS 0503-2852",
                                       "ITAU UNIBANCO HOLDING S.A.", "CARTAO")):
        return "Fatura Cartão"

    if any(kw in desc_norm for kw in ("APLICACAO", "CDB", "CREDBANCRF")):
        return "Investimentos (Aplicações)"

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
        return "Fornecedores e Insumos"

    return "A Classificar"
