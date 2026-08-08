"""Captura ao vivo dos mercados binários diários (fase de observação).

A cada rodada (10 min no main.yml), para cada mercado do registro ``MERCADOS``,
tira um snapshot do dia (D0, no fuso do mercado) com preço e melhor ask dos dois
lados. Guardamos os dois lados posicionalmente (lado A → "up", lado B → "down"),
sem depender do nome do desfecho, então serve para SPY (Up/Down), Bitcoin
(Above/Below) etc. Fim de semana/feriado sem mercado: a rodada apenas não grava.

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

from . import MERCADOS, Mercado, market_date


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


def fetch_binary(slug: str, timeout: int = 30) -> dict | None:
    """Preço/token dos dois lados de um mercado binário. None se não existe hoje.

    Pega os dois desfechos por POSIÇÃO (0 → up, 1 → down), então funciona para
    qualquer par (Up/Down, Above/Below, Yes/No)."""
    response = requests.get(
        f"{pm.GAMMA_API}/events", params={"slug": slug},
        headers={"User-Agent": config.USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    event = data[0] if isinstance(data, list) and data else data
    if not isinstance(event, dict) or not event.get("markets"):
        return None
    market = event["markets"][0]
    outcomes = pm._as_list(market.get("outcomes"))
    prices = pm._as_list(market.get("outcomePrices"))
    token_ids = pm._as_list(market.get("clobTokenIds"))
    if len(outcomes) < 2:
        return None

    def side(i: int) -> dict:
        try:
            price = float(prices[i])
        except (IndexError, TypeError, ValueError):
            price = None
        return {"price": price,
                "token_id": str(token_ids[i]) if i < len(token_ids) else None,
                "label": str(outcomes[i])}

    return {"title": event.get("title") or slug,
            "end": event.get("endDate"),
            "up": side(0), "down": side(1)}


def _attach_asks(market: dict, timeout: int = 30) -> None:
    """Anexa o melhor ask executável de cada lado pelo livro CLOB."""
    tokens = [token for token in (market["up"].get("token_id"),
                                  market["down"].get("token_id")) if token]
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
    for name in ("up", "down"):
        ask, size = _best_ask(books.get(str(market[name].get("token_id"))))
        market[name]["ask"] = ask
        market[name]["ask_size"] = size


def coletar(mercado: Mercado) -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    d0 = market_date(mercado, now)
    slug = market_slug(mercado.slug_prefix, d0)
    try:
        book = fetch_binary(slug)
        if book is None:
            print(f"sem mercado {mercado.key} hoje ({slug})")
            return []
        _attach_asks(book)
    except Exception as exc:  # noqa: BLE001 — captura é acessória
        print(f"captura {mercado.key} falhou: {exc}", file=sys.stderr)
        return []
    record = {
        "ts_utc": now.isoformat(), "dia": d0.isoformat(), "slug": slug,
        "preco_up": book["up"].get("price"),
        "preco_down": book["down"].get("price"),
        "up_label": book["up"].get("label"),
        "down_label": book["down"].get("label"),
        "up_token_id": book["up"].get("token_id"),
        "down_token_id": book["down"].get("token_id"),
        "ask_up": book["up"].get("ask"),
        "ask_up_volume": book["up"].get("ask_size"),
        "ask_down": book["down"].get("ask"),
        "ask_down_volume": book["down"].get("ask_size"),
        "livro_consultado": True,
    }
    print(f"{mercado.key} {d0}: {book['up'].get('label')}="
          f"{record['preco_up']} {book['down'].get('label')}="
          f"{record['preco_down']}")
    return [record]


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
    hoje = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    with buf.open(encoding="utf-8") as handle:
        linhas = [json.loads(line) for line in handle if line.strip()]
    if not linhas:
        return
    df = pd.DataFrame(linhas)
    df["utc"] = df["ts_utc"].str[:10]
    (arch_dir / "mercado").mkdir(parents=True, exist_ok=True)
    for utc, group in df.groupby("utc"):
        if utc >= hoje:
            continue                        # dia UTC corrente fica no buffer
        fp = arch_dir / "mercado" / f"{utc}.parquet"
        group = group.drop(columns=["utc"])
        if fp.exists():
            group = pd.concat([pd.read_parquet(fp), group], ignore_index=True)
        group = group.drop_duplicates(["ts_utc", "dia"])
        group.to_parquet(fp, index=False)
        print(f"arquivado {mercado.key}/mercado/{utc}: {len(group)} linhas")
    resto = df[df["utc"] >= hoje].drop(columns=["utc"])
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
