"""Captura ao vivo do mercado 'SPY Daily Up or Down' (fase de observação).

A cada rodada (10 min no main.yml) tira um snapshot do mercado do dia (D0, no
calendário de Nova York) com o preço e o melhor ask dos dois lados — Up e Down.
Guardamos os dois lados; o estudo (spy.study) aloca no lado (Up ou Down) que
estiver na faixa de preço. Fins de semana e feriados não têm mercado: quando a
Gamma não devolve o evento, a rodada apenas não grava.

Grava no lago data_spy/ (buffer do dia UTC corrente, entra no zip do botão
Atualizar) e dados_spy/ (parquet por dia UTC fechado, commitado). Só arquiva;
não envia alerta nem ordem.

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
from zoneinfo import ZoneInfo

import requests

from tmax import config
from tmax import polymarket as pm

# O "dia" do mercado é o pregão dos EUA — o slug usa o calendário de Nova York.
MARKET_TZ = ZoneInfo("America/New_York")
BUF_DIR = config.ROOT / "data_spy"       # buffer (cache do Actions / live zip)
ARCH_DIR = config.ROOT / "dados_spy"     # parquet por dia UTC fechado (commitado)


def spy_slug(d: dt.date) -> str:
    """Slug determinístico do mercado do dia, ex.: spy-up-or-down-on-august-7-2026."""
    return f"spy-up-or-down-on-{pm._MONTHS[d.month - 1]}-{d.day}-{d.year}"


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


def fetch_spy(slug: str, timeout: int = 30) -> dict | None:
    """Preço e token de Up/Down do mercado SPY do dia. None se não existe hoje."""
    response = requests.get(
        f"{pm.GAMMA_API}/events", params={"slug": slug},
        headers={"User-Agent": config.USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    event = data[0] if isinstance(data, list) and data else data
    if not isinstance(event, dict) or not event.get("markets"):
        return None
    market = event["markets"][0]
    outcomes = [str(o).strip().lower()
                for o in pm._as_list(market.get("outcomes"))]
    prices = pm._as_list(market.get("outcomePrices"))
    token_ids = pm._as_list(market.get("clobTokenIds"))
    side: dict[str, dict] = {"up": {}, "down": {}}
    for i, outcome in enumerate(outcomes):
        if outcome not in side:
            continue
        try:
            side[outcome]["price"] = float(prices[i])
        except (IndexError, TypeError, ValueError):
            side[outcome]["price"] = None
        side[outcome]["token_id"] = (str(token_ids[i])
                                     if i < len(token_ids) else None)
    if not side["up"] or not side["down"]:
        return None
    return {"title": event.get("title") or slug,
            "end": event.get("endDate"),
            "up": side["up"], "down": side["down"]}


def _attach_asks(spy: dict, timeout: int = 30) -> None:
    """Anexa o melhor ask executável de cada lado (Up e Down) pelo livro CLOB."""
    tokens = [token for token in (spy["up"].get("token_id"),
                                  spy["down"].get("token_id")) if token]
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
        ask, size = _best_ask(books.get(str(spy[name].get("token_id"))))
        spy[name]["ask"] = ask
        spy[name]["ask_size"] = size


def coletar() -> list[dict]:
    now = dt.datetime.now(dt.timezone.utc)
    d0 = dt.datetime.now(MARKET_TZ).date()
    slug = spy_slug(d0)
    try:
        spy = fetch_spy(slug)
        if spy is None:
            print(f"sem mercado SPY hoje ({slug})")
            return []
        _attach_asks(spy)
    except Exception as exc:  # noqa: BLE001 — captura é acessória
        print(f"captura SPY falhou: {exc}", file=sys.stderr)
        return []
    record = {
        "ts_utc": now.isoformat(), "dia": d0.isoformat(), "slug": slug,
        "preco_up": spy["up"].get("price"),
        "preco_down": spy["down"].get("price"),
        "up_token_id": spy["up"].get("token_id"),
        "down_token_id": spy["down"].get("token_id"),
        "ask_up": spy["up"].get("ask"),
        "ask_up_volume": spy["up"].get("ask_size"),
        "ask_down": spy["down"].get("ask"),
        "ask_down_volume": spy["down"].get("ask_size"),
        "livro_consultado": True,
    }
    print(f"SPY {d0}: up={record['preco_up']} down={record['preco_down']}")
    return [record]


def salvar(recs: list[dict]) -> None:
    import pandas as pd

    BUF_DIR.mkdir(exist_ok=True)
    buf = BUF_DIR / "mercado.jsonl"
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
    (ARCH_DIR / "mercado").mkdir(parents=True, exist_ok=True)
    for utc, group in df.groupby("utc"):
        if utc >= hoje:
            continue                        # dia UTC corrente fica no buffer
        fp = ARCH_DIR / "mercado" / f"{utc}.parquet"
        group = group.drop(columns=["utc"])
        if fp.exists():
            group = pd.concat([pd.read_parquet(fp), group], ignore_index=True)
        group = group.drop_duplicates(["ts_utc", "dia"])
        group.to_parquet(fp, index=False)
        print(f"arquivado spy/mercado/{utc}: {len(group)} linhas")
    resto = df[df["utc"] >= hoje].drop(columns=["utc"])
    with open(buf, "w", encoding="utf-8") as handle:
        for _, record in resto.iterrows():
            handle.write(json.dumps(record.to_dict(), default=str) + "\n")


def main() -> int:
    recs = coletar()
    try:
        salvar(recs)
    except Exception as exc:  # noqa: BLE001 — captura é acessória
        print(f"erro ao salvar SPY: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
