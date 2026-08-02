"""Preenche TAF historico nos snapshots de temperatura minima.

Somente snapshots que poderiam gerar uma entrada na H-1 sao consultados. Para
evitar look-ahead, cada grupo horario usa o TAF mais recente que ja havia sido
emitido no instante da primeira oportunidade daquele grupo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import time
from bisect import bisect_right
from collections import defaultdict
from pathlib import Path

import pandas as pd
import requests

from tmax import ceifa, config, fetch

ARCHIVE = config.ROOT / "dados_low"
LIVE_ARCHIVE = config.ROOT / "dados_low_live"
CACHE_FILE = config.DATA_DIR / "taf_backfill_cache.json"
TAF_API = "https://aviationweather.gov/api/data/taf"


def _forecast_targets(market: pd.DataFrame, forecasts: pd.DataFrame):
    """Retorna consultas horarias e snapshots de previsao usados na H-1."""
    candidates = ceifa._execution_candidates(market, "NAO")
    candidates["hloc"] = candidates.groupby("icao")["ts"].transform(
        lambda series: series.dt.tz_convert(
            config.STATIONS[series.name].tz).dt.hour)

    by_forecast = {}
    for key, group in forecasts.groupby(["icao", "dia"]):
        ordered = group.sort_values("ts")
        by_forecast[key] = (
            [stamp.value for stamp in ordered["ts"]],
            [row for _, row in ordered.iterrows()],
        )

    query_at: dict[tuple[str, pd.Timestamp], pd.Timestamp] = {}
    targets: dict[tuple[str, pd.Timestamp], set[tuple[str, str, str]]] = (
        defaultdict(set))
    for row in candidates.itertuples(index=False):
        lookup = by_forecast.get((row.icao, row.dia))
        if lookup is None:
            continue
        stamps, rows = lookup
        index = bisect_right(stamps, row.ts.value) - 1
        if index < 0:
            continue
        forecast = rows[index]
        if pd.isna(forecast.get("pico_hora")):
            continue
        if int(row.hloc) != (int(forecast["pico_hora"]) - 1) % 24:
            continue
        hour = row.ts.floor("h")
        bucket = (row.icao, hour)
        query_at[bucket] = min(query_at.get(bucket, row.ts), row.ts)
        targets[bucket].add((
            str(forecast["icao"]), str(forecast["dia"]),
            str(forecast["ts_utc"]),
        ))
    return query_at, targets


def _load_cache(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _taf_asof(icao: str, timestamp: pd.Timestamp,
                session: requests.Session) -> dict | None:
    response = session.get(TAF_API, params={
        "ids": icao,
        "format": "json",
        "time": "issue",
        "date": timestamp.isoformat().replace("+00:00", "Z"),
    }, headers={"User-Agent": config.USER_AGENT}, timeout=30)
    response.raise_for_status()
    payload = response.json()
    return payload[0] if isinstance(payload, list) and payload else None


def collect_tafs(query_at: dict, cache_path: Path = CACHE_FILE,
                 delay: float = 0.65) -> tuple[dict, int]:
    """Busca um TAF por estacao/hora, reutilizando cache local persistente."""
    cache = _load_cache(cache_path)
    results = {}
    downloaded = 0
    with requests.Session() as session:
        for bucket, timestamp in sorted(query_at.items()):
            icao, hour = bucket
            key = f"{icao}|{hour.isoformat()}"
            if key not in cache:
                cache[key] = _taf_asof(icao, timestamp, session)
                downloaded += 1
                _save_cache(cache_path, cache)
                if delay:
                    time.sleep(delay)
            results[bucket] = cache[key]
    return results, downloaded


def build_updates(targets: dict, tafs: dict) -> dict:
    updates = {}
    for bucket, forecast_keys in targets.items():
        item = tafs.get(bucket)
        if not item:
            continue
        station = config.STATIONS[bucket[0]]
        for key in forecast_keys:
            row_time = pd.to_datetime(key[2], utc=True).to_pydatetime()
            windows = fetch.minimum_convection_windows(
                item.get("fcsts") or [], row_time, station)
            codes = sorted({code for window in windows
                            for code in window.get("codes", [])})
            updates[key] = {
                "taf_raw": item.get("rawTAF") or None,
                "taf_convective_blocked": bool(windows),
                "taf_convective_codes": ",".join(codes) or None,
                "taf_convective_windows": json.dumps(windows),
            }
    return updates


def apply_updates(paths: list[Path], updates: dict) -> tuple[int, int]:
    files_changed = rows_changed = 0
    columns = (
        "taf_raw", "taf_convective_blocked", "taf_convective_codes",
        "taf_convective_windows",
    )
    for path in paths:
        frame = pd.read_parquet(path)
        for column in columns:
            if column not in frame:
                frame[column] = None
        changed = 0
        for index, row in frame.iterrows():
            key = (str(row.get("icao")), str(row.get("dia")),
                   str(row.get("ts_utc")))
            values = updates.get(key)
            if values is None or not pd.isna(row.get("taf_convective_blocked")):
                continue
            for column, value in values.items():
                frame.at[index, column] = value
            changed += 1
        if not changed:
            continue
        temporary = path.with_suffix(".parquet.tmp")
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
        files_changed += 1
        rows_changed += changed
    return files_changed, rows_changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=0.65,
                        help="Intervalo entre consultas novas ao AWC.")
    args = parser.parse_args()

    market = ceifa._load("mercado", ARCHIVE)
    forecasts = ceifa._load("previsao", ARCHIVE)
    query_at, targets = _forecast_targets(market, forecasts)
    tafs, downloaded = collect_tafs(query_at, delay=max(args.delay, 0))
    updates = build_updates(targets, tafs)
    paths = sorted((ARCHIVE / "previsao").glob("*.parquet"))
    paths += sorted((LIVE_ARCHIVE / "previsao").glob("*/*.parquet"))
    files, rows = apply_updates(paths, updates)
    print(f"TAF retroativo: {downloaded} consulta(s) nova(s), "
          f"{rows} snapshot(s) preenchido(s) em {files} arquivo(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
