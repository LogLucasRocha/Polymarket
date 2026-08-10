"""Captura ao vivo dos mercados binários/multi-strike diários (observação).

A cada rodada (10 min no main.yml), para cada mercado do registro ``MERCADOS``,
tira um snapshot do dia (D0, no fuso do mercado):

- kind="binary" (SPY): um mercado, dois lados (Up/Down) — um contrato por dia.
- kind="strikes" (Bitcoin Above): vários strikes ("acima de 60k?", "acima de
  62k?", ...), cada um Yes/No — um contrato por strike (a ``faixa``).

Guardamos os dois lados posicionalmente (lado A → "up"/"Yes", lado B →
"down"/"No"), sem depender do nome do desfecho. Fim de semana/feriado sem
mercado: a rodada apenas não grava.

Grava em data_{key}/ (buffer do dia UTC corrente, entra no zip do Atualizar) e
dados_{key}/ (parquet por dia UTC fechado, commitado). Só arquiva; não envia
alerta nem ordem.

Roda no .github/workflows/main.yml. Uso: python -m spy.capture
"""
from __future__ import annotations

try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # noqa: BLE001
    pass

import datetime as dt
import json
import sys

import requests

from tmax import config
from tmax import polymarket as pm

from . import MERCADOS, Mercado, close_utc, market_date


def market_slug(prefix: str, d: dt.date) -> str:
    """Slug determinístico do dia, ex.: spy-up-or-down-on-august-7-2026."""
    return f"{prefix}-{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}"


def spy_slug(d: dt.date) -> str:
    """Compat.: slug do SPY (o registro é a fonte da verdade)."""
    return market_slug(MERCADOS["spy"].slug_prefix, d)


def _best_ask(book: dict | None) -> tuple[float | None, float | None]:
    asks = book.get("asks", []) if book else []
    valid = []
    for ask in asks:
        try:
            price, size = float(ask["price"]), float(ask["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 < price < 1 and size > 0:
            valid.append((price, size))
    if not valid:
        return None, None
    return min(valid, key=lambda item: item[0])


def _side(outcomes, prices, token_ids, i: int) -> dict:
    try:
        price = float(prices[i])
    except (IndexError, TypeError, ValueError):
        price = None
    return {"price": price,
            "token_id": str(token_ids[i]) if i < len(token_ids) else None,
            "label": str(outcomes[i]) if i < len(outcomes) else None}


def fetch_market(slug: str, kind: str, timeout: int = 30) -> list[dict] | None:
    """Lista de contratos do evento do dia. None se não existe hoje.

    binary → um contrato (faixa "—"); strikes → um por strike (faixa = o alvo).
    Cada contrato: {faixa, up, down} pegando os dois desfechos por posição."""
    response = requests.get(
        f"{pm.GAMMA_API}/events", params={"slug": slug},
        headers={"User-Agent": config.USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    event = data[0] if isinstance(data, list) and data else data
    if not isinstance(event, dict) or not event.get("markets"):
        return None
    markets = event["markets"]
    entries = markets if kind == "strikes" else markets[:1]
    out = []
    for market in entries:
        outcomes = pm._as_list(market.get("outcomes"))
        prices = pm._as_list(market.get("outcomePrices"))
        token_ids = pm._as_list(market.get("clobTokenIds"))
        if len(outcomes) < 2:
            continue
        faixa = "—"
        if kind == "strikes":
            faixa = str(market.get("groupItemTitle")
                        or market.get("question") or "—")
        out.append({
            "faixa": faixa,
            "up": _side(outcomes, prices, token_ids, 0),
            "down": _side(outcomes, prices, token_ids, 1),
        })
    return out or None


def _attach_asks(entries: list[dict], timeout: int = 30) -> None:
    """Melhor ask executável de cada lado de cada contrato, num livro só."""
    tokens = [entry[side].get("token_id")
              for entry in entries for side in ("up", "down")
              if entry[side].get("token_id")]
    books: dict[str, dict] = {}
    if tokens:
        response = requests.post(
            f"{pm.CLOB_API}/books",
            json=[{"token_id": token} for token in tokens],
            headers={"User-Agent": config.USER_AGENT}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            books = {str(book.get("asset_id")): book for book in data
                     if isinstance(book, dict) and book.get("asset_id")}
    for entry in entries:
        for side in ("up", "down"):
            ask, size = _best_ask(books.get(str(entry[side].get("token_id"))))
            entry[side]["ask"] = ask
            entry[side]["ask_size"] = size


def coletar(mercado: Mercado) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    d0 = market_date(mercado, now)
    slug = market_slug(mercado.slug_prefix, d0)
    try:
        entries = fetch_market(slug, mercado.kind)
        if not entries:
            print(f"sem mercado {mercado.key} hoje ({slug})")
            return []
        _attach_asks(entries)
    except Exception as exc:  # noqa: BLE001 — captura é acessória
        print(f"captura {mercado.key} falhou: {exc}", file=sys.stderr)
        return []
    recs = []
    for entry in entries:
        recs.append({
            "ts_utc": now.isoformat(), "dia": d0.isoformat(), "slug": slug,
            "faixa": entry["faixa"],
            "preco_up": entry["up"].get("price"),
            "preco_down": entry["down"].get("price"),
            "up_label": entry["up"].get("label"),
            "down_label": entry["down"].get("label"),
            "up_token_id": entry["up"].get("token_id"),
            "down_token_id": entry["down"].get("token_id"),
            "ask_up": entry["up"].get("ask"),
            "ask_up_volume": entry["up"].get("ask_size"),
            "ask_down": entry["down"].get("ask"),
            "ask_down_volume": entry["down"].get("ask_size"),
            "livro_consultado": True,
        })
    resumo = ", ".join(
        f"{r['faixa']}={r['preco_up']}/{r['preco_down']}" for r in recs[:4])
    print(f"{mercado.key} {d0}: {len(recs)} contrato(s) · {resumo}")
    return recs


def salvar(mercado: Mercado, recs: list[dict]) -> None:
    import pandas as pd

    buf_dir = config.ROOT / f"data_{mercado.key}"
    arch_dir = config.ROOT / f"dados_{mercado.key}"
    buf_dir.mkdir(exist_ok=True)
    buf = buf_dir / "mercado.jsonl"
    if recs:
        with open(buf, "a", encoding="utf-8") as handle:
            for record in recs:
                handle.write(json.dumps(record, default=str) + "\n")
    if not buf.exists():
        return
    now = dt.datetime.now(dt.timezone.utc)
    with buf.open(encoding="utf-8") as handle:
        linhas = [json.loads(line) for line in handle if line.strip()]
    if not linhas:
        return
    df = pd.DataFrame(linhas)
    if "faixa" not in df:
        df["faixa"] = "—"

    # Arquiva por DIA DE MERCADO assim que o mercado dele FECHOU (não na meia-
    # noite UTC). Um mercado rolling (Bitcoin) tem o dia cruzando a meia-noite
    # UTC; arquivar só na virada UTC deixava a metade perto do fechamento até
    # ~16h parada no buffer efêmero — e um reset da cache do Actions perdia
    # exatamente H-1…H-12. Fechou, vai pro permanente na próxima rodada.
    def _closed(dia_str: str) -> bool:
        try:
            d = dt.date.fromisoformat(str(dia_str))
        except ValueError:
            return False
        return now >= close_utc(mercado, d)

    df["_closed"] = df["dia"].map(_closed)
    (arch_dir / "mercado").mkdir(parents=True, exist_ok=True)
    for dia, group in df[df["_closed"]].groupby("dia"):
        fp = arch_dir / "mercado" / f"{dia}.parquet"
        group = group.drop(columns=["_closed"])
        if fp.exists():
            group = pd.concat([pd.read_parquet(fp), group], ignore_index=True)
        group = group.drop_duplicates(["ts_utc", "dia", "faixa"])
        group.to_parquet(fp, index=False)
        print(f"arquivado {mercado.key}/mercado/{dia}: {len(group)} linhas")
    resto = df[~df["_closed"]].drop(columns=["_closed"])
    with open(buf, "w", encoding="utf-8") as handle:
        for _, record in resto.iterrows():
            handle.write(json.dumps(record.to_dict(), default=str) + "\n")


def main() -> int:
    for mercado in MERCADOS.values():
        try:
            salvar(mercado, coletar(mercado))
        except Exception as exc:  # noqa: BLE001 — captura é acessória
            print(f"erro ao salvar {mercado.key}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
