"""Reconstrói trajetórias perdidas pela API histórica oficial da Polymarket.

Uso: ``python -m spy.backfill ethereum 2026-08-12``.

O endpoint histórico fornece preços negociados em intervalos de cinco minutos,
mas não o livro de ofertas daquele instante. Por isso os asks ficam vazios e o
registro é marcado como ``historico_reconstruido``. O estudo pode recuperar a
trajetória e a faixa de preços, sem fingir que consultou um livro retroativo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path

import pandas as pd
import requests

from tmax import config
from tmax import polymarket as pm

from . import INTERVAL_MINUTES, MERCADOS, Mercado, close_utc
from .capture import market_slug


def _as_list(value) -> list:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    return value if isinstance(value, list) else []


def _event(slug: str, timeout: int = 30) -> dict:
    response = requests.get(
        f"{pm.GAMMA_API}/events", params={"slug": slug},
        headers={"User-Agent": config.USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    event = data[0] if isinstance(data, list) and data else None
    if not isinstance(event, dict) or not event.get("markets"):
        raise ValueError(f"evento não encontrado: {slug}")
    return event


def _history(token_id: str, start_ts: int, end_ts: int,
             timeout: int = 30) -> list[dict]:
    response = requests.get(
        f"{pm.CLOB_API}/prices-history",
        params={"market": token_id, "startTs": start_ts, "endTs": end_ts,
                "fidelity": INTERVAL_MINUTES},
        headers={"User-Agent": config.USER_AGENT}, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return data.get("history", []) if isinstance(data, dict) else []


def _align(up: list[dict], down: list[dict]) -> pd.DataFrame:
    """Alinha os buckets dos dois tokens, tolerando poucos segundos de desvio."""
    left = pd.DataFrame(up).rename(columns={"t": "t_up", "p": "preco_up"})
    right = pd.DataFrame(down).rename(
        columns={"t": "t_down", "p": "preco_down"})
    if left.empty or right.empty:
        return pd.DataFrame()
    left = left[["t_up", "preco_up"]].sort_values("t_up")
    right = right[["t_down", "preco_down"]].sort_values("t_down")
    aligned = pd.merge_asof(
        left, right, left_on="t_up", right_on="t_down", direction="nearest",
        tolerance=120)
    return aligned.dropna(subset=["preco_down"])


def build_records(mercado: Mercado, day: dt.date) -> list[dict]:
    slug = market_slug(mercado.slug_prefix, day)
    event = _event(slug)
    close = close_utc(mercado, day)
    start = close - dt.timedelta(days=1)
    markets = event["markets"] if mercado.kind == "strikes" \
        else event["markets"][:1]
    records: list[dict] = []
    for contract in markets:
        outcomes = _as_list(contract.get("outcomes"))
        tokens = _as_list(contract.get("clobTokenIds"))
        final_prices = _as_list(contract.get("outcomePrices"))
        if len(outcomes) < 2 or len(tokens) < 2:
            continue
        aligned = _align(
            _history(str(tokens[0]), int(start.timestamp()), int(close.timestamp())),
            _history(str(tokens[1]), int(start.timestamp()), int(close.timestamp())))
        faixa = "—" if mercado.kind == "binary" else str(
            contract.get("groupItemTitle") or contract.get("question") or "—")
        for row in aligned.itertuples(index=False):
            stamp = max(int(row.t_up), int(row.t_down))
            records.append({
                "ts_utc": dt.datetime.fromtimestamp(
                    stamp, tz=dt.timezone.utc).isoformat(),
                "dia": day.isoformat(), "slug": slug, "faixa": faixa,
                "preco_up": float(row.preco_up),
                "preco_down": float(row.preco_down),
                "up_label": str(outcomes[0]), "down_label": str(outcomes[1]),
                "up_token_id": str(tokens[0]), "down_token_id": str(tokens[1]),
                "ask_up": None, "ask_up_volume": None,
                "ask_down": None, "ask_down_volume": None,
                "livro_consultado": False,
                "historico_reconstruido": True,
                "fonte_historica": "clob.prices-history",
            })
        # A série de cinco minutos termina antes do fechamento. Quando a Gamma
        # já publica o resultado pinado, guardamos esse desfecho oficial no
        # instante do close para o estudo não deixar o dia eternamente aberto.
        try:
            final_up, final_down = map(float, final_prices[:2])
        except (TypeError, ValueError):
            final_up = final_down = .5
        if (final_up >= .99 or final_up <= .01) and \
                (final_down >= .99 or final_down <= .01):
            records.append({
                "ts_utc": close.isoformat(), "dia": day.isoformat(),
                "slug": slug, "faixa": faixa,
                "preco_up": final_up, "preco_down": final_down,
                "up_label": str(outcomes[0]), "down_label": str(outcomes[1]),
                "up_token_id": str(tokens[0]), "down_token_id": str(tokens[1]),
                "ask_up": None, "ask_up_volume": None,
                "ask_down": None, "ask_down_volume": None,
                "livro_consultado": False,
                "historico_reconstruido": True,
                "resolucao_oficial": True,
                "fonte_historica": "gamma.outcomePrices",
            })
    return records


def save_records(mercado: Mercado, day: dt.date, records: list[dict],
                 root: Path | None = None) -> Path:
    if not records:
        raise ValueError(f"sem preços históricos para {mercado.key} em {day}")
    root = root or config.ROOT
    path = root / f"dados_{mercado.key}" / "mercado" / f"{day}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    new = pd.DataFrame(records)
    if path.exists():
        new = pd.concat([pd.read_parquet(path), new], ignore_index=True)
    buffer = root / f"data_{mercado.key}" / "mercado.jsonl"
    if buffer.exists():
        live = [json.loads(line) for line in buffer.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        live = [row for row in live if str(row.get("dia")) == day.isoformat()]
        if live:
            new = pd.concat([new, pd.DataFrame(live)], ignore_index=True)
    # Um snapshot real ganha do histórico quando ambos caem no mesmo bucket.
    new["_bucket"] = pd.to_datetime(
        new["ts_utc"], utc=True, format="ISO8601").dt.floor(
            f"{INTERVAL_MINUTES}min")
    reconstructed = new.get(
        "historico_reconstruido", pd.Series(False, index=new.index))
    new["_real"] = ~reconstructed.fillna(False).astype(bool)
    new = (new.sort_values(["_bucket", "faixa", "_real"])
           .drop_duplicates(["dia", "faixa", "_bucket"], keep="last")
           .drop(columns=["_bucket", "_real"]))
    new.to_parquet(path, index=False)
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("market", choices=sorted(MERCADOS))
    parser.add_argument("day", type=dt.date.fromisoformat)
    args = parser.parse_args()
    mercado = MERCADOS[args.market]
    records = build_records(mercado, args.day)
    path = save_records(mercado, args.day, records)
    print(f"{mercado.key} {args.day}: {len(records)} pontos -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
