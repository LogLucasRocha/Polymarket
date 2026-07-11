"""Correção de viés por modelo ("MOS caseiro"): compara as máximas previstas
no passado (Open-Meteo histórico) com as máximas observadas nos METARs (IEM)
e aprende o erro sistemático de cada modelo para o ponto da estação."""
from __future__ import annotations

import datetime as dt
import json
import math

from . import config, fetch
from .config import Station


def _compute(station: Station) -> dict:
    today = dt.datetime.now(station.tz).date()  # dia local da estação, não da máquina
    end = today - dt.timedelta(days=2)   # dá folga para o arquivo consolidar
    start = end - dt.timedelta(days=config.BIAS_LOOKBACK_DAYS)

    obs = fetch.fetch_metar_history(station, start, end)
    hist = fetch.fetch_historical_forecast(station, start, end)
    daily = hist.get("daily", {})
    dates = [dt.date.fromisoformat(d) for d in daily.get("time", [])]

    result: dict[str, dict] = {}
    for model, family in config.DET_MODELS.items():
        key = f"temperature_2m_max_{model}"
        values = daily.get(key)
        if values is None and len(config.DET_MODELS) == 1:
            values = daily.get("temperature_2m_max")
        if values is None:
            continue
        errors = [
            fc - obs[d]["tmax"]
            for d, fc in zip(dates, values)
            if fc is not None and d in obs
        ]
        if len(errors) < 15:
            continue
        n = len(errors)
        mean = sum(errors) / n
        var = sum((e - mean) ** 2 for e in errors) / max(n - 1, 1)
        result[family] = {
            "model": model,
            "bias": round(mean, 3),          # previsto - observado (subtrair da previsão)
            "resid_std": round(math.sqrt(var), 3),
            "n_days": n,
            "mae": round(sum(abs(e - mean) for e in errors) / n, 3),
        }
    return result


def get_bias(station: Station, force: bool = False) -> dict:
    """Viés por família de modelo, com cache diário em disco (um por estação).
    Retorna {family: {bias, resid_std, n_days, mae, model}}."""
    cache_file = station.bias_cache_file
    if not force and cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            computed = dt.datetime.fromisoformat(cached["computed_at"])
            age_h = (dt.datetime.now() - computed).total_seconds() / 3600
            if age_h < config.BIAS_CACHE_MAX_AGE_HOURS and cached.get("bias"):
                return cached["bias"]
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    bias = _compute(station)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {"computed_at": dt.datetime.now().isoformat(), "bias": bias},
            indent=2,
        ),
        encoding="utf-8",
    )
    return bias
