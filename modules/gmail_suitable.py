"""
Importação automática de lançamentos de dinheiro a partir dos emails de
fechamento de caixa enviados pelo Suitable (envios@suitable.com.br).
"""
import base64
import re
import unicodedata
from datetime import date, timedelta

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

def _parse_data_hora_pt(texto: str) -> tuple[date, int] | tuple[None, None]:
    """
    Aceita a data por extenso do relatório antigo e o intervalo MOVIMENTO
    do relatório novo. Usa a data/hora FINAL do intervalo.
    Retorna (date, hora_int) ou (None, None) se a data/hora for inválida.
    Sem hora no formato antigo, retorna 11 para preservar a regra original.
    """
    from bs4 import BeautifulSoup

    # Tags e entidades HTML podem separar os componentes do cabeçalho.
    texto = BeautifulSoup(texto, "html.parser").get_text(" ", strip=True)
    texto = " ".join(texto.split())

    m = re.search(
        r"\bDE\s+\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}"
        r"\s+A\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2})\b",
        texto, re.IGNORECASE,
    )
    if m:
        dia, mes, ano, hora, minuto = map(int, m.groups())
    else:
        m = re.search(
            r"(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})"
            r"(?:\s+[àa]s?\s+(\d{1,2}):(\d{2}))?",
            texto, re.IGNORECASE,
        )
        if not m:
            return None, None

        dia = int(m.group(1))
        mes_txt = unicodedata.normalize("NFD", m.group(2).lower()).encode("ascii", "ignore").decode("ascii")
        mes = _MESES_PT.get(mes_txt)
        if not mes:
            return None, None
        ano = int(m.group(3))
        hora = int(m.group(4)) if m.group(4) is not None else 11
        minuto = int(m.group(5)) if m.group(5) is not None else 0

    if not (0 <= hora <= 23 and 0 <= minuto <= 59):
        return None, None
    try:
        return date(ano, mes, dia), hora
    except ValueError:
        return None, None


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
    - "Total por forma de Pagamento" → linha Dinheiro → Entrada (antigo)
    - "Pedidos por Meio de Pagamento" → linha (+) DINHEIRO → Entrada (novo)
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

        is_pedidos = "pedidos por meio de pagamento" in contexto
        # Reconhece as tabelas de vendas dos dois formatos.
        # Saldos de fechamento não representam vendas em dinheiro.
        is_total_pagamento = (
            ("total por forma" in contexto or is_pedidos)
            and "saldo" not in contexto
        )
        is_contas = "contas pagas" in contexto

        if is_total_pagamento:
            valor_idx = next((i for i, h in enumerate(headers) if "valor" in h), len(headers) - 1)
            forma_idx = next(
                (i for i, h in enumerate(headers) if "forma" in h or "pagamento" in h), 0
            )

            secao = ""
            din_secao = 0.0   # linha (+) DINHEIRO da seção Recebimentos em Dinheiro
            recebimento_off = 0.0  # Recebimento OFF (não é dinheiro na gaveta)
            for row in rows[1:]:
                cols = row.find_all(["td", "th"])
                if is_pedidos and len(cols) == 1:
                    secao = " ".join(cols[0].get_text(" ", strip=True).lower().split())
                    continue
                if len(cols) <= max(forma_idx, valor_idx):
                    continue
                forma = " ".join(cols[forma_idx].get_text(" ", strip=True).lower().split())
                # O novo relatório soma o "Recebimento OFF" na linha (+) DINHEIRO,
                # mas esse valor não entra na gaveta. Lê o OFF para descontá-lo.
                if is_pedidos and re.fullmatch(r"(?:\(\+\)\s*)?recebimento off", forma):
                    recebimento_off += parse_numero_br(cols[valor_idx].get_text(strip=True))
                    continue
                # O novo relatório repete (+) Dinheiro em Recebimentos em Cartão.
                # Importa exclusivamente a linha da seção Recebimentos em Dinheiro.
                if is_pedidos and secao != "recebimentos em dinheiro":
                    continue
                # No formato novo há também pagamentos e saldo em dinheiro.
                # Somente Dinheiro / (+) DINHEIRO representam vendas.
                if not re.fullmatch(r"(?:\(\+\)\s*)?dinheiro", forma):
                    continue
                valor = parse_numero_br(cols[valor_idx].get_text(strip=True))
                if is_pedidos:
                    din_secao += valor
                elif valor > 0:
                    lancamentos.append({
                        "Data": data_fechamento,
                        "Descrição": "Vendas em dinheiro (Suitable)",
                        "Tipo": "Entrada",
                        "Valor": valor,
                    })

            if is_pedidos:
                valor = round(din_secao - recebimento_off, 2)
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


def _extrair_info_caixa(html: str) -> dict:
    """
    Extrai do email (sem gerar lançamentos):
    - recebimento_off: valor de "Recebimento OFF" (venda que não entrou na gaveta)
    - saldo_dinheiro: "Dinheiro" (valor esperado) da tabela "Saldos de Fechamento
      por Forma de Pagamento" = dinheiro físico na gaveta ao fechar.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    info = {"recebimento_off": 0.0, "saldo_dinheiro": None}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        contexto = ""
        for prev in table.find_all_previous(["h1", "h2", "h3", "h4", "p", "strong", "b"], limit=3):
            txt = prev.get_text(strip=True).lower()
            if txt:
                contexto = txt
                break
        for row in rows[1:]:
            cols = row.find_all(["td", "th"])
            if len(cols) < 2:
                continue
            forma = " ".join(cols[0].get_text(" ", strip=True).lower().split())
            if "saldos de fechamento" in contexto and forma == "dinheiro":
                info["saldo_dinheiro"] = parse_numero_br(cols[1].get_text(strip=True))
            elif "pedidos por meio de pagamento" in contexto and re.fullmatch(
                r"(?:\(\+\)\s*)?recebimento off", forma
            ):
                info["recebimento_off"] += parse_numero_br(cols[-1].get_text(strip=True))
    return info


# ---------------------------------------------------------------------------
# Função principal
# ---------------------------------------------------------------------------

def buscar_fechamentos_gmail(ano_mes: str) -> list[dict]:
    """Compatibilidade: retorna apenas os lançamentos."""
    return buscar_fechamentos_gmail_com_info(ano_mes)[0]


def buscar_fechamentos_gmail_com_info(ano_mes: str) -> tuple[list[dict], dict]:
    """
    Igual a buscar_fechamentos_gmail, mas retorna também informações de conferência:
    {"avisos": [str], "ultimo_saldo": (date, float) | None}.

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
    avisos: list[str] = []
    ultimo_saldo = None
    for msg_ref in messages:
        html = _get_email_body(service, msg_ref["id"])
        if not html:
            continue

        data_email, hora_fechamento = _parse_data_hora_pt(html)
        if not data_email:
            continue

        # Fechamentos antes das 11h referem-se ao caixa do dia anterior
        if hora_fechamento < 11:
            data_fechamento = data_email - timedelta(days=1)
        else:
            data_fechamento = data_email

        # Descarta emails cujo lançamento pertence a outro mês
        if data_fechamento.year != ano or data_fechamento.month != mes:
            continue

        lancamentos = _parse_email_html(html, data_fechamento)
        todos.extend(lancamentos)

        info = _extrair_info_caixa(html)
        if info["recebimento_off"] > 0:
            _off = f"{info['recebimento_off']:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            avisos.append(
                f"{data_fechamento:%d/%m/%Y}: Recebimento OFF de R\\$ {_off} "
                "não foi lançado como dinheiro (não entrou na gaveta). Conferir com Itaú/PagSeguro."
            )
        if info["saldo_dinheiro"] is not None and (
            ultimo_saldo is None or data_fechamento >= ultimo_saldo[0]
        ):
            ultimo_saldo = (data_fechamento, info["saldo_dinheiro"])

    avisos.sort()
    return todos, {"avisos": avisos, "ultimo_saldo": ultimo_saldo}
