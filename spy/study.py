"""Estudo observacional dos mercados binários diários (SPY, Bitcoin, ...).

Para cada mercado, a cada 5 min aloca no lado (up **ou** down) cujo preço
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

from . import BAND, INTERVAL_MINUTES, MERCADOS, PAIR_ASK_CEILING, Mercado

STAKE_FRAC = config.CEIFA_STAKE_FRAC       # 1% do caixa livre por parcela

# (rótulo, horas antes do fechamento). None = sem janela.
WINDOWS: list[tuple[str, int | None]] = [
    ("Sem janela", None), ("H-1", 1), ("H-2", 2),
    ("H-3", 3), ("H-6", 6), ("H-12", 12),
]


def _mercado(market: str | Mercado) -> Mercado:
    return market if isinstance(market, Mercado) else MERCADOS[market]


def price_band(market: str | Mercado = "spy") -> tuple[float, float]:
    """Faixa de compra do mercado, com piso global e teto individual."""
    return BAND[0], _mercado(market).band_high


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
    # ISO8601 com precisão variável: o ao vivo tem microssegundos, o
    # reconstruído (backfill) não. format="ISO8601" aceita os dois.
    df["ts"] = pd.to_datetime(df["ts_utc"], utc=True, format="ISO8601")
    df = df.drop_duplicates(["ts_utc", "dia", "faixa"]).sort_values("ts")
    return df


def _close_utc(market: str, dia: str) -> pd.Timestamp:
    m = _mercado(market)
    d = dt.date.fromisoformat(dia)
    close = dt.datetime(d.year, d.month, d.day, m.close_hour, 0,
                        tzinfo=ZoneInfo(m.tz))
    return pd.Timestamp(close).tz_convert("UTC")


def _resolved(group: pd.DataFrame, column: str, close: pd.Timestamp,
              now_ts: pd.Timestamp) -> float | None:
    """Preço final de um lado, quando o dia já ACABOU.

    O dia acabou quando o relógio — o último snapshot do lago (``now_ts``) —
    passou o fechamento. Dois regimes de captura:

    - **Não rolling (SPY):** seguimos capturando o mesmo dia depois do fechamento,
      então há snapshot ≥ close e exigimos o preço pinado (0/1). Assim uma captura
      incompleta que parou no meio (ex.: 0,60) não é pontuada.
    - **Rolling (Bitcoin):** o dia vira no próprio fechamento e NUNCA há snapshot
      depois dele. Quando o relógio já passou o close, o último preço antes do
      fechamento é o desfecho (melhor estimativa disponível).

    Sem isso, um strike fundo no dinheiro pareceria resolvido cedo demais, e um
    mercado rolling nunca resolveria."""
    if now_ts < close:
        return None                        # dia ainda aberto — não pontua
    valid = group.dropna(subset=[column])
    if valid.empty:
        return None
    value = float(valid.sort_values("ts").iloc[-1][column])
    if group["ts"].max() >= close:         # há dado no fechamento → exige pino
        return value if value >= 0.99 or value <= 0.01 else None
    return value                           # rolling: último preço pré-fechamento


def _side_in_price_band(row, market: str | Mercado = "spy") -> str | None:
    """Lado elegível apenas pela faixa de preço, antes do veto do par."""
    low, high = price_band(market)
    for name in ("up", "down"):
        price = row.get(f"preco_{name}")
        if price is not None and not pd.isna(price) \
                and low < float(price) < high:
            return name
    return None


def _pair_asks_allowed(row) -> bool:
    """Se houver um par completo, exige soma estritamente abaixo de 105¢."""
    # O par de asks é o custo executável simultâneo de ambos os lados. Um livro
    # largo ou inconsistente (Yes + No >= 105¢) veta a parcela inteira. Dados
    # antigos sem os dois asks caem para os preços indicativos; se nem o par de
    # preços estiver completo, não inventamos um veto sem informação.
    for up_col, down_col in (("ask_up", "ask_down"),
                             ("preco_up", "preco_down")):
        up = row.get(up_col)
        down = row.get(down_col)
        if up is None or down is None or pd.isna(up) or pd.isna(down):
            continue
        if float(up) + float(down) >= PAIR_ASK_CEILING:
            return False
        return True
    return True


def _side_in_band(row, market: str | Mercado = "spy") -> str | None:
    if not _pair_asks_allowed(row):
        return None
    return _side_in_price_band(row, market)


def simulate(window_hours: int | None = None, market: str = "spy") -> dict:
    """Backtest de uma variante de janela; devolve stats no formato da Ceifa."""
    df = _load_market(market)
    if df.empty:
        stats = ceifa._stats_relative_available_stake([], 0, STAKE_FRAC)
        stats["pair_filter_blocked"] = 0
        return stats

    signals: list[dict] = []
    baseline_n = 0
    now_ts = df["ts"].max()                # relógio: último snapshot do lago
    # Cada (dia, faixa) é um contrato: binário tem uma faixa "—"; strikes têm
    # uma por alvo (Bitcoin: "acima de 64k?"). O lado (Yes/No ou Up/Down) na
    # faixa de preço vira parcela; o intervalo de 5 min é por contrato.
    for (dia, faixa), group in df.groupby(["dia", "faixa"]):
        group = group.sort_values("ts")
        close = _close_utc(market, str(dia))
        final = {"up": _resolved(group, "preco_up", close, now_ts),
                 "down": _resolved(group, "preco_down", close, now_ts)}
        if final["up"] is None and final["down"] is None:
            continue                       # contrato ainda não resolveu
        cutoff = (None if window_hours is None
                  else close - pd.Timedelta(hours=window_hours))
        last_ts = None
        baseline_last_ts = None
        for _, row in group.iterrows():
            baseline_side = _side_in_price_band(row, market)
            side = _side_in_band(row, market)
            if baseline_side is None and side is None:
                continue
            if cutoff is not None and (row["ts"] < cutoff or row["ts"] > close):
                continue
            if baseline_side is not None and final[baseline_side] is not None \
                    and (baseline_last_ts is None or
                         (row["ts"] - baseline_last_ts) >=
                         pd.Timedelta(minutes=INTERVAL_MINUTES)):
                baseline_n += 1
                baseline_last_ts = row["ts"]
            if side is None:
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
    # Diferença líquida, já depois da mesma janela e cadência de cinco minutos.
    # Não é contagem de linhas brutas rejeitadas.
    stats["pair_filter_blocked"] = max(baseline_n - len(signals), 0)
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


def price_days(market: str = "spy") -> list[str]:
    """Dias com trajetória de preços disponível, em ordem cronológica."""
    if _mercado(market).kind != "binary":
        return []
    df = _load_market(market)
    if df.empty:
        return []
    return sorted(df["dia"].dropna().astype(str).unique().tolist())


def prices_for_day(market: str = "spy", day: str | None = None) -> pd.DataFrame:
    """Série de preços de um dia; sem data explícita, usa o mais recente."""
    columns = ["ts", "preco_up", "preco_down", "dia"]
    if _mercado(market).kind != "binary":
        return pd.DataFrame(columns=columns)
    df = _load_market(market)
    if df.empty:
        return pd.DataFrame(columns=columns)
    available = sorted(df["dia"].dropna().astype(str).unique().tolist())
    selected = day or available[-1]
    if selected not in available:
        return pd.DataFrame(columns=columns)
    group = df[df["dia"].astype(str) == selected].sort_values("ts")
    out = group[["ts", "preco_up", "preco_down"]].copy()
    out["dia"] = selected
    return out


def latest_prices(market: str = "spy") -> pd.DataFrame:
    """Compat.: série de preços do dia mais recente."""
    return prices_for_day(market)


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
        lambda row: _side_in_band(row, market) is not None, axis=1)
    return snap.sort_values("faixa")[
        ["faixa", "preco_up", "preco_down", "na_faixa"]].reset_index(drop=True)


def _count_parcelas(group: pd.DataFrame, close: pd.Timestamp,
                    now_ts: pd.Timestamp,
                    market: str = "spy") -> tuple[int, int, bool]:
    """Parcelas, acertos e se resolveu, para um contrato (dia, faixa)."""
    group = group.sort_values("ts")
    final = {"up": _resolved(group, "preco_up", close, now_ts),
             "down": _resolved(group, "preco_down", close, now_ts)}
    resolved = final["up"] is not None or final["down"] is not None
    parcelas = acertos = 0
    last_ts = None
    for _, row in group.iterrows():
        side = _side_in_band(row, market)
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
    now_ts = df["ts"].max()
    for dia, day_group in df.groupby("dia"):
        close = _close_utc(market, str(dia))
        parcelas = acertos = 0
        resolved = False
        for _, group in day_group.groupby("faixa"):
            p, a, r = _count_parcelas(group, close, now_ts, market)
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
    close = _close_utc(market, day)
    now_ts = df["ts"].max()
    day_group = df[df["dia"] == day]
    parcelas = 0
    resolved = False
    for _, group in day_group.groupby("faixa"):
        p, _a, r = _count_parcelas(group, close, now_ts, market)
        parcelas += p
        resolved = resolved or r
    return {"day": day, "snapshots": int(day_group["ts"].nunique()),
            "parcelas": parcelas, "resolved": resolved}
