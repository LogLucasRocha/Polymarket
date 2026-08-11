"""Coleta de dados: METAR/TAF (aviationweather.gov), histórico de METAR (Iowa State)
e previsões determinísticas/ensemble/históricas (Open-Meteo)."""
from __future__ import annotations

import csv
import datetime as dt
import io
import re
import time

import requests

from . import config
from .config import Station

UTC = dt.timezone.utc

_session = requests.Session()
_session.headers.update({"User-Agent": config.USER_AGENT})


_RETRY_WAITS = (2, 5, 10)  # segundos entre tentativas


def _get(url: str, params: dict | None = None, timeout: int = 60):
    """GET com retry para falhas transitórias (5xx, 429, timeout, conexão)."""
    for wait in (*_RETRY_WAITS, None):
        try:
            r = _session.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            if wait is None:
                raise
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if wait is None or (status < 500 and status != 429):
                raise
        time.sleep(wait)


# ---------------------------------------------------------------- METAR / TAF

def fetch_metars(station: Station, hours: int = 48) -> list[dict]:
    """METARs/SPECIs recentes da estação, decodificados, ordenados do mais antigo
    ao mais recente. Cada item: {time (datetime local), temp, dewp, wdir, wspd, raw}."""
    r = _get(
        "https://aviationweather.gov/api/data/metar",
        params={"ids": station.icao, "format": "json", "hours": hours},
    )
    out = []
    for ob in r.json():
        t = ob.get("obsTime")
        if t is not None:
            when = dt.datetime.fromtimestamp(int(t), tz=UTC)
        else:
            rt = ob.get("reportTime")
            if not rt:
                continue
            when = dt.datetime.fromisoformat(rt.replace("Z", "+00:00"))
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
        temp = ob.get("temp")
        if temp is None:
            continue
        out.append(
            {
                "time": when.astimezone(station.tz),
                "temp": float(temp),
                "dewp": ob.get("dewp"),
                "wdir": ob.get("wdir"),
                "wspd": ob.get("wspd"),
                "raw": ob.get("rawOb", ""),
            }
        )
    out.sort(key=lambda x: x["time"])
    return out


def fetch_taf_data(station: Station) -> dict:
    """TAF mais recente com o texto bruto e os períodos decodificados."""
    try:
        r = _get(
            "https://aviationweather.gov/api/data/taf",
            params={"ids": station.icao, "format": "json"},
        )
        payload = r.json()
        item = payload[0] if isinstance(payload, list) and payload else {}
        return {
            "raw": item.get("rawTAF") or None,
            "forecasts": item.get("fcsts") or [],
        }
    except Exception:
        return {"raw": None, "forecasts": []}


def fetch_taf(station: Station) -> str | None:
    """TAF mais recente da estação (texto bruto)."""
    return fetch_taf_data(station)["raw"]


def minimum_convection_windows(forecasts: list[dict], now: dt.datetime,
                               station: Station) -> list[dict]:
    """Períodos futuros de TSRA/VCTS até o fim do dia local da estação.

    O AWC fornece ``timeFrom``/``timeTo`` em epoch UTC. O filtro ignora
    trovoadas que já terminaram e previsões que só começam no dia seguinte.
    ``CB`` isolado não entra nesta regra: a decisão operacional é bloquear
    especificamente TSRA e VCTS.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=station.tz)
    local_now = now.astimezone(station.tz)
    local_end = dt.datetime.combine(
        local_now.date() + dt.timedelta(days=1), dt.time.min,
        tzinfo=station.tz)
    start_epoch = local_now.timestamp()
    end_epoch = local_end.timestamp()
    windows = []
    for forecast in forecasts or []:
        try:
            time_from = float(forecast.get("timeFrom"))
            time_to = float(forecast.get("timeTo"))
        except (TypeError, ValueError):
            continue
        if time_to <= start_epoch or time_from >= end_epoch:
            continue
        weather = str(forecast.get("wxString") or "").upper()
        tokens = [token.lstrip("+-") for token in weather.split()]
        codes = []
        if any("TSRA" in token for token in tokens):
            codes.append("TSRA")
        if "VCTS" in tokens:
            codes.append("VCTS")
        if not codes:
            continue
        windows.append({
            "from_utc": dt.datetime.fromtimestamp(
                time_from, tz=UTC).isoformat(),
            "to_utc": dt.datetime.fromtimestamp(
                time_to, tz=UTC).isoformat(),
            "codes": codes,
            "change": forecast.get("fcstChange"),
            "probability": forecast.get("probability"),
        })
    return windows


_TX_RE = re.compile(r"TX(M?)(\d{2})/(\d{2})(\d{2})Z")


def parse_taf_tx(taf: str, ref: dt.datetime, station: Station) -> list[dict]:
    """Extrai os grupos TX (máxima prevista pelo meteorologista) do TAF.
    Retorna [{'temp': int, 'local_date': date, 'valid_local': datetime}]."""
    results = []
    for sign, temp, day, hour in _TX_RE.findall(taf):
        t = int(temp) * (-1 if sign == "M" else 1)
        # dia/hora em UTC; resolve para o mês corrente ou seguinte
        base = ref.astimezone(UTC)
        for month_shift in (0, 1):
            year, month = base.year, base.month + month_shift
            if month > 12:
                year, month = year + 1, 1
            try:
                valid = dt.datetime(year, month, int(day), int(hour) % 24, tzinfo=UTC)
            except ValueError:
                continue
            if abs((valid - base).total_seconds()) < 3 * 86400:
                local = valid.astimezone(station.tz)
                results.append({"temp": t, "local_date": local.date(), "valid_local": local})
                break
    return results


# ------------------------------------------------- Histórico de METAR (IEM)

def fetch_metar_history(station: Station, start: dt.date, end: dt.date) -> dict[dt.date, dict]:
    """Máxima observada por dia local a partir do arquivo da Iowa State.
    Retorna {date: {'tmax': float, 'n_obs': int}} apenas para dias com
    cobertura suficiente de observações."""
    r = _get(
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py",
        params={
            "station": station.icao,
            "data": "tmpc",
            "year1": start.year, "month1": start.month, "day1": start.day,
            "year2": end.year, "month2": end.month, "day2": end.day,
            "tz": station.timezone,
            "format": "onlycomma",
            "latlon": "no",
            "missing": "M",
            "trace": "T",
            "report_type": ["3", "4"],
        },
        timeout=120,
    )
    days: dict[dt.date, list[float]] = {}
    reader = csv.DictReader(io.StringIO(r.text))
    for row in reader:
        val = row.get("tmpc", "M").strip()
        if val in ("M", ""):
            continue
        try:
            temp = float(val)
            day = dt.datetime.strptime(row["valid"][:10], "%Y-%m-%d").date()
        except (ValueError, KeyError):
            continue
        days.setdefault(day, []).append(temp)
    return {
        d: {"tmax": max(temps), "n_obs": len(temps)}
        for d, temps in days.items()
        if len(temps) >= config.MIN_OBS_PER_DAY
    }


# ------------------------------------------------------------------ Open-Meteo

def fetch_deterministic(station: Station, forecast_days: int = 3) -> dict:
    """Previsão determinística multi-modelo (horária + máxima diária)."""
    r = _get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": "temperature_2m",
            "daily": "temperature_2m_max",
            "models": ",".join(config.DET_MODELS),
            "timezone": station.timezone,
            "forecast_days": forecast_days,
        },
    )
    return r.json()


def fetch_ensemble(station: Station, forecast_days: int = 3) -> dict:
    """Previsão horária de todos os membros dos ensembles configurados.
    Retorna {'time': [datetime local], 'members': {(model, member_id): [temps]}}."""
    r = _get(
        "https://ensemble-api.open-meteo.com/v1/ensemble",
        params={
            "latitude": station.lat,
            "longitude": station.lon,
            "hourly": "temperature_2m",
            "models": ",".join(config.ENS_MODELS),
            "timezone": station.timezone,
            "forecast_days": forecast_days,
        },
        timeout=120,
    )
    hourly = r.json()["hourly"]
    times = [dt.datetime.fromisoformat(t).replace(tzinfo=station.tz)
             for t in hourly["time"]]

    members: dict[tuple[str, str], list] = {}
    name_map = {**{m: m for m in config.ENS_MODELS}, **config.ENS_RESPONSE_ALIASES}
    model_names = sorted(name_map, key=len, reverse=True)
    for key, values in hourly.items():
        if key == "time" or not key.startswith("temperature_2m"):
            continue
        rest = key[len("temperature_2m"):]
        model = next((name_map[m] for m in model_names if m in rest), None)
        if model is None and len(config.ENS_MODELS) == 1:
            model = next(iter(config.ENS_MODELS))
        if model is None:
            continue
        m = re.search(r"member(\d+)", rest)
        member_id = m.group(1) if m else "00"  # controle
        members[(model, member_id)] = values
    return {"time": times, "members": members}


def fetch_historical_forecast(station: Station, start: dt.date, end: dt.date) -> dict:
    """Máximas diárias previstas no passado (para aprender o viés de cada modelo)."""
    r = _get(
        "https://historical-forecast-api.open-meteo.com/v1/forecast",
        params={
            "latitude": station.lat,
            "longitude": station.lon,
            "daily": "temperature_2m_max",
            "models": ",".join(config.DET_MODELS),
            "timezone": station.timezone,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        },
        timeout=120,
    )
    return r.json()
