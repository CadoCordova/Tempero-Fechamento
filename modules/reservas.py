"""
Saldos das reservas (digitados à mão) e conciliação com os fechamentos.

O extrato mostra só o MOVIMENTO das aplicações (colocou / tirou), nunca o saldo. Aqui o saldo real de cada
reserva é lançado manualmente (idealmente no fim do mês) e comparado com o que os fechamentos explicam:

    saldo anterior + colocou − tirou = saldo esperado
    saldo real − saldo esperado      = rendimento do período (não aparece no extrato do CDB)

Duas reservas, cada uma na sua linha:
  - Reserva da loja (Itaú): CDB do Itaú.
  - Reserva trabalhista (PagBank): investimentos do PagBank (+ opcionalmente parte do saldo em conta reservada).
"""
from __future__ import annotations

import calendar
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from modules.gdrive import load_json_from_gdrive_history, save_json_to_gdrive_history
from modules.gerencial import BOLSAO_LOJA, BOLSAO_TRABALHISTA, reservas_por_bolsao

ARQUIVO = "saldos_reservas.json"
CAMINHO_LOCAL = Path(ARQUIVO)
BOLSOES_SALDO = (BOLSAO_LOJA, BOLSAO_TRABALHISTA)
DIAS_ALERTA_DEFASAGEM = 35
PARAMETROS_PADRAO = {"passivo_trabalhista": 28714.0, "piso_loja": 10000.0}


def _vazio() -> dict:
    return {"entradas": [], "parametros": dict(PARAMETROS_PADRAO)}


def _normalizar(dados) -> dict:
    out = _vazio()
    if isinstance(dados, dict):
        ent = dados.get("entradas")
        if isinstance(ent, list):
            out["entradas"] = [e for e in ent if isinstance(e, dict) and e.get("data") and e.get("bolsao")]
        par = dados.get("parametros")
        if isinstance(par, dict):
            for k in PARAMETROS_PADRAO:
                try:
                    out["parametros"][k] = float(par.get(k, PARAMETROS_PADRAO[k]))
                except (TypeError, ValueError):
                    pass
    return out


def carregar_saldos() -> dict:
    """Drive primeiro (sobrevive a redeploy); cai para o arquivo local."""
    dados = load_json_from_gdrive_history(ARQUIVO)
    if isinstance(dados, dict):
        return _normalizar(dados)
    if CAMINHO_LOCAL.exists():
        try:
            return _normalizar(json.loads(CAMINHO_LOCAL.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            pass
    return _vazio()


def salvar_saldos(dados: dict) -> None:
    """Grava local e no Drive. Se o Drive falhar, levanta erro (dado digitado à mão não pode sumir calado)."""
    dados = _normalizar(dados)
    try:
        CAMINHO_LOCAL.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    try:
        save_json_to_gdrive_history(ARQUIVO, dados)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"Não consegui salvar no Drive: {e}") from e


def _data(txt) -> date:
    return pd.to_datetime(txt).date()


def registrar_saldo(
    dados: dict, data_ref: date, bolsao: str, liquido: float,
    bruto: float | None = None, conta: float = 0.0, obs: str = "",
) -> dict:
    """Insere/atualiza o saldo de (data, bolsão). Devolve novo dict (não altera o recebido)."""
    if bolsao not in BOLSOES_SALDO:
        raise ValueError("Reserva inválida.")
    if liquido is None or liquido < 0:
        raise ValueError("Informe o saldo líquido (zero ou mais).")
    if bruto is not None and bruto < 0:
        raise ValueError("Saldo bruto não pode ser negativo.")
    if conta is not None and conta < 0:
        raise ValueError("Saldo em conta não pode ser negativo.")
    chave = str(data_ref)
    novo = _normalizar(dados)
    novo["entradas"] = [e for e in novo["entradas"] if not (e["data"] == chave and e["bolsao"] == bolsao)]
    novo["entradas"].append({
        "data": chave, "bolsao": bolsao, "liquido": round(float(liquido), 2),
        "bruto": None if bruto is None else round(float(bruto), 2),
        "conta": round(float(conta or 0.0), 2), "obs": str(obs or "").strip(),
    })
    novo["entradas"].sort(key=lambda e: (e["data"], e["bolsao"]))
    return novo


def remover_saldo(dados: dict, data_ref: str, bolsao: str) -> dict:
    novo = _normalizar(dados)
    novo["entradas"] = [e for e in novo["entradas"] if not (e["data"] == str(data_ref) and e["bolsao"] == bolsao)]
    return novo


def entradas_do_bolsao(dados: dict, bolsao: str) -> list[dict]:
    return sorted((e for e in dados.get("entradas", []) if e["bolsao"] == bolsao), key=lambda e: e["data"])


def ultimo_saldo(dados: dict, bolsao: str) -> dict | None:
    lst = entradas_do_bolsao(dados, bolsao)
    return lst[-1] if lst else None


def total_do_registro(e: dict | None) -> float:
    return 0.0 if not e else float(e.get("liquido") or 0.0) + float(e.get("conta") or 0.0)


def cobertura(dados: dict) -> dict:
    """Leitura da regra de uso: o dinheiro de funcionários sai do PagBank e, se faltar, do Itaú."""
    par = _normalizar(dados)["parametros"]
    trab = total_do_registro(ultimo_saldo(dados, BOLSAO_TRABALHISTA))
    loja = total_do_registro(ultimo_saldo(dados, BOLSAO_LOJA))
    passivo, piso = par["passivo_trabalhista"], par["piso_loja"]
    return {
        "trabalhista": trab, "loja": loja, "passivo": passivo, "piso": piso,
        "cobertura_pagbank": (trab / passivo) if passivo else None,
        "cobertura_pagbank_mais_itau": ((trab + loja) / passivo) if passivo else None,
        "loja_vs_piso": (loja / piso) if piso else None,
        "total": trab + loja,
    }


def dias_sem_atualizar(dados: dict, hoje: date | None = None) -> dict[str, int | None]:
    hoje = hoje or date.today()
    out: dict[str, int | None] = {}
    for b in BOLSOES_SALDO:
        u = ultimo_saldo(dados, b)
        out[b] = (hoje - _data(u["data"])).days if u else None
    return out


def _meses_entre(d0: date, d1: date) -> list[str]:
    """Meses (YYYY-MM) tocados por (d0, d1], ou seja, a partir do dia seguinte a d0."""
    ini = pd.Timestamp(d0) + pd.Timedelta(days=1)
    if ini.date() > d1:
        return []
    return [str(p) for p in pd.period_range(ini, pd.Timestamp(d1), freq="M")]


def conciliar(dados: dict, reps: dict[str, dict], bolsao: str) -> list[dict]:
    """
    Compara cada par de saldos consecutivos de um bolsão com o movimento dos fechamentos salvos.
    reps: {YYYY-MM: relatório com a aba movimentos}. Só concilia o período se todos os meses tocados por
    ele já estiverem salvos E o mês do saldo final estiver fechado até o fim (ou o saldo final for dentro de um
    mês salvo); senão devolve coberto=False.
    """
    lst = entradas_do_bolsao(dados, bolsao)
    out = []
    for a, b in zip(lst, lst[1:]):
        d0, d1 = _data(a["data"]), _data(b["data"])
        meses = _meses_entre(d0, d1)
        faltando = [m for m in meses if m not in reps]
        linha = {
            "de": a["data"], "ate": b["data"], "saldo_ini": float(a["liquido"]),
            "saldo_fim": float(b["liquido"]), "colocou": 0.0, "tirou": 0.0,
            "esperado": None, "diferenca": None, "rendimento_pct": None,
            "coberto": not faltando, "faltando": faltando,
        }
        if not faltando:
            colocou = tirou = 0.0
            for m in meses:
                df = reps[m].get("movimentos")
                if df is None or len(df) == 0:
                    continue
                df = df.copy()
                dt = pd.to_datetime(df["Data"], errors="coerce").dt.date
                df = df[(dt > d0) & (dt <= d1)]
                r = reservas_por_bolsao(df).get(bolsao, {})
                colocou += r.get("aplicado", 0.0)
                tirou += r.get("resgatado", 0.0)
            linha["colocou"], linha["tirou"] = colocou, tirou
            linha["esperado"] = linha["saldo_ini"] + colocou - tirou
            linha["diferenca"] = linha["saldo_fim"] - linha["esperado"]
            base = linha["saldo_ini"] + colocou
            linha["rendimento_pct"] = (linha["diferenca"] / base) if base > 0 else None
        out.append(linha)
    return out
