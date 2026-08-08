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


_TZ_LABELS = {"America/New_York": "ET", "UTC": "UTC"}


def close_label(market: str = "spy") -> str:
    """Rótulo do fechamento, ex.: '16:00 ET' (SPY) ou '16:00 UTC' (Bitcoin)."""
    m = _mercado(market)
    return f"{m.close_hour:02d}:00 {_TZ_LABELS.get(m.tz, m.tz)}"


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
    if "faixa" not in df:
        df["faixa"] = "—"                  # binário antigo (SPY) sem strikes
    df["faixa"] = df["faixa"].fillna("—")
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True)
    df = df.drop_duplicates(["ts_utc", "dia", "faixa"]).sort_values("ts")
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
    # Cada (dia, faixa) é um contrato: binário tem uma faixa "—"; strikes têm
    # uma por alvo (Bitcoin: "acima de 64k?"). O lado (Yes/No ou Up/Down) na
    # faixa de preço vira parcela; o intervalo de 10 min é por contrato.
    for (dia, faixa), group in df.groupby(["dia", "faixa"]):
        group = group.sort_values("ts")
        final = {"up": _resolved(group, "preco_up"),
                 "down": _resolved(group, "preco_down")}
        if final["up"] is None and final["down"] is None:
            continue                       # contrato ainda não resolveu
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
                "icao": market.upper(), "day": str(dia),
                "faixa": f"{faixa}·{side}", "pick": side,
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
        "up": sum(1 for s in signals if s.get("pick") == "up"),
        "down": sum(1 for s in signals if s.get("pick") == "down"),
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
    """Série de preços do dia mais recente (só para mercados binários)."""
    if _mercado(market).kind != "binary":
        return pd.DataFrame(columns=["ts", "preco_up", "preco_down", "dia"])
    df = _load_market(market)
    if df.empty:
        return pd.DataFrame(columns=["ts", "preco_up", "preco_down", "dia"])
    day = df["dia"].max()
    group = df[df["dia"] == day].sort_values("ts")
    out = group[["ts", "preco_up", "preco_down"]].copy()
    out["dia"] = str(day)
    return out


def latest_strikes(market: str = "spy") -> pd.DataFrame:
    """Strikes do último snapshot (só multi-strike), com quem está na faixa."""
    if _mercado(market).kind != "strikes":
        return pd.DataFrame()
    df = _load_market(market)
    if df.empty:
        return pd.DataFrame()
    day = df["dia"].max()
    day_group = df[df["dia"] == day]
    snap = day_group[day_group["ts"] == day_group["ts"].max()].copy()
    snap["na_faixa"] = snap.apply(
        lambda row: _side_in_band(row) is not None, axis=1)
    return snap.sort_values("faixa")[
        ["faixa", "preco_up", "preco_down", "na_faixa"]].reset_index(drop=True)


def _count_parcelas(group: pd.DataFrame) -> tuple[int, int, bool]:
    """Parcelas, acertos e se resolveu, para um contrato (dia, faixa)."""
    group = group.sort_values("ts")
    final = {"up": _resolved(group, "preco_up"),
             "down": _resolved(group, "preco_down")}
    resolved = final["up"] is not None or final["down"] is not None
    parcelas = acertos = 0
    last_ts = None
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
    return parcelas, acertos, resolved


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
    for dia, day_group in df.groupby("dia"):
        parcelas = acertos = 0
        resolved = False
        for _, group in day_group.groupby("faixa"):
            p, a, r = _count_parcelas(group)
            parcelas += p
            acertos += a
            resolved = resolved or r
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
    day_group = df[df["dia"] == day]
    parcelas = 0
    resolved = False
    for _, group in day_group.groupby("faixa"):
        p, _a, r = _count_parcelas(group)
        parcelas += p
        resolved = resolved or r
    return {"day": day, "snapshots": int(day_group["ts"].nunique()),
            "parcelas": parcelas, "resolved": resolved}
