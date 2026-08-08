"""Estudo observacional da estratégia SPY Up or Down.

A cada 10 min, aloca no lado (Up **ou** Down) cujo preço estiver na faixa
(0,95, 0,996), adicionando 1% do caixa livre — o mesmo modelo de parcelas da
Ceifa. Como o mercado é binário (up + down ≈ 1), no máximo um lado cabe na
faixa por rodada.

Roda seis variantes de janela em relação ao fechamento do mercado (16:00 ET):
sem janela, H-1, H-2, H-3, H-6 e H-12 (cada H-n = só as parcelas dentro das
últimas n horas antes do fechamento).

Só lê os snapshots capturados por spy.capture (dados_spy/ + data_spy/); não
coleta nada online.
"""
from __future__ import annotations

import datetime as dt
import json
from zoneinfo import ZoneInfo

import pandas as pd

from tmax import ceifa, config

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_CLOSE_HOUR = 16                     # SPY fecha 16:00 ET
ARCH_DIR = config.ROOT / "dados_spy" / "mercado"
# Buffers do dia corrente: o local (Actions/execução) e o extraído do zip do
# botão Atualizar (data_spy_live, escrito por monitor._sync_live_snapshot).
BUF_FILES = (config.ROOT / "data_spy" / "mercado.jsonl",
             config.ROOT / "data_spy_live" / "mercado.jsonl")

BAND = (0.95, 0.996)                       # >95¢ e <99,6¢, estritamente
INTERVAL_MINUTES = 10
STAKE_FRAC = config.CEIFA_STAKE_FRAC       # 1% do caixa livre por parcela

# (rótulo, horas antes do fechamento). None = sem janela.
WINDOWS: list[tuple[str, int | None]] = [
    ("Sem janela", None), ("H-1", 1), ("H-2", 2),
    ("H-3", 3), ("H-6", 6), ("H-12", 12),
]


def _load_market() -> pd.DataFrame:
    """Une o parquet commitado (dias fechados) e os buffers do dia corrente."""
    frames: list[pd.DataFrame] = []
    if ARCH_DIR.exists():
        for fp in sorted(ARCH_DIR.glob("*.parquet")):
            frames.append(pd.read_parquet(fp))
    for buf in BUF_FILES:
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


def _close_utc(dia: str) -> pd.Timestamp:
    d = dt.date.fromisoformat(dia)
    close = dt.datetime(d.year, d.month, d.day, MARKET_CLOSE_HOUR, 0,
                        tzinfo=MARKET_TZ)
    return pd.Timestamp(close).tz_convert("UTC")


def _resolved(group: pd.DataFrame, column: str) -> float | None:
    """Preço final de um lado; resolvido quando encosta em 0 ou 1 (±1¢)."""
    valid = group.dropna(subset=[column])
    if valid.empty:
        return None
    value = float(valid.sort_values("ts").iloc[-1][column])
    return value if value >= 0.99 or value <= 0.01 else None


def simulate(window_hours: int | None = None) -> dict:
    """Backtest de uma variante de janela; devolve stats no formato da Ceifa."""
    df = _load_market()
    if df.empty:
        return ceifa._stats_relative_available_stake([], 0, STAKE_FRAC)

    signals: list[dict] = []
    for dia, group in df.groupby("dia"):
        group = group.sort_values("ts")
        final = {"up": _resolved(group, "preco_up"),
                 "down": _resolved(group, "preco_down")}
        if final["up"] is None and final["down"] is None:
            continue                       # dia ainda não resolveu
        close = _close_utc(str(dia))
        cutoff = (None if window_hours is None
                  else close - pd.Timedelta(hours=window_hours))
        last_ts = None
        for _, row in group.iterrows():
            side = None
            for name in ("up", "down"):
                price = row.get(f"preco_{name}")
                if price is not None and not pd.isna(price) \
                        and BAND[0] < float(price) < BAND[1]:
                    side = name
                    break
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
                "icao": "SPY", "day": str(dia), "faixa": side.upper(),
                "ts": row["ts"], "price": float(row[f"preco_{side}"]),
                "won": final[side] > 0.5, "stopped": False,
                "loss_frac": None, "spread": None,
            })
            last_ts = row["ts"]

    stats = ceifa._stats_relative_available_stake(
        signals, df["dia"].nunique(), STAKE_FRAC)
    stats["archive_kind"] = "spy"
    stats["side"] = "SPY"
    stats["repeat_minutes"] = INTERVAL_MINUTES
    stats["window_hours"] = window_hours
    # Quantas parcelas caíram em cada lado — só para leitura.
    stats["by_pick"] = {
        "up": sum(1 for s in signals if s["faixa"] == "UP"),
        "down": sum(1 for s in signals if s["faixa"] == "DOWN"),
    }
    return stats


def run_variants() -> list[tuple[str, dict]]:
    """Roda as seis janelas e devolve [(rótulo, stats), ...]."""
    return [(label, simulate(hours)) for label, hours in WINDOWS]


def latest_day() -> str | None:
    df = _load_market()
    if df.empty:
        return None
    return str(df["dia"].max())


def daily_summary() -> pd.DataFrame:
    """Uma linha por dia capturado: parcelas, se resolveu e o resultado.

    Inclui o dia em andamento (ainda não resolvido) — assim o gráfico de
    parcelas por dia aparece mesmo antes do fechamento do mercado.
    """
    df = _load_market()
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
            side = None
            for name in ("up", "down"):
                price = row.get(f"preco_{name}")
                if price is not None and not pd.isna(price) \
                        and BAND[0] < float(price) < BAND[1]:
                    side = name
                    break
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


def today_progress() -> dict:
    """Andamento do dia mais recente capturado, mesmo antes de resolver.

    Deixa o painel mostrar 'capturando: N snapshots, M parcelas em aberto'
    enquanto o mercado do dia não fecha (16:00 ET).
    """
    df = _load_market()
    if df.empty:
        return {"day": None, "snapshots": 0, "parcelas": 0, "resolved": False}
    day = str(df["dia"].max())
    group = df[df["dia"] == day].sort_values("ts")
    resolved = (_resolved(group, "preco_up") is not None
                or _resolved(group, "preco_down") is not None)
    parcelas, last_ts = 0, None
    for _, row in group.iterrows():
        in_band = False
        for name in ("up", "down"):
            price = row.get(f"preco_{name}")
            if price is not None and not pd.isna(price) \
                    and BAND[0] < float(price) < BAND[1]:
                in_band = True
                break
        if not in_band:
            continue
        if last_ts is not None and \
                (row["ts"] - last_ts) < pd.Timedelta(minutes=INTERVAL_MINUTES):
            continue
        parcelas += 1
        last_ts = row["ts"]
    return {"day": day, "snapshots": len(group),
            "parcelas": parcelas, "resolved": resolved}
