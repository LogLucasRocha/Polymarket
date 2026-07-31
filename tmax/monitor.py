"""Preparação dos dados exibidos no monitor local da estratégia Ceifa."""
from __future__ import annotations

import datetime as dt
import gzip
import json
import subprocess
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from . import ceifa, config

MAXIMUM_ARCHIVE = config.ROOT / "dados"
MINIMUM_ARCHIVE = config.ROOT / "dados_low"


def sync_dashboard_data() -> dict:
    """Sincroniza somente os arquivos de dados com o ``main`` remoto.

    O dashboard local contém código e atalhos próprios que podem estar
    modificados. Atualizar apenas ``dados/`` e ``dados_low/`` evita que essas
    alterações bloqueiem a chegada dos snapshots publicados pelo workflow.
    """
    def run(*args: str):
        return subprocess.run(
            ["git", *args], cwd=config.ROOT, capture_output=True, text=True,
            timeout=60, check=False)

    try:
        fetched = run("fetch", "origin", "--quiet")
        if fetched.returncode:
            return {"ok": False, "updated": False,
                    "message": "Não consegui baixar os dados do GitHub."}
        archives = ("dados", "dados_low")
        compared = run("diff", "--quiet", "origin/main", "--", *archives)
        if compared.returncode not in (0, 1):
            return {
                "ok": False, "updated": False,
                "message": "Não consegui comparar os arquivos de dados.",
            }
        if compared.returncode == 0:
            return {
                "ok": True, "updated": False,
                "message": "Você já estava com os dados mais recentes.",
            }
        restored = run(
            "restore", "--source=origin/main", "--worktree", "--", *archives)
        if restored.returncode:
            return {
                "ok": False, "updated": False,
                "message": "Não consegui atualizar os arquivos de dados.",
            }
        return {
            "ok": True, "updated": True,
            "message": "Dados novos baixados e indicadores recalculados.",
        }
    except (OSError, subprocess.SubprocessError):
        return {"ok": False, "updated": False,
                "message": "A atualização automática não pôde ser concluída."}


def run_strategy() -> dict:
    """Executa exatamente o backtest usado no relatório diário."""
    stats = ceifa.simulate_repeated(
        icaos=set(config.STATIONS),
        interval_minutes=config.CEIFA_REPEAT_MINUTES,
        stake_frac=config.CEIFA_STAKE_FRAC,
    )
    stats["archive_kind"] = "maximum"
    stats["side"] = "NAO"
    return stats


def run_minimum_strategy() -> dict:
    """Executa o monitor oficial da Ceifa de temperaturas mínimas.

    A estratégia permanece em observação. Testa parcelas de 1% do caixa livre a
    cada cinco minutos na H-1, sem os filtros meteorológicos das máximas.
    """
    stats = ceifa.simulate_repeated(
        icaos=set(config.STATIONS), archive=MINIMUM_ARCHIVE,
        warm_target_filter=False, uncertainty_filter=False,
        interval_minutes=config.CEIFA_REPEAT_MINUTES,
        stake_frac=config.CEIFA_STAKE_FRAC)
    stats["archive_kind"] = "minimum"
    stats["side"] = "NAO"
    return stats


def run_yes_strategy(archive_kind: str) -> dict:
    """Executa o teste separado do SIM usando somente ofertas executáveis."""
    archive = (MINIMUM_ARCHIVE if archive_kind == "minimum"
               else MAXIMUM_ARCHIVE)
    stats = ceifa.simulate_yes_repeated(
        icaos=set(config.STATIONS), archive=archive,
        interval_minutes=config.CEIFA_REPEAT_MINUTES,
        stake_frac=config.CEIFA_STAKE_FRAC)
    stats["archive_kind"] = archive_kind
    stats["side"] = "SIM"
    return stats


def slice_strategy(stats: dict, lookback_days: int | None) -> dict:
    """Recalcula a banca para o período escolhido, sem olhar o futuro."""
    signals = list(stats.get("signals", []))
    if not signals or lookback_days is None:
        return stats
    last_day = max(pd.Timestamp(signal["day"]) for signal in signals)
    cutoff = last_day - pd.Timedelta(days=lookback_days - 1)
    selected = [signal for signal in signals
                if pd.Timestamp(signal["day"]) >= cutoff]
    days = len({str(signal["day"]) for signal in selected})
    if stats.get("repeat_minutes") is not None:
        sliced = ceifa._stats_relative_available_stake(  # mesma regra de banca
            selected, days=days, stake_frac=config.CEIFA_STAKE_FRAC)
    else:
        sliced = ceifa._stats(selected, days=days)
    sliced.update({
        "repeat_minutes": stats.get("repeat_minutes"),
        "period_cutoff": cutoff.date().isoformat(),
        "filter_scope_note": "Filtros exibidos para todo o histórico disponível",
        "archive_kind": stats.get("archive_kind", "maximum"),
        "side": stats.get("side", "NAO"),
        "executable_snapshots": stats.get("executable_snapshots", 0),
    })
    for key in (
        "n_filtrado", "n_filtrado_spread", "n_filtrado_nowcast",
        "n_filtrado_plateau", "n_filtrado_100c", "n_filtrado_0c",
    ):
        sliced[key] = stats.get(key, 0)
    return sliced


def city_frame(stats: dict) -> pd.DataFrame:
    rows = []
    for icao, (count, wins) in stats.get("by_city", {}).items():
        station = config.STATIONS.get(icao)
        rows.append({
            "Cidade": station.city if station else icao,
            "ICAO": icao,
            "Parcelas": int(count),
            "Acertos": int(wins),
            "Erros": int(count - wins),
            "Assertividade": wins / count if count else 0.0,
        })
    return pd.DataFrame(rows).sort_values(
        ["Erros", "Parcelas"], ascending=[False, False], ignore_index=True)


def unique_contracts(stats: dict) -> tuple[int, int]:
    groups: dict[tuple, list] = {}
    for signal in stats.get("signals", []):
        key = (signal["icao"], str(signal["day"]), signal["faixa"])
        groups.setdefault(key, []).append(signal)
    losses = sum(not all(item["won"] for item in items)
                 for items in groups.values())
    return len(groups), losses


def signals_with_stakes(stats: dict) -> list[dict]:
    """Devolve as entradas com a stake efetiva, inclusive no modelo antigo 10%."""
    signals = list(stats.get("signals", []))
    if not signals or all("stake" in signal for signal in signals):
        return signals
    by_day: dict[str, list] = {}
    for signal in signals:
        by_day.setdefault(str(signal["day"]), []).append(signal)
    capital = 1.0
    executed = []
    for day in sorted(by_day):
        available, settled = capital, 0.0
        for signal in sorted(by_day[day], key=lambda item: item["ts"]):
            stake = ceifa.STAKE_FRAC * available
            available -= stake
            if signal["won"]:
                settled += stake / signal["price"]
            executed.append(dict(signal, stake=stake))
        capital = available + settled
    return executed


def risk_metrics(stats: dict) -> dict:
    signals = signals_with_stakes(stats)
    gross_wins = sum(signal["stake"] * (1.0 / signal["price"] - 1.0)
                     for signal in signals if signal["won"])
    gross_losses = sum(signal["stake"] for signal in signals
                       if not signal["won"])
    daily = stats.get("per_day", [])
    return {
        "gross_wins": gross_wins,
        "gross_losses": gross_losses,
        "profit_factor": (gross_wins / gross_losses
                          if gross_losses else float("inf")),
        "avg_daily": (sum(day["ret"] for day in daily) / len(daily)
                      if daily else 0.0),
        "worst_day": min((day["ret"] for day in daily), default=0.0),
        "best_day": max((day["ret"] for day in daily), default=0.0),
    }


def _local_tz(icao: str) -> ZoneInfo:
    station = config.STATIONS.get(icao)
    return ZoneInfo(station.timezone) if station else ZoneInfo("UTC")


def _observations(icao: str, day: str) -> list[tuple[dt.datetime, float]]:
    path = config.ROOT / "backtest_data" / icao / f"{day}.json.gz"
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        raw = json.load(handle).get("obs", [])
    timezone = _local_tz(icao)
    return [(dt.datetime.fromisoformat(str(when)).replace(tzinfo=timezone),
             float(temp)) for when, temp in raw]


def _display_temperature(icao: str, value_c: float | None) -> float | None:
    if value_c is None or pd.isna(value_c):
        return None
    station = config.STATIONS.get(icao)
    return value_c * 9.0 / 5.0 + 32.0 if station and station.unit == "F" else value_c


def _entry_forecast(icao: str, day: str, timestamp,
                    archive: Path = MAXIMUM_ARCHIVE) -> pd.Series | None:
    path = archive / "previsao" / icao / f"{day}.parquet"
    if path.exists():
        frame = pd.read_parquet(path)
    else:
        files = sorted((archive / "previsao").glob("*.parquet"))
        if not files:
            return None
        frame = pd.concat((pd.read_parquet(item) for item in files),
                          ignore_index=True)
        frame = frame[frame["icao"].astype(str) == icao]
    frame = frame[frame["dia"].astype(str) == str(day)].copy()
    frame["ts"] = pd.to_datetime(frame["ts_utc"], utc=True)
    eligible = frame[frame["ts"] <= pd.Timestamp(timestamp)]
    return eligible.sort_values("ts").iloc[-1] if not eligible.empty else None


def loss_details(stats: dict) -> pd.DataFrame:
    rows = []
    minimum = stats.get("archive_kind") == "minimum"
    side = stats.get("side", "NAO")
    archive = MINIMUM_ARCHIVE if minimum else MAXIMUM_ARCHIVE
    for signal in signals_with_stakes(stats):
        if signal["won"]:
            continue
        icao, day = signal["icao"], str(signal["day"])
        station = config.STATIONS.get(icao)
        timezone = _local_tz(icao)
        entry_utc = pd.Timestamp(signal["ts"])
        entry_local = entry_utc.tz_convert(timezone)
        forecast = _entry_forecast(icao, day, entry_utc, archive)
        observations = _observations(icao, day) if not minimum else []
        cutoff = entry_local.to_pydatetime()
        prior = [(when, temp) for when, temp in observations if when <= cutoff]
        final_max = max((temp for _, temp in observations), default=None)
        final_time = next((when for when, temp in observations
                           if temp == final_max), None)

        def fvalue(name: str):
            if forecast is None:
                return None
            value = forecast.get(name)
            return None if value is None or pd.isna(value) else float(value)

        shift = fvalue("nowcast_shift")
        used_offset = fvalue("nowcast_offset")
        if used_offset is None:
            used_offset = ceifa.reconstructed_observed_deviation(
                icao, entry_utc, shift)
        exact_offset = used_offset
        if shift is not None and prior:
            last_hour = prior[-1][0].hour
            weight = min(max((last_hour - 6) / 6.0, 0.25), 1.0)
            exact_offset = shift / (config.NOWCAST_DAMPING * weight)

        median = fvalue("mediana")
        p90 = fvalue("p90")
        ceiling = fvalue("teto_ens")
        observed = fvalue("obs_max")
        peak = int(forecast["pico_hora"]) if forecast is not None else None
        target = ceifa.target_temperature_c(icao, signal["faixa"])
        price_boundary = round(float(signal["price"]), 3) <= config.CEIFA_PRICE_MIN

        if side == "SIM":
            diagnosis = "O contrato SIM terminou em 0¢"
        elif minimum:
            diagnosis = ("A mínima resolveu exatamente na faixa vendida · "
                         "arquivo meteorológico detalhado ainda não capturado")
        elif exact_offset is not None and exact_offset >= config.CEIFA_OBS_DEVIATION_MIN \
                and (used_offset is None or used_offset < config.CEIFA_OBS_DEVIATION_MIN):
            diagnosis = "Desvio quente histórico ficou subestimado"
        elif final_time is not None and peak is not None \
                and final_time.hour >= (peak + 2) % 24:
            diagnosis = "Pico real ocorreu mais tarde que o previsto"
        else:
            diagnosis = "Pico curto atingiu uma faixa ainda plausível no ensemble"
        if price_boundary:
            diagnosis += " · entrada no limite de 95¢"

        unit = station.unit if station else "C"
        rows.append({
            "Chave": f"{icao} · {day} · {side} · {signal['faixa']}",
            "Cidade": station.city if station else icao,
            "ICAO": icao,
            "Dia": day,
            "Faixa": signal["faixa"],
            "Lado": side,
            "Entrada local": entry_local.strftime("%d/%m/%Y %H:%M"),
            "Entrada UTC": entry_utc.isoformat(),
            "Preço (¢)": float(signal["price"]) * 100,
            "Parcela (% banca inicial)": float(signal["stake"]) * 100,
            "Unidade": unit,
            "Observado na entrada": _display_temperature(icao, observed),
            "Máxima final": _display_temperature(icao, final_max),
            "Horário da máxima": final_time.strftime("%H:%M") if final_time else "—",
            "Mediana": _display_temperature(icao, median),
            "P90": _display_temperature(icao, p90),
            "Teto": _display_temperature(icao, ceiling),
            "Alvo base (°C)": target,
            "Spread (°C)": float(signal.get("spread") or 0.0),
            "Shift (°C)": shift,
            "Desvio usado (°C)": used_offset,
            "Desvio com hora METAR (°C)": exact_offset,
            "Pico previsto": f"{peak:02d}:00" if peak is not None else "—",
            "Diagnóstico": diagnosis,
        })
    return pd.DataFrame(rows)


def loss_days(losses: pd.DataFrame) -> list[str]:
    """Dias com perdas, em ordem da mais recente para a mais antiga."""
    if losses.empty or "Dia" not in losses:
        return []
    parsed = pd.to_datetime(losses["Dia"], errors="coerce")
    return sorted(
        {value.date().isoformat() for value in parsed.dropna()},
        reverse=True,
    )


def losses_on_day(losses: pd.DataFrame, day: str) -> pd.DataFrame:
    """Recorta os contratos perdidos para um único dia."""
    if losses.empty or "Dia" not in losses:
        return losses.iloc[0:0].copy()
    try:
        day_key = pd.Timestamp(day).date().isoformat()
    except (TypeError, ValueError):
        return losses.iloc[0:0].copy()
    normalized = pd.to_datetime(
        losses["Dia"], errors="coerce").dt.strftime("%Y-%m-%d")
    return losses.loc[normalized == day_key].copy()


def error_timeline(icao: str, day: str, faixa: str, entry_utc: str,
                   archive_kind: str = "maximum",
                   side: str = "NAO") -> dict:
    timezone = _local_tz(icao)
    observations = pd.DataFrame(
        _observations(icao, day) if archive_kind == "maximum" else [],
                                columns=["Horário", "Temperatura_C"])
    if not observations.empty:
        observations["Temperatura"] = observations["Temperatura_C"].map(
            lambda value: _display_temperature(icao, value))

    archive = MAXIMUM_ARCHIVE if archive_kind == "maximum" else MINIMUM_ARCHIVE
    market_path = archive / "mercado" / icao / f"{day}.parquet"
    market = pd.DataFrame()
    if market_path.exists():
        market = pd.read_parquet(market_path)
    elif archive_kind == "minimum":
        files = sorted((archive / "mercado").glob("*.parquet"))
        if files:
            market = pd.concat((pd.read_parquet(path) for path in files),
                               ignore_index=True)
    if not market.empty:
        market = market[(market["icao"].astype(str) == icao)
                        & (market["dia"].astype(str) == day)
                        & (market["faixa"].astype(str) == faixa)].copy()
        market["Horário"] = pd.to_datetime(
            market["ts_utc"], utc=True).dt.tz_convert(timezone)
        price_column = "preco_sim" if side == "SIM" else "preco_nao"
        market["Preço (¢)"] = market[price_column] * 100
    entry = pd.Timestamp(entry_utc).tz_convert(timezone)
    return {"observations": observations, "market": market, "entry": entry}


def data_freshness(archive_kind: str = "maximum") -> dict:
    archive = MAXIMUM_ARCHIVE if archive_kind == "maximum" else MINIMUM_ARCHIVE
    files = list((archive / "mercado").rglob("*.parquet"))
    if not files:
        return {"files": 0, "updated": None, "latest_day": None}
    latest = max(files, key=lambda path: path.stat().st_mtime)
    updated = dt.datetime.fromtimestamp(latest.stat().st_mtime).astimezone()
    days = [path.stem for path in files if len(path.stem) == 10]
    return {"files": len(files), "updated": updated,
            "latest_day": max(days) if days else None}
