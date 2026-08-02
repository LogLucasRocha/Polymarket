"""Preparação dos dados exibidos no monitor local da estratégia Ceifa."""
from __future__ import annotations

import datetime as dt
import gzip
import io
import json
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path
from pathlib import PurePosixPath
from zipfile import BadZipFile, ZipFile
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from . import ceifa, config

MAXIMUM_ARCHIVE = config.ROOT / "dados"
MINIMUM_ARCHIVE = config.ROOT / "dados_low"
MAXIMUM_LIVE_ARCHIVE = config.ROOT / "dados_live"
MINIMUM_LIVE_ARCHIVE = config.ROOT / "dados_low_live"
LIVE_RELEASE_API = (
    "https://api.github.com/repos/LogLucasRocha/Polymarket/"
    "releases/tags/ceifa-live")


def _sync_committed_archives() -> dict:
    """Atualiza as partições diárias já consolidadas na branch principal."""
    def run(*args: str):
        return subprocess.run(
            ["git", *args], cwd=config.ROOT, capture_output=True, text=True,
            timeout=60, check=False)

    fetched = run("fetch", "origin", "--quiet")
    if fetched.returncode:
        return {"ok": False, "updated": False,
                "message": "Não consegui baixar o histórico do GitHub."}
    archives = ("dados", "dados_low")
    compared = run("diff", "--quiet", "origin/main", "--", *archives)
    if compared.returncode not in (0, 1):
        return {"ok": False, "updated": False,
                "message": "Não consegui comparar o histórico."}
    if compared.returncode == 0:
        return {"ok": True, "updated": False,
                "message": "Histórico diário já atualizado."}
    restored = run(
        "restore", "--source=origin/main", "--worktree", "--", *archives)
    if restored.returncode:
        return {"ok": False, "updated": False,
                "message": "Não consegui atualizar os arquivos de dados."}
    return {"ok": True, "updated": True,
            "message": "Histórico diário atualizado."}


def _write_live_frames(rows_by_archive: dict[tuple[str, str], list[dict]]) -> int:
    """Materializa o JSONL intradiário como Parquet separado do histórico."""
    build_root = config.DATA_DIR / "dashboard_live_build"
    shutil.rmtree(build_root, ignore_errors=True)
    roots = {
        "maximum": build_root / "dados_live",
        "minimum": build_root / "dados_low_live",
    }
    written = 0
    for (kind, base), rows in rows_by_archive.items():
        if not rows:
            continue
        frame = pd.DataFrame(rows)
        if frame.empty or "dia" not in frame:
            continue
        frame["snapshot_live"] = True
        per_icao = base in {"mercado", "previsao", "nowcast"}
        groups = (["icao", "dia"] if per_icao and "icao" in frame
                  else ["dia"])
        for key, group in frame.groupby(groups):
            values = key if isinstance(key, tuple) else (key,)
            day = str(values[-1])
            destination = roots[kind] / base
            if per_icao:
                destination /= str(values[0])
            destination.mkdir(parents=True, exist_ok=True)
            group.to_parquet(destination / f"{day}.parquet", index=False)
            written += len(group)

    if not written:
        raise ValueError("O pacote intradiário veio vazio.")
    for kind, destination in (
            ("maximum", MAXIMUM_LIVE_ARCHIVE),
            ("minimum", MINIMUM_LIVE_ARCHIVE)):
        candidate = roots[kind]
        if not candidate.exists():
            continue
        shutil.rmtree(destination, ignore_errors=True)
        shutil.move(str(candidate), str(destination))
    shutil.rmtree(build_root, ignore_errors=True)
    return written


def _sync_live_snapshot() -> dict:
    """Baixa o asset mutável que contém as rodadas do dia UTC corrente."""
    headers = {"User-Agent": config.USER_AGENT,
               "Accept": "application/vnd.github+json"}
    release = requests.get(LIVE_RELEASE_API, headers=headers, timeout=30)
    release.raise_for_status()
    assets = release.json().get("assets", [])
    asset = next((item for item in assets
                  if item.get("name") == "ceifa-live.zip"), None)
    if not asset:
        raise ValueError("Snapshot intradiário ainda não foi publicado.")
    url = asset["browser_download_url"]
    version = asset.get("updated_at") or asset.get("id")
    response = requests.get(
        f"{url}?v={version}", headers=headers, timeout=60)
    response.raise_for_status()

    rows_by_archive: dict[tuple[str, str], list[dict]] = defaultdict(list)
    with ZipFile(io.BytesIO(response.content)) as archive:
        for name in archive.namelist():
            path = PurePosixPath(name)
            if path.suffix != ".jsonl":
                continue
            parts = path.parts
            if parts[:3] == ("data", "capture", "buffer") and len(parts) >= 5:
                kind, base = "maximum", parts[3]
            elif parts[:1] == ("data_low",) and len(parts) == 2:
                kind, base = "minimum", path.stem
            else:
                continue
            text = archive.read(name).decode("utf-8")
            for line in text.splitlines():
                if line.strip():
                    rows_by_archive[(kind, base)].append(json.loads(line))
    written = _write_live_frames(rows_by_archive)
    captured = max(
        (str(row.get("ts_utc")) for rows in rows_by_archive.values()
         for row in rows if row.get("ts_utc")), default=None)
    return {"ok": True, "updated": True, "rows": written,
            "captured": captured,
            "message": f"Snapshot do dia atualizado ({written:,} linhas)."
                       .replace(",", ".")}


def sync_dashboard_data() -> dict:
    """Sincroniza o histórico consolidado e o pacote intradiário.

    O dashboard local contém código e atalhos próprios que podem estar
    modificados. Atualizar apenas ``dados/`` e ``dados_low/`` evita que essas
    alterações bloqueiem a chegada dos snapshots publicados pelo workflow.
    """
    try:
        committed = _sync_committed_archives()
    except (OSError, subprocess.SubprocessError):
        committed = {"ok": False, "updated": False,
                     "message": "Histórico diário indisponível."}
    try:
        live = _sync_live_snapshot()
    except (OSError, ValueError, BadZipFile, requests.RequestException):
        live = {"ok": False, "updated": False,
                "message": "Snapshot do dia ainda indisponível."}
    if not committed["ok"] and not live["ok"]:
        return {"ok": False, "updated": False,
                "message": f"{committed['message']} {live['message']}"}
    messages = [result["message"] for result in (live, committed)
                if result["ok"]]
    return {"ok": True,
            "updated": committed["updated"] or live["updated"],
            "message": " ".join(messages),
            "captured": live.get("captured")}


def run_strategy(single_band: bool = False) -> dict:
    """Executa exatamente o backtest usado no relatório diário."""
    stats = ceifa.simulate_repeated(
        icaos=set(config.STATIONS),
        interval_minutes=config.CEIFA_REPEAT_MINUTES,
        stake_frac=config.CEIFA_STAKE_FRAC,
        single_band=single_band,
    )
    stats["archive_kind"] = "maximum"
    stats["side"] = "NAO"
    return stats


def run_minimum_strategy(single_band: bool = False) -> dict:
    """Executa a Ceifa ativa de temperaturas mínimas.

    Usa parcelas de 1% do caixa livre a cada cinco minutos na H-1, sem os
    filtros meteorológicos das máximas.
    """
    stats = ceifa.simulate_repeated(
        icaos=set(config.STATIONS), archive=MINIMUM_ARCHIVE,
        warm_target_filter=False, uncertainty_filter=False,
        minimum_taf_filter=config.CEIFA_MINIMUM_TAF_FILTER,
        interval_minutes=config.CEIFA_REPEAT_MINUTES,
        stake_frac=config.CEIFA_STAKE_FRAC,
        single_band=single_band)
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


def combine_active_strategies(maximum: dict, minimum: dict) -> dict:
    """Recalcula máximas + mínimas como uma única banca compartilhada."""
    signals = [
        dict(signal, extreme="maximum", archive_kind="maximum")
        for signal in maximum.get("signals", [])
    ] + [
        dict(signal, extreme="minimum", archive_kind="minimum")
        for signal in minimum.get("signals", [])
    ]
    days = len({str(signal["day"]) for signal in signals})
    stats = ceifa._stats_relative_available_stake(
        signals, days=days, stake_frac=config.CEIFA_STAKE_FRAC)
    stats.update({
        "repeat_minutes": config.CEIFA_REPEAT_MINUTES,
        "archive_kind": "consolidated",
        "side": "NAO",
        "single_band": bool(maximum.get("single_band")
                              and minimum.get("single_band")),
        "active_components": {
            "maximum": maximum.get("n", 0),
            "minimum": minimum.get("n", 0),
        },
        "n_filtrado": (maximum.get("n_filtrado", 0)
                        + minimum.get("n_filtrado", 0)),
        "n_filtrado_spread": maximum.get("n_filtrado_spread", 0),
        "n_filtrado_nowcast": maximum.get("n_filtrado_nowcast", 0),
        "n_filtrado_plateau": maximum.get("n_filtrado_plateau", 0),
        "n_filtrado_ensemble_band": maximum.get(
            "n_filtrado_ensemble_band", 0),
        "n_filtrado_upper_tail": maximum.get(
            "n_filtrado_upper_tail", 0),
        "n_filtrado_taf": minimum.get("n_filtrado_taf", 0),
        "n_filtrado_faixa_unica": (
            maximum.get("n_filtrado_faixa_unica", 0)
            + minimum.get("n_filtrado_faixa_unica", 0)),
        "n_filtrado_100c": (maximum.get("n_filtrado_100c", 0)
                             + minimum.get("n_filtrado_100c", 0)),
        "n_filtrado_0c": (maximum.get("n_filtrado_0c", 0)
                           + minimum.get("n_filtrado_0c", 0)),
    })
    return stats


def single_band_scenario(maximum: dict, minimum: dict) -> dict:
    """Deriva a faixa única das entradas ativas, sem reler os snapshots."""
    scenarios = []
    for source in (maximum, minimum):
        signals, removed = ceifa._select_single_band_signals(
            list(source.get("signals", [])))
        scenario = ceifa._stats_relative_available_stake(
            signals, days=source.get("days", 0),
            stake_frac=config.CEIFA_STAKE_FRAC)
        scenario.update({
            "repeat_minutes": source.get("repeat_minutes"),
            "archive_kind": source.get("archive_kind"),
            "side": "NAO",
            "single_band": True,
            "n_filtrado_faixa_unica": removed,
        })
        for key in (
            "n_filtrado", "n_filtrado_spread", "n_filtrado_nowcast",
            "n_filtrado_plateau", "n_filtrado_ensemble_band",
            "n_filtrado_upper_tail",
            "n_filtrado_taf", "n_filtrado_100c",
            "n_filtrado_0c",
        ):
            scenario[key] = source.get(key, 0)
        scenarios.append(scenario)
    return combine_active_strategies(*scenarios)


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
        "active_components": stats.get("active_components"),
        "single_band": stats.get("single_band", False),
    })
    for key in (
        "n_filtrado", "n_filtrado_spread", "n_filtrado_nowcast",
        "n_filtrado_plateau", "n_filtrado_ensemble_band",
        "n_filtrado_upper_tail",
        "n_filtrado_taf", "n_filtrado_100c",
        "n_filtrado_0c", "n_filtrado_faixa_unica",
    ):
        sliced[key] = stats.get(key, 0)
    return sliced


def filter_frame(stats: dict) -> pd.DataFrame:
    """Descrição auditável dos filtros ativos e de suas contagens."""
    kind = stats.get("archive_kind", "maximum")
    spread_abs = f"{config.CEIFA_SPREAD_ABS:.1f}".replace(".", ",")
    spread_rel = f"{config.CEIFA_SPREAD_REL:.1f}".replace(".", ",")
    deviation = f"{config.CEIFA_OBS_DEVIATION_MIN:.1f}".replace(".", ",")
    margin = f"{config.CEIFA_TARGET_MARGIN:.1f}".replace(".", ",")
    rows = []
    if stats.get("single_band"):
        rows.append({
            "Estratégia": "Ambas" if kind == "consolidated" else (
                "Máxima" if kind == "maximum" else "Mínima"),
            "Filtro": "Faixa única por cidade",
            "Quando bloqueia": (
                "Na primeira rodada elegível, escolhe o NÃO com maior "
                "ask executável e mantém essa faixa travada durante todo "
                "o evento da cidade/data."),
            "Entradas bloqueadas": stats.get(
                "n_filtrado_faixa_unica", 0),
            "Observação": (
                "Evita exposição simultânea em faixas mutuamente "
                "exclusivas."),
        })
    if kind in {"maximum", "consolidated"}:
        rows.extend([
            {
                "Estratégia": "Máxima",
                "Filtro": "Ensemble largo",
                "Quando bloqueia": (
                    f"Teto − mediana ≥ {spread_abs} °C "
                    f"ou ≥ {spread_rel}× o spread normal "
                    "da cidade."),
                "Entradas bloqueadas": stats.get("n_filtrado_spread", 0),
                "Observação": "Filtro independente.",
            },
            {
                "Estratégia": "Máxima",
                "Filtro": "Desvio/nowcast quente",
                "Quando bloqueia": (
                    f"Desvio observado ou shift ≥ "
                    f"{deviation} °C e a faixa está entre mediana − "
                    f"{margin} °C e P90 + {margin} °C."),
                "Entradas bloqueadas": stats.get("n_filtrado_nowcast", 0),
                "Observação": "Inclui os casos identificados por platô.",
            },
            {
                "Estratégia": "Máxima",
                "Filtro": "Platô observado",
                "Quando bloqueia": (
                    f"A máxima observada fica parada por ≥ "
                    f"{config.CEIFA_PLATEAU_HOURS:.0f}h; o platô amplia o "
                    "limite inferior da faixa plausível."),
                "Entradas bloqueadas": stats.get("n_filtrado_plateau", 0),
                "Observação": "Subconjunto do desvio/nowcast; não somar novamente.",
            },
            {
                "Estratégia": "Máxima",
                "Filtro": "P90/teto dentro da faixa",
                "Quando bloqueia": (
                    "O P90 ou o membro mais quente do ensemble pertence ao "
                    "mesmo bucket de temperatura que o contrato NÃO. Ex.: "
                    "32°C cobre de 31,5°C até menos de 32,5°C."),
                "Entradas bloqueadas": stats.get(
                    "n_filtrado_ensemble_band", 0),
                "Observação": (
                    "Aplica a discretização correta também às faixas em "
                    "Fahrenheit."),
            },
            {
                "Estratégia": "Máxima",
                "Filtro": "Cauda superior perto do teto",
                "Quando bloqueia": (
                    "Em contratos 'X° ou mais', X fica no máximo "
                    f"{config.CEIFA_UPPER_TAIL_MARGIN:.2f} °C acima do "
                    "membro mais quente do ensemble."),
                "Entradas bloqueadas": stats.get(
                    "n_filtrado_upper_tail", 0),
                "Observação": (
                    "Protege contra um pico curto acima da cauda; não se "
                    "aplica a faixas exatas."),
            },
        ])
    if kind in {"minimum", "consolidated"}:
        rows.append({
            "Estratégia": "Mínima",
            "Filtro": "TAF convectivo",
            "Quando bloqueia": (
                "O TAF prevê TSRA ou VCTS entre a análise e o fim do dia "
                "local da cidade."),
            "Entradas bloqueadas": stats.get("n_filtrado_taf", 0),
            "Observação": (
                "CB, TEMPO ou PROB isolados não bloqueiam; contagem desde "
                "a ativação do filtro."),
        })
    return pd.DataFrame(rows)


def city_frame(stats: dict) -> pd.DataFrame:
    columns = [
        "Cidade", "ICAO", "Parcelas", "Acertos", "Erros", "Assertividade",
    ]
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
    return pd.DataFrame(rows, columns=columns).sort_values(
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
    live_archive = archive.with_name(f"{archive.name}_live")
    direct = [root / "previsao" / icao / f"{day}.parquet"
              for root in (archive, live_archive)]
    files = [path for path in direct if path.exists()]
    if not files:
        files = [path for root in (archive, live_archive)
                 for path in sorted((root / "previsao").glob("*.parquet"))]
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
    live_archive = archive.with_name(f"{archive.name}_live")
    direct = [root / "mercado" / icao / f"{day}.parquet"
              for root in (archive, live_archive)]
    files = [path for path in direct if path.exists()]
    if not files and archive_kind == "minimum":
        files = [path for root in (archive, live_archive)
                 for path in sorted((root / "mercado").glob("*.parquet"))]
    market = (pd.concat((pd.read_parquet(path) for path in files),
                        ignore_index=True) if files else pd.DataFrame())
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
    if archive_kind == "consolidated":
        snapshots = [data_freshness("maximum"), data_freshness("minimum")]
        updated_values = [item["updated"] for item in snapshots
                          if item["updated"] is not None]
        days = [item["latest_day"] for item in snapshots if item["latest_day"]]
        return {
            "files": sum(item["files"] for item in snapshots),
            "updated": max(updated_values) if updated_values else None,
            "latest_day": max(days) if days else None,
        }
    archive = MAXIMUM_ARCHIVE if archive_kind == "maximum" else MINIMUM_ARCHIVE
    live_archive = archive.with_name(f"{archive.name}_live")
    files = [path for root in (archive, live_archive)
             for path in (root / "mercado").rglob("*.parquet")]
    if not files:
        return {"files": 0, "updated": None, "latest_day": None}
    latest = max(files, key=lambda path: path.stat().st_mtime)
    updated = dt.datetime.fromtimestamp(latest.stat().st_mtime).astimezone()
    days = [path.stem for path in files if len(path.stem) == 10]
    return {"files": len(files), "updated": updated,
            "latest_day": max(days) if days else None}
