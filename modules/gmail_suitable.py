"""
Importação automática de lançamentos de dinheiro a partir dos emails de
fechamento de caixa enviados pelo Suitable (envios@suitable.com.br).
"""
import base64
import re
import unicodedata
from datetime import date

import streamlit as st
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from modules.utils import parse_numero_br

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_REMETENTE = "envios@suitable.com.br"
_ASSUNTO = "[temperodasgurias] Fechamento de caixa"

_MESES_PT = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4,
    "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12,
}


# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------

_GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


def _get_gmail_service():
    """
    Cria o cliente Gmail API.
    Ordem de preferência: gmail_oauth → gdrive_oauth (fallback).
    Rejeita credenciais que não tenham o escopo gmail.readonly,
    lançando RuntimeError com orientação clara.
    """
    errors = []
    for secret_key in ("gmail_oauth", "gdrive_oauth"):
        try:
            info = st.secrets[secret_key]
        except Exception:
            continue

        scopes = info.get("scopes", [])
        if isinstance(scopes, str):
            scopes = [scopes]

        # Rejeita imediatamente se o escopo Gmail não estiver presente
        if not any("gmail" in s for s in scopes):
            errors.append(
                f"{secret_key}: escopo gmail.readonly ausente "
                f"(escopos encontrados: {scopes})"
            )
            continue

        creds = Credentials(
            token=info.get("token"),
            refresh_token=info.get("refresh_token"),
            token_uri=info.get("token_uri"),
            client_id=info.get("client_id"),
            client_secret=info.get("client_secret"),
            scopes=scopes,
        )

        try:
            if not creds.valid and creds.refresh_token:
                creds.refresh(Request())
            if creds.valid:
                return build("gmail", "v1", credentials=creds)
        except Exception as e:
            errors.append(f"{secret_key}: {e}")
            continue

    detalhes = "; ".join(errors) if errors else "nenhuma credencial disponível"
    raise RuntimeError(
        f"Não foi possível autenticar com o Gmail ({detalhes}). "
        "Adicione a seção [gmail_oauth] nos secrets do Streamlit Cloud com "
        f"escopo '{_GMAIL_SCOPE}' e token da conta temperodasgurias@gmail.com."
    )


# ---------------------------------------------------------------------------
# Parsing de data
# ---------------------------------------------------------------------------

def _parse_data_pt(texto: str) -> date | None:
    """
    Extrai uma data do formato "DD de Mês de YYYY" (pt-BR) presente no texto.
    Retorna None se não encontrar.
    """
    m = re.search(r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})", texto, re.IGNORECASE)
    if not m:
        return None

    dia = int(m.group(1))
    mes_txt = unicodedata.normalize("NFD", m.group(2).lower()).encode("ascii", "ignore").decode("ascii")
    ano = int(m.group(3))

    mes = _MESES_PT.get(mes_txt)
    if not mes:
        return None

    try:
        return date(ano, mes, dia)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Extração do corpo do email
# ---------------------------------------------------------------------------

def _extrair_html(part: dict) -> str:
    """Percorre recursivamente as parts do Gmail e retorna o primeiro text/html."""
    if part.get("mimeType") == "text/html":
        data = part.get("body", {}).get("data", "")
        if data:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    for subpart in part.get("parts", []):
        result = _extrair_html(subpart)
        if result:
            return result

    return ""


def _get_email_body(service, msg_id: str) -> str:
    msg = service.users().messages().get(
        userId="me", id=msg_id, format="full"
    ).execute()
    return _extrair_html(msg.get("payload", {}))


# ---------------------------------------------------------------------------
# Parsing do HTML do email
# ---------------------------------------------------------------------------

def _parse_email_html(html: str, data_fechamento: date) -> list[dict]:
    """
    Extrai lançamentos de dinheiro do HTML de um email do Suitable.

    Tabelas reconhecidas:
    - "Total por forma de Pagamento" → linha Dinheiro → Entrada
    - "Contas Pagas" → cada linha (exceto total) → Saída individual
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    lancamentos: list[dict] = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        # Cabeçalhos da tabela
        headers = [td.get_text(strip=True).lower() for td in rows[0].find_all(["th", "td"])]
        if not headers:
            continue

        # Texto do elemento mais próximo antes da tabela (h2/h3/p/strong)
        contexto = ""
        for prev in table.find_all_previous(["h1", "h2", "h3", "h4", "p", "strong", "b"], limit=3):
            txt = prev.get_text(strip=True).lower()
            if txt:
                contexto = txt
                break

        is_pagamento = (
            any("forma" in h or "pagamento" in h for h in headers)
            or "forma de pagamento" in contexto
            or "total por forma" in contexto
        )
        is_contas = (
            "contas pagas" in contexto
            or "conta" in contexto
            or (
                not is_pagamento
                and any("conta" in h or "descri" in h for h in headers)
            )
        )

        if is_pagamento:
            valor_idx = next((i for i, h in enumerate(headers) if "valor" in h), len(headers) - 1)
            forma_idx = next(
                (i for i, h in enumerate(headers) if "forma" in h or "pagamento" in h), 0
            )

            for row in rows[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) <= max(forma_idx, valor_idx):
                    continue
                forma = cols[forma_idx].get_text(strip=True).lower()
                if "dinheiro" not in forma:
                    continue
                valor = parse_numero_br(cols[valor_idx].get_text(strip=True))
                if valor > 0:
                    lancamentos.append({
                        "Data": data_fechamento,
                        "Descrição": "Vendas em dinheiro (Suitable)",
                        "Tipo": "Entrada",
                        "Valor": valor,
                    })

        elif is_contas:
            valor_idx = next((i for i, h in enumerate(headers) if "valor" in h), len(headers) - 1)
            desc_idx = 0

            for row in rows[1:]:
                cols = row.find_all(["td", "th"])
                if len(cols) <= max(desc_idx, valor_idx):
                    continue
                descricao = cols[desc_idx].get_text(strip=True)
                if not descricao or "total" in descricao.lower():
                    continue
                valor = parse_numero_br(cols[valor_idx].get_text(strip=True))
                if valor > 0:
                    lancamentos.append({
                        "Data": data_fechamento,
                        "Descrição": f"{descricao} (Suitable)",
                        "Tipo": "Saída",
                        "Valor": valor,
                    })

    return lancamentos


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def buscar_fechamentos_gmail(ano_mes: str) -> list[dict]:
    """
    Busca emails de fechamento do Suitable para o mês ano_mes (YYYY-MM).

    Retorna lista de lançamentos no formato do caixa diário:
    [{"Data": date, "Descrição": str, "Tipo": "Entrada"|"Saída", "Valor": float}]

    Lança RuntimeError em caso de falha de autenticação ou formato inválido.
    """
    try:
        ano, mes = int(ano_mes[:4]), int(ano_mes[5:7])
    except (ValueError, IndexError):
        raise ValueError(f"Formato inválido: {ano_mes!r}. Use YYYY-MM.")

    prox_ano, prox_mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)

    service = _get_gmail_service()

    query = (
        f"from:{_REMETENTE} "
        f'subject:"{_ASSUNTO}" '
        f"after:{ano}/{mes:02d}/01 "
        f"before:{prox_ano}/{prox_mes:02d}/01"
    )

    results = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=100)
        .execute()
    )
    messages = results.get("messages", [])

    todos: list[dict] = []
    for msg_ref in messages:
        html = _get_email_body(service, msg_ref["id"])
        if not html:
            continue

        data_fechamento = _parse_data_pt(html)
        if not data_fechamento:
            continue

        lancamentos = _parse_email_html(html, data_fechamento)
        todos.extend(lancamentos)

    return todos
