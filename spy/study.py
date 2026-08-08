"""Estudo observacional dos mercados binários diários (SPY, Bitcoin, ...).

Para cada mercado, a cada 10 min aloca no lado (up **ou** down) cujo preço
estiver na faixa (0,95, 0,998), somando 1% do caixa livre — o modelo de
parcelas da Ceifa. Como o mercado é binário, no máximo um lado cabe na faixa.

Roda seis variantes de janela em relação ao fechamento (padrão 16:00 ET): sem
janela, H-1, H-2, H-3, H-6 e H-12. Só lê os snapshots capturados (dados_{key}/
+ data_{key}/); não coleta nada online.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import pandas as pd

from tmax import ceifa, config

from . import BAND, INTERVAL_MINUTES, MERCADOS, Mercado

STAKE_FRAC = config.CEIFA_STAKE_FRAC       # 1% do caixa livre por parcela

# (rótulo, horas antes do fechamento). None = sem janela.
WINDOWS: list[tuple[str, int | None]] = [
    ("Sem janela", None), ("H-1", 1), ("H-2", 2),
    ("H-3", 3), ("H-6", 6), ("H-12", 12),
]


def _mercado(market: str | Mercado) -> Mercado:
    return market if isinstance(market, Mercado) else MERCADOS[market]


def _load_market(market: str = "spy") -> pd.DataFrame:
    """Une o parquet commitado (dias fechados) e os buffers do dia corrente."""
    m = _mercado(market)
    arch_dir = config.ROOT / f"dados_{m.key}" / "mercado"
    buf_files = (config.ROOT / f"data_{m.key}" / "mercado.jsonl",
                 config.ROOT / f"data_{m.key}_live" / "mercado.jsonl")
    frames: list[pd.DataFrame] = []
    if arch_dir.exists():
        for fp in sorted(arch_dir.glob("*.parquet")):
            frames.append(pd.read_parquet(fp))
    for buf in buf_files:
        if buf.exists():
            rows = [json.loads(line) for line in
                    buf.read_text(encoding="utf-8").splitlines() if line.strip()]
            if rows:
                frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame()
    df = pd.concat(frames, ignore_index=True)
    if "preco_down" not in df or "preco_up" not in df:
        return pd.DataFrame()
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
    df = df.drop_duplicates(["ts_utc", "dia"]).sort_values("ts")
    return df


def _close_utc(market: str, dia: str) -> pd.Timestamp:
    m = _mercado(market)
    d = dt.date.fromisoformat(dia)
    close = dt.datetime(d.year, d.month, d.day, m.close_hour, 0,
                        tzinfo=ZoneInfo(m.tz))
    return pd.Timestamp(close).tz_convert("UTC")


def _resolved(group: pd.DataFrame, column: str) -> float | None:
    """Preço final de um lado; resolvido quando encosta em 0 ou 1 (±1¢)."""
    valid = group.dropna(subset=[column])
    if valid.empty:
        return None
    value = float(valid.sort_values("ts").iloc[-1][column])
    return value if value >= 0.99 or value <= 0.01 else None


def _side_in_band(row) -> str | None:
    for name in ("up", "down"):
        price = row.get(f"preco_{name}")
        if price is not None and not pd.isna(price) \
                and BAND[0] < float(price) < BAND[1]:
            return name
    return None


def simulate(window_hours: int | None = None, market: str = "spy") -> dict:
    """Backtest de uma variante de janela; devolve stats no formato da Ceifa."""
    df = _load_market(market)
    if df.empty:
        return ceifa._stats_relative_available_stake([], 0, STAKE_FRAC)

    signals: list[dict] = []
    for dia, group in df.groupby("dia"):
        group = group.sort_values("ts")
        final = {"up": _resolved(group, "preco_up"),
                 "down": _resolved(group, "preco_down")}
        if final["up"] is None and final["down"] is None:
            continue                       # dia ainda não resolveu
        close = _close_utc(market, str(dia))
        cutoff = (None if window_hours is None
                  else close - pd.Timedelta(hours=window_hours))
        last_ts = None
        for _, row in group.iterrows():
            side = _side_in_band(row)
            if side is None:
                continue
            if cutoff is not None and (row["ts"] < cutoff or row["ts"] > close):
                continue
            if last_ts is not None and \
                    (row["ts"] - last_ts) < pd.Timedelta(minutes=INTERVAL_MINUTES):
                continue
            if final[side] is None:
                continue
            signals.append({
                "icao": market.upper(), "day": str(dia), "faixa": side.upper(),
                "ts": row["ts"], "price": float(row[f"preco_{side}"]),
                "won": final[side] > 0.5, "stopped": False,
                "loss_frac": None, "spread": None,
            })
            last_ts = row["ts"]

    stats = ceifa._stats_relative_available_stake(
        signals, df["dia"].nunique(), STAKE_FRAC)
    stats["archive_kind"] = "mercado"
    stats["side"] = market.upper()
    stats["repeat_minutes"] = INTERVAL_MINUTES
    stats["window_hours"] = window_hours
    stats["by_pick"] = {
        "up": sum(1 for s in signals if s["faixa"] == "UP"),
        "down": sum(1 for s in signals if s["faixa"] == "DOWN"),
    }
    return stats


def run_variants(market: str = "spy") -> list[tuple[str, dict]]:
    """Roda as seis janelas e devolve [(rótulo, stats), ...]."""
    return [(label, simulate(hours, market)) for label, hours in WINDOWS]


def latest_day(market: str = "spy") -> str | None:
    df = _load_market(market)
    if df.empty:
        return None
    return str(df["dia"].max())


def latest_prices(market: str = "spy") -> pd.DataFrame:
    """Série de preços dos dois lados do dia mais recente (para o gráfico)."""
    df = _load_market(market)
    if df.empty:
        return pd.DataFrame(columns=["ts", "preco_up", "preco_down", "dia"])
    day = df["dia"].max()
    group = df[df["dia"] == day].sort_values("ts")
    out = group[["ts", "preco_up", "preco_down"]].copy()
    out["dia"] = str(day)
    return out


def side_labels(market: str = "spy") -> tuple[str, str]:
    """Rótulos dos dois lados (up/down) do dia mais recente, se gravados."""
    df = _load_market(market)
    up, down = "Up", "Down"
    if not df.empty and "up_label" in df and "down_label" in df:
        last = df.sort_values("ts").iloc[-1]
        up = str(last.get("up_label") or up)
        down = str(last.get("down_label") or down)
    return up, down


def daily_summary(market: str = "spy") -> pd.DataFrame:
    """Uma linha por dia capturado: parcelas, se resolveu e o resultado."""
    df = _load_market(market)
    cols = ["dia", "parcelas", "acertos", "resolvido", "resultado"]
    if df.empty:
        return pd.DataFrame(columns=cols)
    rows = []
    for dia, group in df.groupby("dia"):
        group = group.sort_values("ts")
        final = {"up": _resolved(group, "preco_up"),
                 "down": _resolved(group, "preco_down")}
        resolved = final["up"] is not None or final["down"] is not None
        parcelas, acertos, last_ts = 0, 0, None
        for _, row in group.iterrows():
            side = _side_in_band(row)
            if side is None:
                continue
            if last_ts is not None and \
                    (row["ts"] - last_ts) < pd.Timedelta(minutes=INTERVAL_MINUTES):
                continue
            parcelas += 1
            last_ts = row["ts"]
            if resolved and final[side] is not None and final[side] > 0.5:
                acertos += 1
        if not resolved:
            resultado = "Em aberto"
        elif parcelas == 0:
            resultado = "Sem entrada"
        elif acertos == parcelas:
            resultado = "Acerto"
        elif acertos == 0:
            resultado = "Erro"
        else:
            resultado = f"{acertos}/{parcelas}"
        rows.append({"dia": pd.to_datetime(str(dia)), "parcelas": parcelas,
                     "acertos": acertos, "resolvido": resolved,
                     "resultado": resultado})
    return pd.DataFrame(rows).sort_values("dia").reset_index(drop=True)


def today_progress(market: str = "spy") -> dict:
    """Andamento do dia mais recente, mesmo antes de resolver."""
    df = _load_market(market)
    if df.empty:
        return {"day": None, "snapshots": 0, "parcelas": 0, "resolved": False}
    day = str(df["dia"].max())
    group = df[df["dia"] == day].sort_values("ts")
    resolved = (_resolved(group, "preco_up") is not None
                or _resolved(group, "preco_down") is not None)
    parcelas, last_ts = 0, None
    for _, row in group.iterrows():
        if _side_in_band(row) is None:
            continue
        if last_ts is not None and \
                (row["ts"] - last_ts) < pd.Timedelta(minutes=INTERVAL_MINUTES):
            continue
        parcelas += 1
        last_ts = row["ts"]
    return {"day": day, "snapshots": len(group),
            "parcelas": parcelas, "resolved": resolved}
